# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Convert an Ossie semantic model into a SAP CSN document (Core Schema Notation, JSON).

Mapping:

* a dataset becomes an ``entity``; its fields become elements, and primary-key
  fields carry ``key: true``;
* a field whose expression is not just its own column becomes a calculated element
  (``value``), with the expression translated from SQL to CXN;
* a relationship becomes an association on the ``from`` entity: managed (``keys``)
  when the foreign-key columns are the ones CDS would generate, otherwise with an
  ``on`` condition;
* a metric becomes ``@Aggregation.default`` on the element it aggregates, or a
  derived measure element when it aggregates an expression of one dataset;
* a metric CSN cannot express (several datasets, ``COUNT(*)``, a ratio, ...) goes
  into ``meta.ossie.metrics``, which this converter reads back and other CSN
  consumers ignore.

Ossie content with no CSN equivalent (AI context, other vendors' extensions)
rides in ``@Ossie.*`` annotations, which CSN permits and consumers ignore.
"""

import re
from typing import Any, Optional

from ossie import OssieDataset, OssieDocument, OssieField, OssieMetric, OssieRelationship
from sqlglot import exp

from ossie_sap_csn import _common as c
from ossie_sap_csn.converter_issues import (
    ConverterError,
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)
from ossie_sap_csn.cxn import UnsupportedExpression, parse_sql, sql_to_cxn
from ossie_sap_csn.datatypes import builtin_type, cds_type, ossie_datatype

_IDENTIFIER_UNSAFE = re.compile(r"[^A-Za-z0-9_]")

_AGG_NODES: dict[type, str] = {exp.Sum: "SUM", exp.Min: "MIN", exp.Max: "MAX", exp.Avg: "AVG", exp.Count: "COUNT"}


def _select_sql(expression: Any) -> tuple[Optional[str], Optional[str]]:
    """The ANSI_SQL expression text, and the first other dialect's text when ANSI_SQL is absent."""
    other = None
    for entry in expression.dialects:
        if entry.dialect.value == "ANSI_SQL":
            return entry.expression, None
        other = other or f"{entry.dialect.value}:{entry.expression}"
    return None, other


class _Metric:
    """A metric of the shape ``AGG(expr)``, split into its parts."""

    def __init__(self, aggregation: str, inner: exp.Expression) -> None:
        self.aggregation = aggregation
        self.inner = inner


def _parse_aggregate(sql: str) -> Optional[_Metric]:
    try:
        tree = parse_sql(sql)
    except UnsupportedExpression:
        return None
    if isinstance(tree, exp.Count) and isinstance(tree.this, exp.Distinct) and len(tree.this.expressions) == 1:
        return _Metric("COUNT_DISTINCT", tree.this.expressions[0])
    for node_type, name in _AGG_NODES.items():
        if type(tree) is node_type:
            inner = tree.this
            if inner is None or isinstance(inner, (exp.Star, exp.Distinct)):
                return None
            return _Metric(name, inner)
    return None


def _identifier(name: str) -> str:
    return _IDENTIFIER_UNSAFE.sub("_", name)


class OssieToCsnConverter:
    """Converts an :class:`OssieDocument` into a CSN document (as a JSON-ready dict)."""

    def convert(self, document: OssieDocument) -> ConverterResult[dict[str, Any]]:
        issues: list[ConverterIssue] = []
        model_ext = c.read_vendor_extension(document.custom_extensions, f"model {document.name!r}")

        datasets = {d.name: d for d in document.datasets}
        if len(datasets) != len(document.datasets):
            raise ConverterError("the document declares two datasets with the same name")
        dataset_ext = {n: c.read_vendor_extension(d.custom_extensions, f"dataset {n!r}") for n, d in datasets.items()}
        entity_name = {n: dataset_ext[n].get("entity") or n for n in datasets}
        if len(set(entity_name.values())) != len(entity_name):
            raise ConverterError("two datasets map to the same CSN entity name")

        # Relationships first: they decide which foreign-key fields CDS generates, and
        # what a path through an association is called in a calculated expression.
        plans: dict[str, list[dict[str, Any]]] = {n: [] for n in datasets}
        taken_assoc: dict[str, set[str]] = {n: {f.name for f in (d.fields or [])} for n, d in datasets.items()}
        for rel in document.relationships or []:
            plan = self._plan_relationship(rel, datasets, taken_assoc, entity_name, issues)
            if plan is not None:
                plans[rel.from_dataset].append(plan)
        generated_fks = {
            n: {col for plan in plans[n] if plan["managed"] for col in plan["from_columns"]} for n in datasets
        }

        definitions: dict[str, Any] = {}
        metric_homes = self._place_metrics(document, datasets, dataset_ext, entity_name, plans, issues)

        for ds_name, dataset in datasets.items():
            definitions[entity_name[ds_name]] = self._entity(
                dataset, dataset_ext[ds_name], plans[ds_name], generated_fks[ds_name],
                metric_homes, datasets, entity_name, issues,
            )

        for def_name, definition in (model_ext.get("other_definitions") or {}).items():
            definitions.setdefault(def_name, definition)

        csn: dict[str, Any] = {"definitions": definitions}
        csn_residue = dict(model_ext.get("csn") or {})
        csn["$version"] = csn_residue.pop("$version", "2.0")
        meta = dict(csn_residue.pop("meta", None) or {})
        meta.setdefault("creator", "apache-ossie-sap-csn")
        for key, value in csn_residue.items():
            csn[key] = value

        ossie_meta: dict[str, Any] = {}
        model_info: dict[str, Any] = {"name": document.name}
        if document.description:
            model_info["description"] = document.description
        if document.ai_context is not None:
            model_info["ai_context"] = _dump(document.ai_context)
        foreign = c.foreign_extensions(document.custom_extensions)
        if foreign:
            model_info["custom_extensions"] = foreign
        ossie_meta["model"] = model_info
        if metric_homes["meta"]:
            ossie_meta["metrics"] = [m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in metric_homes["meta"]]
        meta[c.META_KEY] = ossie_meta
        csn["meta"] = meta
        return ConverterResult(output=csn, issues=issues)

    # ----------------------------------------------------------------------------------

    def _plan_relationship(
        self,
        rel: OssieRelationship,
        datasets: dict[str, OssieDataset],
        taken: dict[str, set[str]],
        entity_name: dict[str, str],
        issues: list[ConverterIssue],
    ) -> Optional[dict[str, Any]]:
        if rel.from_dataset not in datasets or rel.to not in datasets:
            issues.append(
                ConverterIssue(
                    ConverterIssueType.RELATIONSHIP_DROPPED,
                    rel.name,
                    f"Relationship joins {rel.from_dataset!r} to {rel.to!r}, and one of them is not a dataset.",
                )
            )
            return None
        if len(rel.from_columns) != len(rel.to_columns):
            issues.append(
                ConverterIssue(
                    ConverterIssueType.RELATIONSHIP_COLUMN_UNRESOLVED,
                    rel.name,
                    "from_columns and to_columns differ in length, so no column pairs can be formed.",
                )
            )
            return None
        ext = c.read_vendor_extension(rel.custom_extensions, f"relationship {rel.name!r}")
        name = ext.get("association") or _identifier(rel.name)
        if name in taken[rel.from_dataset]:
            name = f"{name}_assoc"
        taken[rel.from_dataset].add(name)

        fields = {f.name: f for f in datasets[rel.from_dataset].fields or []}
        managed = all(
            col == f"{name}_{to_col}"
            and col in fields
            and c.read_vendor_extension(fields[col].custom_extensions, col).get("generated_foreign_key")
            and c.read_vendor_extension(fields[col].custom_extensions, col).get("association") == name
            for col, to_col in zip(rel.from_columns, rel.to_columns)
        )
        return {
            "rel": rel,
            "ext": ext,
            "name": name,
            "managed": managed,
            "from_columns": list(rel.from_columns),
            "to_columns": list(rel.to_columns),
            "target": entity_name[rel.to],
        }

    def _place_metrics(
        self,
        document: OssieDocument,
        datasets: dict[str, OssieDataset],
        dataset_ext: dict[str, dict[str, Any]],
        entity_name: dict[str, str],
        plans: dict[str, list[dict[str, Any]]],
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        """Decide for each metric whether it is an annotation, a derived measure, or ``meta``."""
        annotate: dict[tuple[str, str], tuple[OssieMetric, str]] = {}  # (dataset, field) -> (metric, aggregation)
        derived: dict[str, list[tuple[OssieMetric, _Metric]]] = {n: [] for n in datasets}
        in_meta: list[OssieMetric] = []
        element_names = {n: {f.name for f in (d.fields or [])} for n, d in datasets.items()}
        reachable = {n: {p["rel"].to for p in plans[n]} for n in datasets}

        for metric in document.metrics or []:
            sql, other = _select_sql(metric.expression)
            if sql is None:
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.DIALECT_UNAVAILABLE,
                        metric.name,
                        f"Metric has no ANSI_SQL expression (only {other.split(':')[0]}); "
                        "carried in meta.ossie.metrics only.",
                    )
                )
                in_meta.append(metric)
                continue
            parsed = _parse_aggregate(sql)
            home = self._home(parsed, datasets, reachable) if parsed is not None else None
            if home is None:
                in_meta.append(metric)
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.METRIC_NOT_MODELED,
                        metric.name,
                        "CSN expresses a measure as one aggregation over one dataset's columns "
                        "(and those of datasets it reaches through associations); "
                        f"{sql!r} is not of that shape, so it is carried in meta.ossie.metrics only.",
                    )
                )
                continue
            ds_name, inner = home
            plain = isinstance(inner, exp.Column) and not inner.table and inner.name == metric.name and inner.name in element_names[ds_name]
            if plain and (ds_name, metric.name) not in annotate:
                annotate[(ds_name, metric.name)] = (metric, parsed.aggregation)
            elif metric.name in element_names[ds_name] or any(
                m.name == metric.name for lst in derived.values() for m, _ in lst
            ):
                in_meta.append(metric)
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.METRIC_NOT_MODELED,
                        metric.name,
                        f"A measure element named {metric.name!r} would collide with an existing element "
                        "of the same name; carried in meta.ossie.metrics only.",
                    )
                )
            else:
                derived[ds_name].append((metric, _Metric(parsed.aggregation, inner)))
        return {"annotate": annotate, "derived": derived, "meta": in_meta}

    @staticmethod
    def _home(
        parsed: _Metric, datasets: dict[str, OssieDataset], reachable: dict[str, set[str]]
    ) -> Optional[tuple[str, exp.Expression]]:
        """The dataset ``parsed`` aggregates, with its own columns unqualified.

        The home is the one dataset from which every other dataset the expression
        names is reachable through an association. Relationships run from the many
        side to the one side, so such a path is to-one and cannot fan out the rows
        being aggregated.
        """
        tables = {col.table for col in parsed.inner.find_all(exp.Column)}
        if None in tables or "" in tables or not tables <= set(datasets):
            return None
        homes = [t for t in tables if tables - {t} <= reachable[t]]
        if len(homes) != 1:
            return None
        inner = parsed.inner.copy()
        for col in inner.find_all(exp.Column):
            if col.table == homes[0]:
                col.set("table", None)
        return homes[0], inner

    # ----------------------------------------------------------------------------------

    def _entity(
        self,
        dataset: OssieDataset,
        ext: dict[str, Any],
        plans: list[dict[str, Any]],
        generated_fks: set[str],
        metric_homes: dict[str, Any],
        datasets: dict[str, OssieDataset],
        entity_name: dict[str, str],
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        entity: dict[str, Any] = {"kind": "entity"}
        desc_key = ext.get("description_key", "doc")
        if dataset.description:
            entity[desc_key] = dataset.description
        for key, value in (ext.get("annotations") or {}).items():
            entity[key] = value
        entity.update(_carry(dataset.ai_context, dataset.custom_extensions))

        modeled = {f.name for f in dataset.fields or []} | {p["name"] for p in plans}
        names = ext.get("unique_key_names") or []
        for i, columns in enumerate(dataset.unique_keys or []):
            if all(col in modeled for col in columns):
                key_name = names[i] if i < len(names) else f"uk{i + 1}"
                entity[c.UNIQUE_PREFIX + key_name] = [{"=": col} for col in columns]
        if any(col not in modeled for columns in dataset.unique_keys or [] for col in columns):
            entity[c.OSSIE_UNIQUE_KEYS] = [list(columns) for columns in dataset.unique_keys]
            issues.append(
                ConverterIssue(
                    ConverterIssueType.UNIQUE_KEY_COLUMN_UNRESOLVED,
                    dataset.name,
                    "A unique key names a column that is not a field of the dataset, so CDS cannot "
                    "declare it; the keys are carried in an @Ossie.uniqueKeys annotation only.",
                )
            )

        # A path through an association (`customer.name`) is how CXN reaches another entity.
        assoc_to_dataset = {p["rel"].to: p["name"] for p in reversed(plans)}

        def resolve_column(table: Optional[str], column: str) -> list[str]:
            if table is None or table == dataset.name:
                return [column]
            if table in assoc_to_dataset:
                return [assoc_to_dataset[table], column]
            raise UnsupportedExpression(f"column {table}.{column} reaches a dataset with no association from here")

        primary_key = set(dataset.primary_key or [])
        if not primary_key <= modeled:
            entity[c.OSSIE_PRIMARY_KEY] = list(dataset.primary_key)
            issues.append(
                ConverterIssue(
                    ConverterIssueType.PRIMARY_KEY_COLUMN_UNRESOLVED,
                    dataset.name,
                    f"Primary key names {sorted(primary_key - modeled)}, which are not fields of the dataset, "
                    "so CDS cannot mark them `key`; the key is carried in an @Ossie.primaryKey annotation, "
                    "and the columns that do exist are marked `key`.",
                )
            )
        elements: dict[str, Any] = {}
        annotate = metric_homes["annotate"]
        for field in dataset.fields or []:
            fk_ext = c.read_vendor_extension(field.custom_extensions, f"field {dataset.name}.{field.name}")
            if field.name in generated_fks and fk_ext.get("generated_foreign_key"):
                continue  # CDS generates this column from the managed association
            elements[field.name] = self._element(
                dataset, field, fk_ext, field.name in primary_key, resolve_column,
                annotate.get((dataset.name, field.name)), issues,
            )

        for plan in plans:
            elements[plan["name"]] = self._association(plan)

        measures = metric_homes["derived"][dataset.name]
        for metric, parsed in measures:
            elements[metric.name] = self._derived_measure(dataset, metric, parsed, resolve_column, issues)
        # Only for an entity authored in Ossie: one read from CSN keeps the category it had.
        authored_here = not ext
        if authored_here and (measures or any(k[0] == dataset.name for k in annotate)):
            entity[c.DATA_CATEGORY_KEY] = c.enum_annotation("CUBE")

        for name, raw in (ext.get("unmodeled_elements") or {}).items():
            elements.setdefault(name, raw)
        if elements:
            entity["elements"] = elements
        for key, value in (ext.get("native") or {}).items():
            entity[key] = value
        return entity

    def _element(
        self,
        dataset: OssieDataset,
        field: OssieField,
        ext: dict[str, Any],
        is_key: bool,
        resolve_column: Any,
        measure: Optional[tuple[OssieMetric, str]],
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        where = f"{dataset.name}.{field.name}"
        element: dict[str, Any] = {"type": self._type(field.datatype, ext.get("type"), where, issues)}
        if is_key:
            element["key"] = True

        sql, other = _select_sql(field.expression)
        override: Optional[dict[str, str]] = None
        if sql is None:
            dialect, _, text = other.partition(":")
            override = {"dialect": dialect, "expression": text}
            issues.append(
                ConverterIssue(
                    ConverterIssueType.DIALECT_UNAVAILABLE,
                    where,
                    f"Field has no ANSI_SQL expression (only {dialect}); exported as a plain column "
                    "with the original expression in an @Ossie.sql annotation.",
                )
            )
        else:
            try:
                cxn = sql_to_cxn(sql, resolve_column)
                if cxn != {"ref": [field.name]}:
                    element["value"] = cxn
            except UnsupportedExpression as exc:
                override = {"dialect": "ANSI_SQL", "expression": sql}
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
                        where,
                        f"Expression {sql!r} has no CXN translation ({exc}); exported as a plain column "
                        "with the original expression in an @Ossie.sql annotation.",
                    )
                )
        if override:
            element[c.OSSIE_SQL] = override

        if field.label:
            element[ext.get("label_key", c.LABEL_KEYS[0])] = field.label
        if field.description:
            element[ext.get("description_key", "doc")] = field.description
        for key, value in (ext.get("annotations") or {}).items():
            element[key] = value
        if measure is not None:
            element[c.AGGREGATION_KEY] = c.enum_annotation(measure[1])
        element.update(_carry(field.ai_context, field.custom_extensions))

        default_is_time = field.datatype is not None and field.datatype.value in ("Date", "Time", "DateTime", "DateTimeTz")
        if field.dimension is not None and field.dimension.is_time is not None and field.dimension.is_time != default_is_time:
            element[c.OSSIE_IS_TIME] = field.dimension.is_time
        for key, value in (ext.get("native") or {}).items():
            element[key] = value
        return element

    def _derived_measure(
        self,
        dataset: OssieDataset,
        metric: OssieMetric,
        parsed: _Metric,
        resolve_column: Any,
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        where = f"{dataset.name}.{metric.name}"
        ext = c.read_vendor_extension(metric.custom_extensions, f"metric {metric.name!r}")
        measure_ext = ext.get("measure") or {}
        datatype = metric.datatype.value if metric.datatype else None
        if datatype is None and parsed.aggregation in ("COUNT", "COUNT_DISTINCT"):
            datatype = "Integer"
        element: dict[str, Any] = {"type": self._type(datatype, measure_ext.get("type"), where, issues)}
        try:
            element["value"] = sql_to_cxn(parsed.inner.sql(), resolve_column)
        except UnsupportedExpression as exc:
            # Unreachable for metrics that passed placement unless a column path is unresolvable.
            raise ConverterError(f"{where}: {exc}") from exc
        element[c.AGGREGATION_KEY] = c.enum_annotation(parsed.aggregation)
        element[c.OSSIE_METRIC] = True
        if metric.description:
            element["doc"] = metric.description
        if measure_ext.get("label"):
            element[measure_ext.get("label_key", c.LABEL_KEYS[0])] = measure_ext["label"]
        for key, value in (measure_ext.get("annotations") or {}).items():
            element[key] = value
        element.update(_carry(metric.ai_context, metric.custom_extensions))
        for key, value in (measure_ext.get("native") or {}).items():
            element[key] = value
        return element

    def _association(self, plan: dict[str, Any]) -> dict[str, Any]:
        ext = plan["ext"]
        element: dict[str, Any] = {"type": ext.get("type") or "cds.Association", "target": plan["target"]}
        name = plan["name"]
        if plan["managed"]:
            element["keys"] = [{"ref": [col]} for col in plan["to_columns"]]
        else:
            condition: list[Any] = []
            for from_col, to_col in zip(plan["from_columns"], plan["to_columns"]):
                if condition:
                    condition.append("and")
                condition += [{"ref": [name, to_col]}, "=", {"ref": [from_col]}]
            element["on"] = condition
        for key, value in (ext.get("annotations") or {}).items():
            element[key] = value
        rel = plan["rel"]
        element.update(_carry(rel.ai_context, rel.custom_extensions))
        for key, value in (ext.get("native") or {}).items():
            element[key] = value
        return element

    def _type(
        self,
        datatype: Optional[str],
        original: Optional[str],
        where: str,
        issues: list[ConverterIssue],
    ) -> str:
        if original is not None:
            # A CSN document is its own best witness for the CDS type it started as.
            known = builtin_type(original, {})
            if datatype is None or ossie_datatype(known) in (datatype, None):
                return original
        mapped = cds_type(datatype)
        if mapped is not None:
            return mapped
        if datatype == "Opaque":
            issues.append(
                ConverterIssue(
                    ConverterIssueType.OPAQUE_DATATYPE,
                    where,
                    "Opaque datatype has no CDS built-in without a SAP_CSN extension naming one; exported as cds.String.",
                )
            )
        else:
            issues.append(
                ConverterIssue(
                    ConverterIssueType.ASSUMED_DATATYPE,
                    where,
                    "No datatype is declared and CSN requires a type; assumed cds.String.",
                )
            )
        return "cds.String"


def _dump(value: Any) -> Any:
    """JSON-ready form of an Ossie ``ai_context``."""
    return value if isinstance(value, str) else value.model_dump(mode="json", exclude_none=True)


def _carry(ai_context: Any, extensions: Any) -> dict[str, Any]:
    """`@Ossie.*` annotations that keep AI context and other vendors' extensions."""
    carried: dict[str, Any] = {}
    if ai_context is not None:
        carried[c.OSSIE_AI_CONTEXT] = _dump(ai_context)
    foreign = c.foreign_extensions(extensions)
    if foreign:
        carried[c.OSSIE_EXTENSIONS] = foreign
    return carried
