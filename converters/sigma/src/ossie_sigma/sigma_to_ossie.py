"""Sigma data model spec (JSON) -> Apache Ossie (OssieDocument)."""

from __future__ import annotations

import json
from typing import Any, Optional

from ossie import (
    OssieCustomExtension,
    OssieDataset,
    OssieDialect,
    OssieDialectExpression,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
    OssieVendor,
)

from ossie_sigma.converter_issues import ConverterIssue, ConverterIssueType, ConverterResult
from ossie_sigma.expression_utils import build_expression, qualify
from ossie_sigma.sigma_formula import FormulaParseError, is_plain_column_ref, parse_formula, to_ansi_sql
from ossie_sigma.spec_keys import MODEL_LEVEL_SPEC_KEYS

_MODEL_LEVEL_SPEC_KEYS = MODEL_LEVEL_SPEC_KEYS

# Spec keys this converter maps onto a portable Ossie concept. Everything else on an
# element/column/metric/relationship is Sigma-native presentation or governance state
# (`filters`, `folders`, `order`, `sort`, `summary`, `groupings`, `columnSecurities`,
# `visibleAsSource`, `hidden`, `isHighlighted`, `timeline`, `relationshipType`, ...)
# and is preserved verbatim under a `native` key in `custom_extensions`. Capturing the
# residue by subtraction rather than by an allow-list means a future `schemaVersion`
# that adds a field still round-trips losslessly instead of silently dropping it.
_MAPPED_MODEL_KEYS = frozenset({"name", "description", "pages"})
_MAPPED_ELEMENT_KEYS = frozenset(
    {"id", "kind", "name", "description", "source", "columns", "metrics", "relationships", "uniqueKeys"}
)
_MAPPED_COLUMN_KEYS = frozenset({"id", "formula", "name", "description", "format"})
_MAPPED_METRIC_KEYS = frozenset({"id", "formula", "name", "description"})
_MAPPED_RELATIONSHIP_KEYS = frozenset({"id", "name", "description", "targetElementId", "keys"})

# Sigma column formats describe *display* formatting, not storage type — the data model
# spec has no datatype field at all — so only the two documented format kinds carry any
# type signal, and only coarsely. Anything else is `Opaque` with the format preserved.
_FORMAT_TO_DATATYPE = {
    "number": "Decimal",
    "date": "DateTime",
}


def _native_residue(obj: dict[str, Any], mapped: frozenset) -> dict[str, Any]:
    """Return the entries of *obj* that no portable Ossie concept covers."""
    return {k: v for k, v in obj.items() if k not in mapped}


def _derived_source_marker(source: dict[str, Any], element_id: str) -> str:
    """A readable ``OssieDataset.source`` stand-in for a non-warehouse-table source.

    The authoritative copy is the full native `source` block in `custom_extensions`;
    this only has to be human-readable and stable.
    """
    kind = source.get("kind") or "unknown"
    if kind == "sql":
        return f"sql:{source.get('connectionId', element_id)}"
    if kind == "data-model":
        return f"data-model:{source.get('dataModelId', '')}/{source.get('elementId', '')}"
    if kind in ("table", "join", "union"):
        return f"{kind}:{source.get('elementId', element_id)}"
    return f"{kind}:{element_id}"


def _vendor_ext(data: dict[str, Any]) -> OssieCustomExtension:
    return OssieCustomExtension(vendor_name=OssieVendor.SIGMA.value, data=json.dumps(data, sort_keys=True))


def _require_id(obj: dict[str, Any], kind: str, dataset_name: str, issues: list[ConverterIssue]) -> Optional[str]:
    """Return ``obj["id"]``, or ``None`` and record a :class:`ConverterIssue` if absent.

    Every Sigma element/column/metric/relationship requires an id for the Data Model
    API to accept the spec, so a spec missing one is already malformed; rather than
    inventing a synthetic id that other parts of the same spec could never actually
    reference, the object is dropped from the conversion.
    """
    obj_id = obj.get("id")
    if obj_id:
        return obj_id
    issues.append(
        ConverterIssue(
            ConverterIssueType.MISSING_ID,
            f"{dataset_name}.{obj.get('name')}" if obj.get("name") else dataset_name,
            f"Sigma {kind} has no id; every Sigma {kind} requires one for the Data Model API "
            "to accept the spec, so it was dropped from the conversion.",
        )
    )
    return None


def _column_display_name(column: dict[str, Any]) -> str:
    """The Ossie field name for a Sigma column: its explicit `name`, else derived from formula."""
    if column.get("name"):
        return column["name"]
    formula = column.get("formula", "")
    ref = is_plain_column_ref(formula)
    if ref is not None:
        return ref.column
    return column["id"]


def _folder_for_column(element: dict[str, Any], column_id: str) -> Optional[dict[str, Any]]:
    for folder in element.get("folders") or []:
        if column_id in (folder.get("items") or []):
            return folder
    return None


class _ElementIndex:
    """Resolves a Sigma relationship key (model column id, or a raw
    ``inode-<file>/<PHYSICAL_COLUMN>`` warehouse-column reference) to the Ossie
    field name of a table element."""

    def __init__(self, element: dict[str, Any]) -> None:
        self.element = element
        # Columns with no id are dropped elsewhere (in the main convert() loop, which
        # emits the MISSING_ID issue); silently excluding them here too just keeps
        # this index consistent with what actually gets modeled as an Ossie field.
        self.columns_by_id: dict[str, dict[str, Any]] = {
            c["id"]: c for c in element.get("columns") or [] if c.get("id")
        }
        # A column formula may self-qualify with this element's own physical table
        # name (e.g. a column of the `EVENTS` warehouse table written as
        # `[EVENTS/ORG_ID]` rather than `[ORG_ID]`) — that is still a plain physical
        # column of *this* element, unlike a genuine cross-table reference such as
        # `[OtherTable/Y]`, which must not be indexed here at all.
        own_table_names = {element.get("name")}
        path = (element.get("source") or {}).get("path")
        if path:
            own_table_names.add(path[-1])
        own_table_names.discard(None)

        self.physical_by_upper: dict[str, str] = {}
        # Ambiguous keys (case-insensitive collisions) are tracked separately rather
        # than resolved to whichever column happened to be seen last, so a lookup
        # can correctly report itself as unresolved instead of silently guessing.
        self._ambiguous_upper: set[str] = set()
        for column in element.get("columns") or []:
            ref = is_plain_column_ref(column.get("formula", ""))
            if ref is not None and (ref.table is None or ref.table in own_table_names):
                key = ref.column.upper()
                display_name = _column_display_name(column)
                if key in self.physical_by_upper and self.physical_by_upper[key] != display_name:
                    self._ambiguous_upper.add(key)
                self.physical_by_upper[key] = display_name

    def resolve(self, column_ref_id: str) -> tuple[str, bool]:
        """Return ``(ossie_field_name, resolved)``."""
        if column_ref_id in self.columns_by_id:
            return _column_display_name(self.columns_by_id[column_ref_id]), True
        if column_ref_id.startswith("inode-"):
            physical_name = column_ref_id.rsplit("/", 1)[-1]
            key = physical_name.upper()
            if key in self._ambiguous_upper:
                return physical_name, False
            resolved = self.physical_by_upper.get(key)
            if resolved is not None:
                return resolved, True
            return physical_name, False
        return column_ref_id, False


class SigmaToOssieConverter:
    """Converts a Sigma data model spec (as parsed JSON) into an :class:`OssieDocument`."""

    def convert(self, spec: dict[str, Any]) -> ConverterResult[OssieDocument]:
        issues: list[ConverterIssue] = []

        elements: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (page, element)
        for page in spec.get("pages") or []:
            for element in page.get("elements") or []:
                elements.append((page, element))

        all_table_elements = [(p, e) for p, e in elements if e.get("kind") == "table"]
        other_elements = [(p, e) for p, e in elements if e.get("kind") != "table"]

        # Elements with no id are dropped up front: every id lookup below
        # (element_by_id, index_by_id, relationship targets) assumes it can key on
        # e["id"], so an id-less element must not reach any of them.
        table_elements = []
        for p, e in all_table_elements:
            if _require_id(e, "table element", e.get("name") or "<unnamed>", issues) is not None:
                table_elements.append((p, e))

        element_by_id = {e["id"]: e for _, e in table_elements}
        index_by_id = {e["id"]: _ElementIndex(e) for _, e in table_elements}

        datasets: list[OssieDataset] = []
        relationships: list[OssieRelationship] = []
        metrics: list[OssieMetric] = []

        for page, element in table_elements:
            dataset_name = element.get("name", element["id"])
            source = element.get("source") or {}

            source_kind = source.get("kind")
            if source_kind == "warehouse-table":
                source_str = ".".join(source.get("path") or [])
            else:
                # `sql`, `table`, `data-model`, `join` and `union` sources have no
                # `database.schema.table` location to put in OssieDataset.source.
                source_str = _derived_source_marker(source, element["id"])
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.DERIVED_ELEMENT_NOT_MODELED,
                        dataset_name,
                        f"Element source kind {source_kind!r} is not a warehouse table; Ossie's "
                        "OssieDataset.source is a physical location string and Ossie has no "
                        "first-class 'derived dataset' concept, so the full native source block "
                        "is carried in custom_extensions and `source` holds a marker only.",
                    )
                )

            fields: list[OssieField] = []
            for column in element.get("columns") or []:
                column_id = _require_id(column, "column", dataset_name, issues)
                if column_id is None:
                    continue
                formula = column.get("formula", "")
                field_name = _column_display_name(column)
                expression = build_expression(formula, dataset_alias=dataset_name)
                if not any(d.dialect == OssieDialect.ANSI_SQL for d in expression.dialects):
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
                            f"{dataset_name}.{field_name}",
                            f"Formula {formula!r} has no ANSI SQL equivalent; preserved as SIGMA-dialect text only.",
                        )
                    )

                datatype = None
                fmt = column.get("format") or {}
                fmt_kind = fmt.get("kind")
                ext_data: dict[str, Any] = {"id": column_id}
                if column.get("name"):
                    ext_data["explicit_name"] = True
                folder = _folder_for_column(element, column_id)
                if folder is not None:
                    ext_data["folder_id"] = folder["id"]
                if fmt:
                    # The format object carries display detail (formatString, prefix,
                    # currencySymbol, ...) that no Ossie field models, so it is always
                    # preserved whole, even when `kind` did yield a datatype.
                    ext_data["format"] = fmt
                    datatype = _FORMAT_TO_DATATYPE.get(fmt_kind)
                    if datatype is None:
                        datatype = "Opaque"
                        issues.append(
                            ConverterIssue(
                                ConverterIssueType.OPAQUE_DATATYPE,
                                f"{dataset_name}.{field_name}",
                                f"Sigma column format {fmt_kind!r} has no portable Ossie datatype.",
                            )
                        )
                native = _native_residue(column, _MAPPED_COLUMN_KEYS)
                if native:
                    ext_data["native"] = native

                fields.append(
                    OssieField(
                        name=field_name,
                        expression=expression,
                        description=column.get("description"),
                        datatype=datatype,
                        custom_extensions=[_vendor_ext(ext_data)],
                    )
                )

            dataset_ext: dict[str, Any] = {
                "id": element["id"],
                "page_id": page.get("id"),
                "page_name": page.get("name"),
            }
            if source:
                dataset_ext["source"] = source
            native = _native_residue(element, _MAPPED_ELEMENT_KEYS)
            if native:
                dataset_ext["native"] = native
            if element.get("filters"):
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.FILTER_NOT_MODELED,
                        dataset_name,
                        "Sigma element filters (`number-range`, `date-range`, `top-n`, `list`, "
                        "`text-match`, `hierarchy`) restrict which rows an element shows; Ossie "
                        "models the shape of a dataset, not a saved row restriction on it, so "
                        "they are preserved verbatim in custom_extensions only.",
                    )
                )

            index = index_by_id[element["id"]]
            raw_unique_keys = element.get("uniqueKeys") or []
            unique_keys: list[str] = []
            for key in raw_unique_keys:
                name, resolved = index.resolve(key)
                if not resolved:
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.UNIQUE_KEY_COLUMN_UNRESOLVED,
                            dataset_name,
                            f"Could not resolve uniqueKeys entry {key!r} to a modeled Ossie field "
                            "name; dropped from Ossie's primary_key, though the raw Sigma "
                            "uniqueKeys list is preserved in custom_extensions for exact "
                            "round-trip reconstruction.",
                        )
                    )
                    continue
                unique_keys.append(name)
            if raw_unique_keys:
                # Preserved verbatim (including any entries that failed to resolve
                # above, and regardless of whether a resolved entry was a raw
                # warehouse column reference or a modeled column id) so Ossie -> Sigma
                # can restore the exact original list rather than re-deriving it from
                # the internal column ids that `unique_keys` resolves to.
                dataset_ext["unique_keys_raw"] = raw_unique_keys

            datasets.append(
                OssieDataset(
                    name=dataset_name,
                    source=source_str,
                    primary_key=unique_keys or None,
                    description=element.get("description"),
                    fields=fields or None,
                    custom_extensions=[_vendor_ext(dataset_ext)],
                )
            )

            for metric in element.get("metrics") or []:
                metric_id = _require_id(metric, "metric", dataset_name, issues)
                if metric_id is None:
                    continue
                formula = metric.get("formula", "")
                metric_name = metric.get("name") or metric_id
                try:
                    node = qualify(parse_formula(formula), dataset_name)
                    sql = to_ansi_sql(node, dataset_alias=None)
                except FormulaParseError:
                    sql = None

                dialect_exprs = [OssieDialectExpression(dialect=OssieDialect.SIGMA, expression=formula)]
                if sql is not None:
                    dialect_exprs.append(OssieDialectExpression(dialect=OssieDialect.ANSI_SQL, expression=sql))
                else:
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
                            f"{dataset_name}.{metric_name}",
                            f"Metric formula {formula!r} has no ANSI SQL equivalent.",
                        )
                    )

                metric_ext: dict[str, Any] = {"id": metric_id, "element_id": element["id"]}
                if metric.get("name"):
                    metric_ext["explicit_name"] = True
                native = _native_residue(metric, _MAPPED_METRIC_KEYS)
                if native:
                    metric_ext["native"] = native

                metrics.append(
                    OssieMetric(
                        name=metric_name,
                        expression=OssieExpression(dialects=dialect_exprs),
                        description=metric.get("description"),
                        custom_extensions=[_vendor_ext(metric_ext)],
                    )
                )

            for rel in element.get("relationships") or []:
                rel_id = _require_id(rel, "relationship", dataset_name, issues)
                if rel_id is None:
                    continue
                target_id = rel.get("targetElementId")
                target_element = element_by_id.get(target_id)
                target_name = target_element.get("name", target_id) if target_element else target_id
                from_index = index_by_id[element["id"]]
                to_index = index_by_id.get(target_id)

                from_columns: list[str] = []
                to_columns: list[str] = []
                for key in rel.get("keys") or []:
                    from_col, from_resolved = from_index.resolve(key["sourceColumnId"])
                    if to_index is not None:
                        to_col, to_resolved = to_index.resolve(key["targetColumnId"])
                    else:
                        to_col, to_resolved = key["targetColumnId"], False
                    from_columns.append(from_col)
                    to_columns.append(to_col)
                    if not (from_resolved and to_resolved):
                        issues.append(
                            ConverterIssue(
                                ConverterIssueType.RELATIONSHIP_COLUMN_UNRESOLVED,
                                rel.get("name") or rel_id,
                                "Could not resolve one or both join key columns to a modeled Ossie "
                                "field name; the raw Sigma column reference is preserved in "
                                "custom_extensions for exact round-trip reconstruction.",
                            )
                        )

                rel_ext: dict[str, Any] = {
                    "id": rel_id,
                    "element_id": element["id"],
                    "raw_keys": rel.get("keys"),
                }
                if rel.get("name"):
                    rel_ext["explicit_name"] = True
                if rel.get("description"):
                    rel_ext["description"] = rel["description"]
                native = _native_residue(rel, _MAPPED_RELATIONSHIP_KEYS)
                if native:
                    rel_ext["native"] = native

                relationships.append(
                    OssieRelationship(
                        name=rel.get("name") or rel_id,
                        **{"from": dataset_name},
                        to=target_name,
                        from_columns=from_columns,
                        to_columns=to_columns,
                        custom_extensions=[_vendor_ext(rel_ext)],
                    )
                )

        model_ext: dict[str, Any] = {k: spec[k] for k in _MODEL_LEVEL_SPEC_KEYS if k in spec}
        native = _native_residue(spec, _MAPPED_MODEL_KEYS | frozenset(_MODEL_LEVEL_SPEC_KEYS))
        if native:
            model_ext["native"] = native
        if other_elements:
            model_ext["non_table_elements"] = [
                {"page_id": page.get("id"), "page_name": page.get("name"), "element": element}
                for page, element in other_elements
            ]
            for _, element in other_elements:
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.UNSUPPORTED_ELEMENT_KIND,
                        element.get("name") or element.get("id") or "<unnamed>",
                        f"Sigma element kind {element.get('kind')!r} is not a table and has no "
                        "equivalent in the Ossie semantic model; preserved verbatim in "
                        "custom_extensions only. See LIMITATIONS.md.",
                    )
                )

        semantic_model = OssieSemanticModel(
            name=spec.get("name", "sigma_data_model"),
            description=spec.get("description"),
            datasets=datasets,
            relationships=relationships or None,
            metrics=metrics or None,
            custom_extensions=[_vendor_ext(model_ext)] if model_ext else None,
        )

        document = OssieDocument(
            dialects=[OssieDialect.ANSI_SQL, OssieDialect.SIGMA],
            vendors=[OssieVendor.SIGMA],
            semantic_model=[semantic_model],
        )
        return ConverterResult(output=document, issues=issues)
