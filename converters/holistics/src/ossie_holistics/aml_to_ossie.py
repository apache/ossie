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

"""Compiled AML to one Apache Ossie semantic model.

`convert` takes the parsed `holistics aml compile` output and returns the Ossie
document plus the issues the conversion raised. A construct it drops or changes
raises an `ISSUE_*` code, and `docs/limitations.md` lists every one.

Naming is the decision that shapes the rest. An AML module path joins with
`::`, so `reporting::calendar` flattens to the Ossie dataset name
`reporting__calendar`. A model measure becomes a root-level Ossie metric, and
root level has one namespace for the whole document, so the metric takes the
name `<dataset>__<measure>`. A reference to a field inside an expression is
dataset-qualified (`ecommerce_orders.created_at`), and a reference to a metric
is the metric's own name, because that is what each one resolves against.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import aml, interpolation, stash
from .datatypes import AGGREGATIONS, AML_TO_OSSIE_DATATYPE, CUSTOM_AGGREGATION, apply_aggregation
from .errors import ConversionError
from .issues import IssueLog, Severity

SPEC_VERSION = "0.2.0.dev0"

AQL_DIALECT = "HOLISTICS_AQL"

ISSUE_AQL_NOT_TRANSLATED = "HOLISTICS_AQL_NOT_TRANSLATED"
ISSUE_INACTIVE_RELATIONSHIP = "HOLISTICS_INACTIVE_RELATIONSHIP"
ISSUE_UNSUPPORTED_RELATIONSHIP = "HOLISTICS_UNSUPPORTED_RELATIONSHIP"
ISSUE_RELATIONSHIP_FILTER = "HOLISTICS_RELATIONSHIP_FILTER"
ISSUE_QUERY_SOURCE = "HOLISTICS_QUERY_SOURCE"
ISSUE_UNRESOLVED_QUERY = "HOLISTICS_UNRESOLVED_QUERY_TEMPLATE"
ISSUE_UNKNOWN_DATATYPE = "HOLISTICS_UNKNOWN_DATATYPE"
ISSUE_UNKNOWN_AGGREGATION = "HOLISTICS_UNKNOWN_AGGREGATION"
ISSUE_UNRESOLVED_REFERENCE = "HOLISTICS_UNRESOLVED_REFERENCE"
ISSUE_PARAM_DROPPED = "HOLISTICS_PARAM_DROPPED"


class Result:
    def __init__(self, model: dict[str, Any], issues: IssueLog):
        self.model = model
        self.issues = issues


def flatten(fqn: str) -> str:
    """An AML module path as a flat Ossie name.

    `__` rather than `_` because a single underscore collides with any model
    name that already contains one, which most do. `reporting::calendar` and a
    root model named `reporting_calendar` would otherwise take the same name.
    """
    return fqn.replace("::", "__")


def _expression(expression: str, dialect: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


class _Converter:
    """One conversion. Holds the name tables every expression resolves against."""

    def __init__(self, dataset: aml.Dataset, sql_dialect: str):
        self.dataset = dataset
        self.sql_dialect = sql_dialect
        self.issues = IssueLog()

        #: Relationships whose condition is a `match` predicate. Ossie encodes a
        #: relationship as column pairs, so these travel in the document stash.
        self.match_relationships: list[dict[str, Any]] = []
        self.models_by_fqn = dataset.model_by_fqn()
        self.dataset_name: dict[str, str] = {}
        claimed_by: dict[str, str] = {}
        for model in dataset.models:
            flat = flatten(model.fqn)
            clash = claimed_by.get(flat)
            if clash is not None:
                raise ConversionError(
                    f"models {clash!r} and {model.fqn!r} both flatten to the Ossie "
                    f"dataset name {flat!r}; rename one of them in AML"
                )
            claimed_by[flat] = model.fqn
            self.dataset_name[model.fqn] = flat

        #: Dataset-level dimensions, grouped by the model they attach to.
        self.extra_dimensions: dict[str, list[aml.Field]] = {}
        for field in dataset.dimensions:
            owner = field.owner_model_fqn
            if owner is None or owner not in self.models_by_fqn:
                raise ConversionError(
                    f"dataset dimension {field.name!r} names model {owner!r}, "
                    f"which is not in this dataset"
                )
            self.extra_dimensions.setdefault(owner, []).append(field)

        #: Every field name reachable on a model, for resolving `{{ field }}`.
        self.model_fields: dict[str, set[str]] = {}
        self.model_measures: dict[str, set[str]] = {}
        for model in dataset.models:
            extra = self.extra_dimensions.get(model.fqn, [])
            self.model_fields[model.fqn] = {d.name for d in model.dimensions} | {
                d.name for d in extra
            }
            self.model_measures[model.fqn] = {m.name for m in model.measures}

    # -- names ------------------------------------------------------------

    def metric_name(self, model_fqn: str, measure_name: str) -> str:
        return f"{self.dataset_name[model_fqn]}__{measure_name}"

    def resolve_model(self, reference: str) -> str | None:
        """An AML model reference to its `__fqn__`.

        A relationship endpoint and a model spell the same name differently. The
        endpoint uses dots (`reporting.calendar`) and the model's own `__fqn__`
        uses colons (`reporting::calendar`). The compiler fills a `__refFqn__`
        shortcut on some endpoints and leaves it null on others, so this
        rewrites the separator and looks the result up instead.
        """
        for candidate in (reference, reference.replace(".", "::")):
            if candidate in self.models_by_fqn:
                return candidate
        return None

    # -- expressions ------------------------------------------------------

    def resolve_reference(self, model_fqn: str, name: str, scope: str) -> str:
        """`{{ field }}` as the Ossie name it resolves to.

        A dimension becomes a dataset-qualified field reference. A measure
        becomes a metric reference, which is the metric's own name because
        metrics live in one root namespace.
        """
        if name in self.model_fields.get(model_fqn, ()):
            return f"{self.dataset_name[model_fqn]}.{name}"
        if name in self.model_measures.get(model_fqn, ()):
            return self.metric_name(model_fqn, name)
        self.issues.add(
            code=ISSUE_UNRESOLVED_REFERENCE,
            severity=Severity.ERROR,
            message=f"expression references {name!r}, which is not a field or measure of this model",
            object_ref=scope,
            remedy="fix the reference in AML, or drop the field",
        )
        return name

    def resolve_model_field(
        self, model_fqn: str, reference: str, field_name: str, scope: str
    ) -> str:
        """`{{ #model.field }}` as an Ossie reference.

        Naming this model is not the same as `{{ field }}`. `{{ field }}` inlines
        the sibling field's own definition, while `{{ #model.field }}` reads the
        column of that model's relation, exactly as `{{ #SOURCE.field }}` does.
        `ecommerce_users.age_bucket` is `FLOOR({{ #ecommerce_users.age }} / 10)`
        and Holistics renders it `FLOOR(`ecommerce_users`.`age` / 10)`, where
        `{{ age }}` would have rendered the whole `extract(...)` body instead.
        Collapsing the two changes the warehouse SQL, so a self-naming reference becomes a
        column and only a measure, which is never a relation column, stays a
        metric reference.
        """
        target = self.resolve_model(reference)
        if target is None:
            self.issues.add(
                code=ISSUE_UNRESOLVED_REFERENCE,
                severity=Severity.ERROR,
                message=f"expression references model {reference!r}, which is not in this dataset",
                object_ref=scope,
                remedy="add the model to the dataset, or drop the field",
            )
            return f"{flatten(reference)}.{field_name}"
        if target == model_fqn and field_name not in self.model_measures.get(target, ()):
            return field_name
        return self.resolve_reference(target, field_name, scope)

    def resolve_interpolation(
        self, model_fqn: str, part: interpolation.Interpolation, scope: str
    ) -> str:
        """One interpolation as the Ossie text that replaces it."""
        if isinstance(part, interpolation.Source):
            return part.column
        if isinstance(part, interpolation.Sibling):
            return self.resolve_reference(model_fqn, part.field, scope)
        if isinstance(part, interpolation.ModelField):
            return self.resolve_model_field(model_fqn, part.model, part.field, scope)
        self.issues.add(
            code=ISSUE_UNRESOLVED_REFERENCE,
            severity=Severity.ERROR,
            message=f"unrecognised AML interpolation {part.to_aml()!r}, carried verbatim",
            object_ref=scope,
            remedy="rewrite the body in AML, or drop the field",
        )
        return part.to_aml()

    def translate_sql(self, model_fqn: str, body: str, scope: str) -> str:
        """A `@sql` body with its interpolations replaced by Ossie references."""
        return "".join(
            part if isinstance(part, str) else self.resolve_interpolation(model_fqn, part, scope)
            for part in interpolation.parse_sql_body(body)
        )

    def sql_body(self, model_fqn: str, body: str, scope: str) -> str:
        """A `@sql` body as an Ossie expression, with its references resolved.

        Every `@sql` body is tagged `--sql-dialect`, including one that is a
        bare reference. Holistics SQL is warehouse SQL throughout, so
        one dialect describes the whole document and a reader needs no rule for
        which fields carry which tag.
        """
        sole = interpolation.sole_reference(body)
        if sole is not None:
            return self.resolve_interpolation(model_fqn, sole, scope)
        return self.translate_sql(model_fqn, body, scope)

    def field_expression(self, model_fqn: str, field: aml.Field, scope: str) -> dict[str, Any]:
        """The `expression` for one dimension.

        An omitted `definition` is a default rather than a missing value: AML
        fills it with `{{ #SOURCE.<field name> }}`, so it resolves exactly like
        a bare source column written out in full.
        """
        definition = field.definition
        if definition is None:
            return _expression(field.name, self.sql_dialect)
        if definition.is_aql:
            self._note_aql(scope)
            return _expression(definition.content, AQL_DIALECT)
        return _expression(self.sql_body(model_fqn, definition.content, scope), self.sql_dialect)

    def measure_expression(self, model_fqn: str, measure: aml.Field, scope: str) -> dict[str, Any]:
        """The `expression` for one measure, aggregation and body composed.

        An AQL body carries its own aggregation, so `aggregation_type` is not
        applied on top of it whatever it says.
        """
        definition = measure.definition
        if definition is None:
            raise ConversionError(
                f"{scope}: a measure must carry a definition, and this one has none"
            )
        if definition.is_aql:
            self._note_aql(scope)
            return _expression(definition.content, AQL_DIALECT)

        body = self.sql_body(model_fqn, definition.content, scope)

        aggregation = measure.aggregation_type
        if aggregation in (None, CUSTOM_AGGREGATION):
            return _expression(body, self.sql_dialect)
        if aggregation not in AGGREGATIONS:
            self.issues.add(
                code=ISSUE_UNKNOWN_AGGREGATION,
                severity=Severity.ERROR,
                message=f"unknown aggregation_type {aggregation!r}, body emitted unaggregated",
                object_ref=scope,
                remedy="add the aggregation to datatypes.AGGREGATIONS",
            )
            return _expression(body, self.sql_dialect)
        return _expression(apply_aggregation(aggregation, body), self.sql_dialect)

    def _note_aql(self, scope: str) -> None:
        self.issues.add(
            code=ISSUE_AQL_NOT_TRANSLATED,
            severity=Severity.INFO,
            message=(
                "body is AQL, carried verbatim under HOLISTICS_AQL. A consumer that does "
                "not speak AQL can read and round-trip it but cannot evaluate it"
            ),
            object_ref=scope,
            remedy="evaluate through Holistics, or rewrite the body as Holistics SQL",
        )

    # -- objects ----------------------------------------------------------

    def datatype(self, field: aml.Field, scope: str) -> str | None:
        if field.aml_type is None:
            return None
        mapped = AML_TO_OSSIE_DATATYPE.get(field.aml_type)
        if mapped is None:
            self.issues.add(
                code=ISSUE_UNKNOWN_DATATYPE,
                severity=Severity.WARNING,
                message=f"unknown AML type {field.aml_type!r}, datatype omitted",
                object_ref=scope,
                remedy="add the type to datatypes.AML_TO_OSSIE_DATATYPE",
            )
        return mapped

    def field_stash(self, field: aml.Field) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if field.hidden:
            data["hidden"] = True
        if field.format:
            data["format"] = field.format
        if field.aml_type:
            data["type"] = field.aml_type
        if field.label:
            data["label"] = field.label
        return data

    def convert_field(
        self, model_fqn: str, field: aml.Field, dataset_level: bool = False
    ) -> dict[str, Any]:
        """One dimension as an Ossie field.

        A dataset-level AML dimension becomes a field on the model it names, and
        the stash records where it was declared. Without that marker the reverse
        path would write it back as a model dimension, and an AQL body spanning
        two models does not resolve from inside one model.
        """
        scope = f"{model_fqn}.{field.name}"
        out: dict[str, Any] = {
            "name": field.name,
            "expression": self.field_expression(model_fqn, field, scope),
        }
        if field.description:
            out["description"] = field.description
        datatype = self.datatype(field, scope)
        if datatype:
            out["datatype"] = datatype
        data = self.field_stash(field)
        if dataset_level:
            data["scope"] = "dataset"
        stash.attach(out, data)
        return out

    def convert_measure(self, model_fqn: str, measure: aml.Field) -> dict[str, Any]:
        scope = f"{model_fqn}.{measure.name}"
        out: dict[str, Any] = {
            "name": self.metric_name(model_fqn, measure.name),
            "expression": self.measure_expression(model_fqn, measure, scope),
        }
        if measure.description:
            out["description"] = measure.description
        datatype = self.datatype(measure, scope)
        if datatype:
            out["datatype"] = datatype
        data = self.field_stash(measure)
        data["model"] = model_fqn
        data["aml_name"] = measure.name
        if measure.aggregation_type:
            data["aggregation_type"] = measure.aggregation_type
        stash.attach(out, data)
        return out

    def convert_dataset_metric(self, metric: aml.Field) -> dict[str, Any]:
        scope = f"{self.dataset.name}.{metric.name}"
        definition = metric.definition
        if definition is None:
            raise ConversionError(f"{scope}: a dataset metric must carry a definition")
        if not definition.is_aql:
            raise ConversionError(
                f"{scope}: a dataset-level metric spans models, so its body has no single "
                f"model to resolve `@sql` against"
            )
        self._note_aql(scope)
        out: dict[str, Any] = {
            "name": metric.name,
            "expression": _expression(definition.content, AQL_DIALECT),
        }
        if metric.description:
            out["description"] = metric.description
        datatype = self.datatype(metric, scope)
        if datatype:
            out["datatype"] = datatype
        stash.attach(out, self.field_stash(metric))
        return out

    def model_source(self, model: aml.Model) -> str:
        scope = model.fqn
        if model.kind is aml.ModelKind.TABLE:
            if not model.table_name:
                raise ConversionError(f"{scope}: a table model has no table_name")
            return model.table_name
        if model.query is None:
            raise ConversionError(f"{scope}: a query model has no query")
        body = model.query.content
        self.issues.add(
            code=ISSUE_QUERY_SOURCE,
            severity=Severity.WARNING,
            message=(
                "a query model becomes a query in `source`, which is one string, so a "
                "consumer reads the text to tell it from a table reference"
            ),
            object_ref=scope,
            remedy="materialise the query as a table in Holistics before converting",
        )
        forms = interpolation.query_forms_used(body)
        if forms:
            self.issues.add(
                code=ISSUE_UNRESOLVED_QUERY,
                severity=Severity.ERROR,
                message=(
                    f"the query body uses Holistics template syntax ({', '.join(forms)}) "
                    f"and is carried unresolved. `source` is not runnable SQL"
                ),
                object_ref=scope,
                remedy="resolve the query through Holistics, or drop the model",
            )
        return body

    def model_stash(self, model: aml.Model) -> dict[str, Any]:
        data: dict[str, Any] = {"aml_type": model.aml_type}
        if model.fqn != model.name:
            data["fqn"] = model.fqn
        if model.label:
            data["label"] = model.label
        if model.owner:
            data["owner"] = model.owner
        if model.data_source_name:
            data["data_source_name"] = model.data_source_name
        if model.table_name:
            data["table_name"] = model.table_name
        if model.persistence:
            data["persistence"] = model.persistence
        if model.params:
            data["param"] = [
                {"name": p.name, "label": p.label, "type": p.aml_type} for p in model.params
            ]
            self.issues.add(
                code=ISSUE_PARAM_DROPPED,
                severity=Severity.WARNING,
                message=(
                    f"{len(model.params)} param(s) have no Ossie equivalent and are stashed. "
                    f"A body that reads one cannot be resolved statically"
                ),
                object_ref=model.fqn,
                remedy="supply the param value in Holistics before converting",
            )
        if model.kind is aml.ModelKind.QUERY and model.query is not None:
            forms = interpolation.query_forms_used(model.query.content)
            if forms:
                data["query_forms"] = forms
        return data

    def convert_model(self, model: aml.Model) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.dataset_name[model.fqn],
            "source": self.model_source(model),
        }
        primary_key = [d.name for d in model.dimensions if d.primary_key]
        if primary_key:
            out["primary_key"] = primary_key
        description = model.description or model.label
        if description:
            out["description"] = description
        out["fields"] = [self.convert_field(model.fqn, f) for f in model.dimensions] + [
            self.convert_field(model.fqn, f, dataset_level=True)
            for f in self.extra_dimensions.get(model.fqn, [])
        ]
        stash.attach(out, self.model_stash(model))
        return out

    def convert_relationship(
        self, relationship: aml.Relationship, index: int, taken: set[str]
    ) -> dict[str, Any] | None:
        scope = f"relationship {index}"
        if relationship.kind not in aml.EQUALITY_KINDS:
            self.issues.add(
                code=ISSUE_UNSUPPORTED_RELATIONSHIP,
                severity=Severity.WARNING,
                message=(
                    f"a {relationship.kind.value} relationship carries an AQL match condition "
                    f"rather than column pairs, so it has no `relationships[]` entry and is "
                    f"stashed on the document instead. A consumer reading Ossie sees the two "
                    f"datasets as unrelated"
                ),
                object_ref=scope,
                remedy="model the join as an equality relationship in AML",
            )
            self.match_relationships.append(relationship.raw)
            return None

        from_fqn = self.resolve_model(relationship.pairs[0][0].model)
        to_fqn = self.resolve_model(relationship.pairs[0][1].model)
        if from_fqn is None or to_fqn is None:
            missing = relationship.pairs[0][0 if from_fqn is None else 1].model
            raise ConversionError(f"{scope}: endpoint names model {missing!r}, not in this dataset")

        from_name = self.dataset_name[from_fqn]
        to_name = self.dataset_name[to_fqn]
        name = f"{from_name}_to_{to_name}"
        base, suffix = name, 2
        while name in taken:
            name, suffix = f"{base}_{suffix}", suffix + 1
        taken.add(name)
        scope = name

        out: dict[str, Any] = {
            "name": name,
            "from": from_name,
            "to": to_name,
            "from_columns": [pair[0].field for pair in relationship.pairs],
            "to_columns": [pair[1].field for pair in relationship.pairs],
        }

        if not relationship.active:
            self.issues.add(
                code=ISSUE_INACTIVE_RELATIONSHIP,
                severity=Severity.WARNING,
                message=(
                    "the relationship is inactive in AML, and Ossie has no inactive marker. "
                    "A consumer joins across it where Holistics leaves it out by default"
                ),
                object_ref=scope,
                remedy="drop the relationship, or activate it in AML",
            )
        if relationship.where is not None:
            self.issues.add(
                code=ISSUE_RELATIONSHIP_FILTER,
                severity=Severity.WARNING,
                message=(
                    "the relationship carries a filter that narrows the join, and Ossie has "
                    "no equivalent. The join widens to every matching row"
                ),
                object_ref=scope,
                remedy="move the condition into a dimension in AML",
            )

        data: dict[str, Any] = {"type": relationship.kind.value, "active": relationship.active}
        for key, value in (
            ("direction", relationship.direction),
            ("nullable", relationship.nullable),
            ("rlp_propagation", relationship.rlp_propagation),
            ("where", relationship.where),
        ):
            if value is not None:
                data[key] = value
        stash.attach(out, data)
        return out

    # -- entry point ------------------------------------------------------

    def run(self) -> Result:
        datasets = [self.convert_model(m) for m in self.dataset.models]

        metrics: list[dict[str, Any]] = []
        for model in self.dataset.models:
            metrics.extend(self.convert_measure(model.fqn, m) for m in model.measures)
        metrics.extend(self.convert_dataset_metric(m) for m in self.dataset.metrics)
        counts = Counter(m["name"] for m in metrics)
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        if duplicates:
            raise ConversionError(
                f"metric name(s) {', '.join(duplicates)} derived twice; "
                f"rename the colliding measures in AML"
            )

        taken: set[str] = set()
        relationships = [
            converted
            for index, relationship in enumerate(self.dataset.relationships)
            if (converted := self.convert_relationship(relationship, index, taken)) is not None
        ]

        model: dict[str, Any] = {
            "version": SPEC_VERSION,
            "name": flatten(self.dataset.fqn),
        }
        description = self.dataset.description or self.dataset.label
        if description:
            model["description"] = description
        model["datasets"] = datasets
        if relationships:
            model["relationships"] = relationships
        if metrics:
            model["metrics"] = metrics

        data: dict[str, Any] = {"aml_type": "Dataset"}
        if self.match_relationships:
            data["match_relationships"] = self.match_relationships
        if self.dataset.fqn != self.dataset.name:
            data["fqn"] = self.dataset.fqn
        if self.dataset.label:
            data["label"] = self.dataset.label
        if self.dataset.owner:
            data["owner"] = self.dataset.owner
        if self.dataset.data_source_name:
            data["data_source_name"] = self.dataset.data_source_name
        stash.attach(model, data)
        return Result(model, self.issues)


def convert(payload: Any, sql_dialect: str) -> Result:
    """One compiled AML dataset to one Ossie semantic model.

    `sql_dialect` names the warehouse a computed `@sql` body is written for. AML
    does not record it, because `data_source_name` is an instance-local
    connection name rather than a dialect declaration. It is required even for a
    dataset whose bodies are all bare references, so that adding one computed
    body later does not change the command a caller has to run.
    """
    return _Converter(aml.parse(payload), sql_dialect).run()
