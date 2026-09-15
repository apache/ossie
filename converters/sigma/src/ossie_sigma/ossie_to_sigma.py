"""Apache Ossie (OssieDocument) -> Sigma data model spec (JSON)."""

from __future__ import annotations

import json
from typing import Any, Optional, Union
from uuid import NAMESPACE_URL, uuid5

from ossie import (
    OssieCustomExtension,
    OssieDataset,
    OssieDocument,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
    OssieVendor,
)

from ossie_sigma.converter_issues import ConverterError, ConverterIssue, ConverterIssueType, ConverterResult
from ossie_sigma.expression_utils import ansi_sql_text, infer_single_dataset_qualifier, sigma_dialect_text
from ossie_sigma.sigma_formula import sql_to_sigma_formula
from ossie_sigma.spec_keys import MODEL_LEVEL_SPEC_KEYS

# Objects a `SIGMA` custom_extensions vendor entry can be attached to.
_SigmaExtensionHost = Union[OssieDataset, OssieField, OssieMetric, OssieRelationship, OssieSemanticModel]

_ID_NAMESPACE = uuid5(NAMESPACE_URL, "ossie.apache.org/converters/sigma")

# The only value the data model spec endpoints currently accept.
_SCHEMA_VERSION = 1

# A Sigma column `format` is a *display* format, and the spec defines exactly two
# variants: `{"kind": "number", ...}` and `{"kind": "date", ...}`. Emitting any other
# `kind` produces a spec the data model API rejects, so the datatypes with no display
# format of their own (String, Boolean, Time, Opaque) deliberately map to nothing and
# the column is written without a `format` key at all — which is also what Sigma's own
# specs look like for those columns.
_DATATYPE_TO_FORMAT = {
    "Integer": "number",
    "Decimal": "number",
    "Float": "number",
    "Date": "date",
    "DateTime": "date",
    "DateTimeTz": "date",
}


def _stable_id(*parts: str) -> str:
    """Deterministic id for an object with no preserved native Sigma id.

    Only used for datasets/fields/relationships that originate purely in Ossie (no
    ``SIGMA`` custom_extensions carrying a native id) — anything previously
    round-tripped through Sigma keeps its real id instead, since Sigma ids are
    referenced by other objects (controls, other data models) that this converter
    cannot see or update.
    """
    return str(uuid5(_ID_NAMESPACE, "/".join(parts))).replace("-", "")


def _sigma_ext(item: _SigmaExtensionHost) -> Optional[dict[str, Any]]:
    for ext in item.custom_extensions or []:
        if ext.vendor_name == OssieVendor.SIGMA.value:
            try:
                data = json.loads(ext.data)
            except json.JSONDecodeError:
                return None
            # A hand-edited or foreign-tool-produced document could carry a SIGMA
            # custom_extension whose `data` is valid JSON but not an object (e.g. a
            # list or a bare string); treat that the same as no extension at all
            # rather than returning something callers' `.get(...)` calls don't expect.
            return data if isinstance(data, dict) else None
    return None


def _emit_name(name: Optional[str], ext: dict[str, Any]) -> bool:
    """Whether to write a `name` key back onto a Sigma metric/relationship.

    `name` is optional in Sigma, and an unnamed object was given its own id as the
    Ossie name on the way in. Re-emitting that would invent a name the model never
    had, so it is written back only when the original had one (`explicit_name`) or
    when the object did not come from Sigma at all (no vendor extension).
    """
    return bool(name) and (bool(ext.get("explicit_name")) or not ext)


def _resolve_formula(
    expression, dataset_alias: str, element_name: str, issues: list[ConverterIssue]
) -> Optional[str]:
    """Prefer the native Sigma formula text; otherwise best-effort translate ANSI SQL.

    Returns ``None`` when no valid Sigma formula can be produced. `formula` is a
    required property of every Sigma column and metric, and the data model API
    validates the whole document before applying any of it — so a placeholder or empty
    formula would not degrade one field, it would fail the entire create/update call.
    Callers therefore omit the column/metric instead.
    """
    native = sigma_dialect_text(expression)
    if native is not None:
        return native

    sql = ansi_sql_text(expression)
    if sql is not None:
        translated = sql_to_sigma_formula(sql, dataset_alias=dataset_alias)
        if translated is not None:
            return translated
        detail = f"ANSI SQL expression {sql!r} has no Sigma formula equivalent"
    else:
        dialects = ", ".join(d.dialect.value for d in expression.dialects) or "none"
        detail = f"no SIGMA or ANSI_SQL dialect expression to translate from (have: {dialects})"

    issues.append(
        ConverterIssue(
            ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
            element_name,
            f"{detail}; omitted from the Sigma spec rather than emitting a placeholder "
            "formula that would fail validation for the entire data model on upload.",
        )
    )
    return None


class OssieToSigmaConverter:
    """Converts an :class:`OssieDocument` into a Sigma data model spec (as plain JSON)."""

    def convert(self, document: OssieDocument) -> ConverterResult[dict[str, Any]]:
        issues: list[ConverterIssue] = []

        if not document.semantic_model:
            raise ConverterError(
                "OssieDocument.semantic_model is empty; there is no semantic model to convert "
                "into a Sigma data model spec."
            )

        if len(document.semantic_model) > 1:
            issues.append(
                ConverterIssue(
                    ConverterIssueType.EXTRA_MODEL_DROPPED,
                    "document",
                    "Sigma data models are single semantic models; only semantic_model[0] "
                    f"was converted, {len(document.semantic_model) - 1} additional model(s) were dropped.",
                )
            )
        model = document.semantic_model[0]
        model_ext = _sigma_ext(model) or {}

        spec: dict[str, Any] = {"kind": "data-model", "name": model.name}
        if model.description:
            spec["description"] = model.description
        # Restores every model-level spec key captured into custom_extensions on the
        # way in (see sigma_to_ossie._MODEL_LEVEL_SPEC_KEYS, the same tuple), not just
        # the identity/versioning keys, so createdAt/createdBy/updatedAt/updatedBy/
        # ownerId/url round-trip losslessly instead of being silently dropped.
        for key in MODEL_LEVEL_SPEC_KEYS:
            if key in model_ext:
                spec[key] = model_ext[key]
        # `schemaVersion` is required on create/update, so a document that never came
        # from Sigma still needs one; the spec currently defines a single version.
        spec.setdefault("schemaVersion", _SCHEMA_VERSION)
        spec.update(model_ext.get("native") or {})

        pages: dict[str, dict[str, Any]] = {}

        def _page(page_id: Optional[str], page_name: Optional[str]) -> dict[str, Any]:
            key = page_id or "page-default"
            if key not in pages:
                pages[key] = {"id": page_id or _stable_id("page", key), "name": page_name or "Page 1", "elements": []}
            return pages[key]

        dataset_names = {d.name for d in model.datasets}
        dataset_element_id: dict[str, str] = {}
        for dataset in model.datasets:
            ext = _sigma_ext(dataset) or {}
            dataset_element_id[dataset.name] = ext.get("id") or _stable_id("element", dataset.name)

        metrics_by_element: dict[str, list[OssieMetric]] = {}
        for metric in model.metrics or []:
            ext = _sigma_ext(metric) or {}
            element_id = ext.get("element_id")
            if element_id is None:
                sql = ansi_sql_text(metric.expression)
                owning_dataset = infer_single_dataset_qualifier(sql, dataset_names) if sql else None
                element_id = dataset_element_id.get(owning_dataset) if owning_dataset else None
                if element_id is None:
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.CROSS_DATASET_METRIC_DROPPED,
                            metric.name,
                            "Sigma metrics are scoped to a single element; this Ossie metric's "
                            "expression does not unambiguously reference exactly one dataset "
                            "(it may span datasets via a relationship, e.g. a ratio metric), so "
                            "it has no faithful Sigma representation and was dropped.",
                        )
                    )
                    continue
            metrics_by_element.setdefault(element_id, []).append(metric)

        relationships_by_element: dict[str, list[OssieRelationship]] = {}
        for rel in model.relationships or []:
            ext = _sigma_ext(rel) or {}
            element_id = ext.get("element_id") or dataset_element_id.get(rel.from_dataset, "")
            relationships_by_element.setdefault(element_id, []).append(rel)

        for dataset in model.datasets:
            element = self._build_element(
                dataset, dataset_element_id, metrics_by_element, relationships_by_element, issues
            )
            ext = _sigma_ext(dataset) or {}
            page = _page(ext.get("page_id"), ext.get("page_name"))
            page["elements"].append(element)

        for entry in model_ext.get("non_table_elements", []):
            page = _page(entry.get("page_id"), entry.get("page_name"))
            page["elements"].append(entry["element"])

        spec["pages"] = list(pages.values()) or [{"id": _stable_id("page", "default"), "name": "Page 1", "elements": []}]

        return ConverterResult(output=spec, issues=issues)

    def _build_element(
        self,
        dataset: OssieDataset,
        dataset_element_id: dict[str, str],
        metrics_by_element: dict[str, list[OssieMetric]],
        relationships_by_element: dict[str, list[OssieRelationship]],
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        ext = _sigma_ext(dataset) or {}
        element_id = dataset_element_id[dataset.name]

        # A round-tripped element carries its whole native `source` block, so every
        # source kind (`sql`, `join`, `union`, `data-model`, ...) is reproduced exactly.
        # Only a document that never came from Sigma has to synthesise one, and the
        # only kind derivable from a `database.schema.table` string is warehouse-table.
        source: dict[str, Any] = dict(ext.get("source") or {"kind": "warehouse-table"})
        if source.get("kind") == "warehouse-table":
            # `path` is re-derived rather than replayed, so an edit to the Ossie
            # document's `source` reaches Sigma instead of being silently overridden by
            # the preserved original. The other kinds have no such portable field.
            source["path"] = dataset.source.split(".")

        field_ids: dict[str, str] = {}
        columns = []
        for field in dataset.fields or []:
            field_ext = _sigma_ext(field) or {}
            col_id = field_ext.get("id") or _stable_id("column", dataset.name, field.name)
            column = self._build_column(dataset, field, col_id, field_ext, issues)
            if column is None:
                continue
            field_ids[field.name] = col_id
            columns.append(column)

        element: dict[str, Any] = {
            "id": element_id,
            "kind": "table",
            "name": dataset.name,
            "source": source,
            "columns": columns,
        }
        if dataset.description:
            element["description"] = dataset.description
        if "unique_keys_raw" in ext:
            # Preserved verbatim from the Sigma -> Ossie direction: it may contain
            # entries that never resolved to a modeled field (kept only in Ossie's
            # custom_extensions, not in primary_key) or raw warehouse column
            # references, neither of which `field_ids` can reconstruct.
            element["uniqueKeys"] = ext["unique_keys_raw"]
        elif dataset.primary_key:
            unique_keys = [field_ids[name] for name in dataset.primary_key if name in field_ids]
            if unique_keys:
                element["uniqueKeys"] = unique_keys
        element.update(ext.get("native") or {})

        metrics = [self._build_metric(m, dataset.name, issues) for m in metrics_by_element.get(element_id, [])]
        metrics = [m for m in metrics if m is not None]
        if metrics:
            element["metrics"] = metrics

        relationships = relationships_by_element.get(element_id, [])
        if relationships:
            element["relationships"] = [
                self._build_relationship(r, dataset.name, dataset_element_id, field_ids) for r in relationships
            ]

        return element

    def _build_column(
        self,
        dataset: OssieDataset,
        field: OssieField,
        col_id: str,
        field_ext: dict[str, Any],
        issues: list[ConverterIssue],
    ) -> Optional[dict[str, Any]]:
        formula = _resolve_formula(field.expression, dataset.name, f"{dataset.name}.{field.name}", issues)
        if formula is None:
            return None

        column: dict[str, Any] = {"id": col_id, "formula": formula}
        needs_name = f"/{field.name}]" not in formula and f"[{field.name}]" != formula
        if field.name and (needs_name or field_ext.get("explicit_name")):
            column["name"] = field.name
        if field.description:
            column["description"] = field.description

        # A preserved native format always wins: it carries display detail (formatString,
        # currencySymbol, ...) that the coarse datatype mapping cannot reconstruct.
        if "format" in field_ext:
            column["format"] = field_ext["format"]
        elif field.datatype in _DATATYPE_TO_FORMAT:
            column["format"] = {"kind": _DATATYPE_TO_FORMAT[field.datatype]}
        elif field.datatype == "Opaque":
            issues.append(
                ConverterIssue(
                    ConverterIssueType.OPAQUE_DATATYPE,
                    f"{dataset.name}.{field.name}",
                    "Field has an Opaque datatype with no preserved native Sigma format; "
                    "no format was emitted.",
                )
            )
        column.update(field_ext.get("native") or {})
        return column

    def _build_metric(
        self, metric: OssieMetric, dataset_name: str, issues: list[ConverterIssue]
    ) -> Optional[dict[str, Any]]:
        ext = _sigma_ext(metric) or {}
        formula = _resolve_formula(metric.expression, dataset_name, f"{dataset_name}.{metric.name}", issues)
        if formula is None:
            return None

        result: dict[str, Any] = {
            "id": ext.get("id") or _stable_id("metric", dataset_name, metric.name),
            "formula": formula,
        }
        if _emit_name(metric.name, ext):
            result["name"] = metric.name
        if metric.description:
            result["description"] = metric.description
        result.update(ext.get("native") or {})
        return result

    def _build_relationship(
        self,
        rel: OssieRelationship,
        dataset_name: str,
        dataset_element_id: dict[str, str],
        field_ids: dict[str, str],
    ) -> dict[str, Any]:
        ext = _sigma_ext(rel) or {}
        target_element_id = dataset_element_id.get(rel.to, rel.to)
        result: dict[str, Any] = {
            # Scoped by owning dataset, like column ids (dataset+field) and metric ids
            # (dataset+metric): unscoped by dataset, two unrelated relationships with
            # the same name on different table pairs would hash to the same id.
            "id": ext.get("id") or _stable_id("relationship", dataset_name, rel.name),
            "targetElementId": target_element_id,
        }
        if _emit_name(rel.name, ext):
            result["name"] = rel.name
        if ext.get("description"):
            result["description"] = ext["description"]

        raw_keys = ext.get("raw_keys")
        if raw_keys is not None:
            result["keys"] = raw_keys
        else:
            result["keys"] = [
                {
                    "sourceColumnId": field_ids.get(from_col, from_col),
                    "targetColumnId": field_ids.get(to_col, to_col),
                }
                for from_col, to_col in zip(rel.from_columns, rel.to_columns)
            ]
        result.update(ext.get("native") or {})
        return result
