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

"""One Apache Ossie semantic model to a set of AML source files.

`convert` returns `(relative path, text)` pairs rather than writing them, so the
CLI owns every filesystem decision and a test can read the output without a
temporary directory.

The layout is one dataset file plus one model file per Ossie dataset:

    <dataset>.dataset.aml
    orders.model.aml                      an Ossie dataset named orders
    modules/reporting/calendar.model.aml  an Ossie dataset named reporting__calendar

The input file layout is lost on the way in. The compiled JSON the forward path
reads records which models a dataset holds and not which file each came from, so
the layout is already gone by the time a document reaches Ossie. AML accepts any
arrangement, so the generator picks the one above.

Four AML properties are required and have no Ossie field: `label` and `type` on
every dimension and measure, and `data_source_name` and `relationships` on the
dataset. A stashed value wins. Otherwise `label` is derived from the name the
way AML itself derives it, `type` from `datatype`, and `data_source_name` from
the `--data-source-name` argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any

from . import amlgen, aql, interpolation, sqlrefs, stash
from .amlgen import Block, quote, sanitize, titleize
from .datatypes import AGGREGATIONS, CUSTOM_AGGREGATION, OSSIE_TO_AML_TYPE, strip_aggregation
from .errors import ConversionError
from .issues import IssueLog, Severity

AQL_DIALECT = "HOLISTICS_AQL"

ISSUE_CROSS_MODEL_SQL = "HOLISTICS_CROSS_MODEL_SQL_BODY"
ISSUE_UNPARSEABLE_SQL = "HOLISTICS_UNPARSEABLE_SQL"
ISSUE_UNKNOWN_QUALIFIER = "HOLISTICS_UNKNOWN_QUALIFIER"
ISSUE_METRIC_NOT_TRANSLATED = "HOLISTICS_METRIC_SQL_NOT_TRANSLATED_TO_AQL"
ISSUE_ASSUMED_TYPE = "HOLISTICS_ASSUMED_TYPE"
ISSUE_AGGREGATION_NOT_STRIPPED = "HOLISTICS_AGGREGATION_NOT_STRIPPED"
ISSUE_RENAMED = "HOLISTICS_NAME_NOT_SPELLABLE_IN_AML"
ISSUE_DATASET_RENAMED = "HOLISTICS_DATASET_NAME_COLLIDES_WITH_MODEL"
ISSUE_TRANSPILED = "HOLISTICS_EXPRESSION_RENDERED_INTO_TARGET_DIALECT"
ISSUE_NO_USABLE_DIALECT = "HOLISTICS_NO_USABLE_DIALECT"
ISSUE_RENDERED_AS_AQL = "HOLISTICS_METRIC_RENDERED_AS_AQL"
ISSUE_BORROWED_COLUMN = "HOLISTICS_UNDECLARED_COLUMN_ADDED_AS_DIMENSION"
ISSUE_WINDOW_NEEDS_GROUPING = "HOLISTICS_WINDOW_FUNCTION_NEEDS_A_GROUPING"
ISSUE_METRIC_CYCLE = "HOLISTICS_METRIC_REFERENCE_CYCLE"

#: Placement markers for `_datasets_named`. A metric that finds one still marked
#: `_RESOLVING` closed a reference cycle, which no single model can hold.
_UNVISITED = object()
_RESOLVING = object()

#: The AML type written for a field whose Ossie `datatype` is absent.
FALLBACK_TYPE = "text"

#: The AML type for a column borrowed into a model by an AQL metric. Ossie
#: declares nothing about it, and every borrowed column so far is an aggregate's
#: argument, so `number` is the reading that lets the metric compile.
FALLBACK_TYPE_FOR_BORROWED = "number"


class Result:
    def __init__(self, files: list[tuple[str, str]], issues: IssueLog):
        self.files = files
        self.issues = issues


@dataclass
class Model:
    """One Ossie dataset, as the AML model it will be written as."""

    ossie_name: str
    fqn: str
    payload: dict[str, Any]
    stash: dict[str, Any]
    measures: list[dict[str, Any]] = dataclass_field(default_factory=list)
    #: Columns an AQL metric needed that the dataset does not declare as a
    #: field. Each becomes a hidden dimension so the AQL reference resolves.
    borrowed: list[str] = dataclass_field(default_factory=list)
    #: Fields an AQL metric aggregates numerically. The metric is evidence of
    #: the type when the Ossie document declares no `datatype`.
    numeric: set[str] = dataclass_field(default_factory=set)

    @property
    def segments(self) -> list[str]:
        return [sanitize(part) for part in self.fqn.split("::")]

    @property
    def aml_fqn(self) -> str:
        return "::".join(self.segments)

    @property
    def aml_name(self) -> str:
        return self.segments[-1]

    @property
    def path(self) -> str:
        parts = self.segments
        if len(parts) == 1:
            return f"{parts[0]}.model.aml"
        return "/".join(["modules", *parts[:-1], f"{parts[-1]}.model.aml"])

    @property
    def reference(self) -> str:
        """How the dataset block names this model. Dots, not `::`."""
        return ".".join(self.segments)


def unflatten(name: str) -> str:
    """An Ossie dataset name as an AML module path.

    The forward path writes `reporting::calendar` as `reporting__calendar`, so
    this reverses that. A stashed `fqn` wins over this rule, because it records
    where the module boundaries actually were rather than inferring them.
    """
    return name.replace("__", "::")


#: The dialects a portable body may be written in. Every sibling converter that
#: reads Ossie treats `OSSIE_SQL_2026` as an `ANSI_SQL` equivalent, including
#: Snowflake's `_extract_expression` and Cube's `pick_expression`.
PORTABLE_DIALECTS = ("ANSI_SQL", "OSSIE_SQL_2026")

#: What the compiler fills in for a relationship that declares none of them.
#: Every compiled payload carries all three, so writing them back
#: unconditionally would turn every short `relationship(...)` into a
#: `RelationshipConfig` block that means exactly the same thing.
RELATIONSHIP_DEFAULTS: dict[str, Any] = {
    "direction": "two_way",
    "nullable": True,
    "rlp_propagation": "inherit",
}


def _named_objects(container: Any, where: str) -> list[dict[str, Any]]:
    """`container` as a list of named mappings, or a `ConversionError` saying why.

    A document this converter did not write is parsed at the boundary rather
    than indexed into. The CLI catches `ConversionError` alone, so any other
    exception reaches the user as a traceback. apache/ossie#297 and
    apache/ossie#407 both asked a sibling converter for the same guard.
    """
    if container is None:
        return []
    if not isinstance(container, list):
        raise ConversionError(
            f"{where} is {type(container).__name__}, and the specification defines it as a list"
        )
    for index, member in enumerate(container):
        if not isinstance(member, dict):
            raise ConversionError(
                f"{where}[{index}] is {type(member).__name__}, and must be a mapping"
            )
        name = member.get("name")
        if not isinstance(name, str) or not name:
            raise ConversionError(f"{where}[{index}] has no name")
    return container


def dialects_of(expression: Any) -> dict[str, str]:
    """Every `{dialect: expression}` pair, validated at the boundary.

    A document this converter did not write arrives unchecked, and the CLI
    catches `ConversionError` alone, so the shape is tested rather than assumed.
    """
    if expression is None:
        raise ConversionError("expression is missing, and the specification requires one")
    if not isinstance(expression, dict):
        raise ConversionError(
            f"expression is {type(expression).__name__}, and the specification defines it "
            f"as a mapping holding a 'dialects' list"
        )
    dialects = expression.get("dialects") or []
    if not dialects:
        raise ConversionError("expression has no dialects")
    if not isinstance(dialects, list):
        raise ConversionError(
            f"expression.dialects is {type(dialects).__name__}, and must be a list"
        )
    available: dict[str, str] = {}
    for index, entry in enumerate(dialects):
        if not isinstance(entry, dict):
            raise ConversionError(
                f"expression.dialects[{index}] is {type(entry).__name__}, and must be a "
                f"mapping holding 'dialect' and 'expression'"
            )
        available[entry.get("dialect", "ANSI_SQL")] = entry.get("expression", "")
    return available


class _Reverse:
    def __init__(
        self, document: dict[str, Any], data_source_name: str | None, sql_dialect: str
    ):
        self.document = document
        self.sql_dialect = sql_dialect
        self.issues = IssueLog()
        self.root_stash = stash.read(document)
        self.data_source_name = self.root_stash.get("data_source_name") or data_source_name
        if not self.data_source_name:
            raise ConversionError(
                "the document carries no HOLISTICS data_source_name, so one must be "
                "supplied: pass --data-source-name naming the Holistics connection"
            )

        #: AML identifier to the Ossie name it was spelled from, so two names
        #: that collapse onto one spelling fail rather than overwrite.
        self._renamed: dict[str, str] = {}

        datasets = _named_objects(document.get("datasets"), "datasets")
        if not datasets:
            raise ConversionError(
                "the document declares no datasets, and the specification requires at least one"
            )
        for dataset in datasets:
            _named_objects(dataset.get("fields"), f"datasets[{dataset['name']}].fields")
        _named_objects(document.get("metrics"), "metrics")

        self.models: list[Model] = []
        for dataset in datasets:
            name = dataset["name"]
            data = stash.read(dataset)
            self.models.append(
                Model(
                    ossie_name=name,
                    fqn=data.get("fqn") or unflatten(name),
                    payload=dataset,
                    stash=data,
                )
            )
        self.by_ossie_name = {m.ossie_name: m for m in self.models}

        taken: dict[str, str] = {}
        for model in self.models:
            clash = taken.setdefault(model.aml_fqn, model.ossie_name)
            if clash != model.ossie_name:
                raise ConversionError(
                    f"datasets {clash!r} and {model.ossie_name!r} both spell as the AML "
                    f"model {model.aml_fqn!r}; rename one of them in Ossie"
                )

        self.dataset_name = self._dataset_name()

        #: Every metric that belongs on a model, indexed by Ossie metric name,
        #: alongside the AML measure name it is written as.
        self.metric_owner: dict[str, Model] = {}
        self.metric_aml_name: dict[str, str] = {}
        self.dataset_metrics: list[dict[str, Any]] = []
        #: Relationships that carry a `where` filter, declared above the
        #: Dataset block because only a named relationship can hold one.
        self.relationship_declarations: list[str] = []
        self._metric_payloads: dict[str, dict[str, Any]] = {}
        self._metric_datasets: dict[str, Any] = {}
        self._cyclic_metrics: set[str] = set()
        self._place_metrics()

    # -- placement --------------------------------------------------------

    def _place_metrics(self) -> None:
        """Decide where each Ossie metric goes: on a model, or on the dataset.

        A stashed `model` settles it. Without one, the expression decides: a
        body naming exactly one dataset becomes a measure on that model, and a
        body naming several becomes a dataset-level metric, which is the only
        AML construct that spans models. A body may also name another metric,
        and `_scan_for_datasets` covers what that counts as.
        """
        metrics = self.document.get("metrics") or []
        for metric in metrics:
            name = metric.get("name")
            self.metric_aml_name[name] = stash.read(metric).get("aml_name") or name
            self._metric_payloads[name] = metric
        for metric in metrics:
            name = metric.get("name")
            named = self._datasets_named(name) or frozenset()
            owner = self.by_ossie_name[next(iter(named))] if len(named) == 1 else None
            if owner is None:
                self.dataset_metrics.append(metric)
            else:
                owner.measures.append(metric)
                self.metric_owner[name] = owner
        self._report_metric_cycles()

    def _datasets_named(self, name: str) -> frozenset[str] | None:
        """The Ossie datasets metric `name` resolves to, or None when that is unknown.

        None covers every reading that cannot put the metric on a single model:
        an AQL body, Ossie SQL this converter cannot read, and a reference cycle.
        """
        resolved = self._metric_datasets.get(name, _UNVISITED)
        if resolved is _RESOLVING:
            self._cyclic_metrics.add(name)
            return None
        if resolved is not _UNVISITED:
            return resolved
        self._metric_datasets[name] = _RESOLVING
        result = self._scan_for_datasets(name)
        self._metric_datasets[name] = result
        return result

    def _scan_for_datasets(self, name: str) -> frozenset[str] | None:
        """Which datasets one metric body names, directly or through a metric.

        An Ossie metric body may name another metric instead of a column, as
        `cities__upstream_measure + 1` in `tests/fixtures/ecommerce` does. That
        body names no dataset of its own, and the datasets the metric it
        references resolves to stand in for it. Without that substitution a
        metric built only from other metrics lands on the dataset, where AML
        takes an AQL body, and the Ossie SQL name it was written with does not
        resolve.
        """
        metric = self._metric_payloads[name]
        data = stash.read(metric)
        owner_fqn = data.get("model")
        if owner_fqn:
            owner = next((m for m in self.models if m.fqn == owner_fqn), None)
            if owner is None:
                raise ConversionError(
                    f"metric {name!r} is stashed against model {owner_fqn!r}, "
                    f"which is not a dataset in this document"
                )
            return frozenset({owner.ossie_name})
        available = dialects_of(metric.get("expression"))
        if AQL_DIALECT in available:
            return None
        dialect = next(
            (d for d in (self.sql_dialect, *PORTABLE_DIALECTS) if d in available), None
        )
        if dialect is None:
            return None
        try:
            references = sqlrefs.columns(available[dialect], dialect)
        except sqlrefs.UnparseableSQL:
            return None
        named = {c.table for c in references if c.table in self.by_ossie_name}
        for reference in references:
            if reference.table or reference.name not in self._metric_payloads:
                continue
            through = self._datasets_named(reference.name)
            if through is None:
                return None
            named |= through
        return frozenset(named)

    def _report_metric_cycles(self) -> None:
        for name in sorted(self._cyclic_metrics):
            self.issues.add(
                code=ISSUE_METRIC_CYCLE,
                severity=Severity.ERROR,
                message=(
                    f"metric {name!r} references itself through another metric. Holistics "
                    f"inlines one metric into the body of the next, so a cycle has no warehouse SQL, "
                    f"and `holistics aml validate` accepts the file without catching it"
                ),
                object_ref=name,
                remedy="break the cycle in Ossie",
            )

    # -- names ------------------------------------------------------------

    def _dataset_name(self) -> str:
        """The AML `Dataset` name, moved aside when a model already has it.

        Ossie keeps the semantic model's name and its dataset names in separate
        namespaces, so a document may use one name for both. Three fixtures in
        this repository do, including
        `converters/cube/tests/fixtures/databricks_ossie.yaml`, whose root name
        and first dataset are both `orders`. AML has one namespace for `Dataset`
        and `Model`, and rejects the pair with "Duplicated name 'orders'".

        Nothing references the AML dataset name. Models reference each other and
        relationships reference models, so the dataset is the safe side to move.
        """
        name = self.root_stash.get("fqn") or self.document.get("name")
        if not name:
            raise ConversionError("the document has no name")
        spelled = self.name_in_aml(name, str(name))
        taken = {model.aml_fqn for model in self.models} | {
            model.aml_name for model in self.models
        }
        if spelled not in taken:
            return spelled
        candidate, suffix = f"{spelled}_dataset", 2
        while candidate in taken:
            candidate, suffix = f"{spelled}_dataset_{suffix}", suffix + 1
        self.issues.add(
            code=ISSUE_DATASET_RENAMED,
            severity=Severity.WARNING,
            message=(
                f"a model is already named {spelled!r}, and AML holds Dataset and Model "
                f"in one namespace, so the dataset is written as {candidate!r}"
            ),
            object_ref=str(name),
            remedy="rename the semantic model or the dataset in Ossie",
        )
        return candidate

    def name_in_aml(self, name: str, scope: str) -> str:
        """An Ossie name as the AML identifier it is written as.

        Lookups elsewhere stay keyed on the Ossie name, because that is what an
        expression references. Only the output spelling changes, and every site
        that writes a name goes through here, so a declaration and a reference
        to it cannot disagree.
        """
        spelled = sanitize(name)
        if spelled != name:
            previous = self._renamed.setdefault(spelled, name)
            if previous != name:
                raise ConversionError(
                    f"{scope}: {name!r} and {previous!r} both spell as the AML "
                    f"identifier {spelled!r}; rename one of them in Ossie"
                )
            self.issues.add(
                code=ISSUE_RENAMED,
                severity=Severity.WARNING,
                message=(
                    f"{name!r} is not a legal AML identifier and is written as "
                    f"{spelled!r}. AML allows letters, digits and underscore, and no "
                    f"leading digit"
                ),
                object_ref=scope,
                remedy=f"rename it to {spelled!r} in Ossie to keep the two in step",
            )
        return spelled

    # -- expressions ------------------------------------------------------

    def pick_body(self, expression: Any, scope: str) -> tuple[str, str]:
        """The body to write and the dialect it is in, after any rendering.

        AQL wins outright. It came out of AML unchanged and goes back unchanged.

        For Ossie SQL the target is `--sql-dialect`, the warehouse the Holistics
        connection reads. A body already in that dialect is written as it
        stands. Otherwise a portable body is rendered into the target with
        sqlglot, because the alternative is AML that will not run. An expression
        offering neither has no body this connection can execute, and the
        converter says so rather than writing Holistics SQL for the wrong warehouse.
        """
        available = dialects_of(expression)
        if AQL_DIALECT in available:
            return AQL_DIALECT, available[AQL_DIALECT]
        if self.sql_dialect in available:
            return self.sql_dialect, available[self.sql_dialect]

        source = next((d for d in PORTABLE_DIALECTS if d in available), None)
        if source is not None:
            try:
                rendered = sqlrefs.transpile(available[source], source, self.sql_dialect)
            except sqlrefs.UnparseableSQL as exc:
                self.issues.add(
                    code=ISSUE_UNPARSEABLE_SQL,
                    severity=Severity.ERROR,
                    message=(
                        f"the {source} body cannot be rendered as {self.sql_dialect} ({exc}), "
                        f"so it is written unchanged and may not run"
                    ),
                    object_ref=scope,
                    remedy=f"add a {self.sql_dialect} expression, or fix the {source} one",
                )
                return source, available[source]
            self.issues.add(
                code=ISSUE_TRANSPILED,
                severity=Severity.WARNING,
                message=(
                    f"no {self.sql_dialect} expression, so the {source} one was rendered "
                    f"into it: {available[source]!r} became {rendered!r}"
                ),
                object_ref=scope,
                remedy=f"add a {self.sql_dialect} expression to keep the body exact",
            )
            return self.sql_dialect, rendered

        fallback = next(iter(available))
        self.issues.add(
            code=ISSUE_NO_USABLE_DIALECT,
            severity=Severity.ERROR,
            message=(
                f"the expression offers {', '.join(sorted(available))} and the connection "
                f"reads {self.sql_dialect}, with no portable body to render from, so the "
                f"{fallback} body is written unchanged and will not run"
            ),
            object_ref=scope,
            remedy=f"add a {self.sql_dialect} or ANSI_SQL expression",
        )
        return fallback, available[fallback]

    def definition(self, block: Block, expression: Any, model: Model, scope: str) -> None:
        """Write a `definition` property for a field or a measure."""
        dialect, text = self.pick_body(expression, scope)
        if dialect == AQL_DIALECT:
            block.heredoc("definition", "aql", text)
            return
        block.heredoc("definition", "sql", self.to_aml_sql(text, dialect, model, scope))

    def to_aml_sql(self, expression: str, dialect: str, model: Model, scope: str) -> str:
        """An Ossie SQL expression with its references written as AML interpolation.

        A bare column name does compile as AML, but AQL reads the interpolation
        to learn which field the body depends on. A body without it loses the
        query rewriting and the optimisation that link drives, so this restores
        every reference rather than passing the SQL through.
        """
        if dialect in sqlrefs.NOT_SQL or not sqlrefs.AVAILABLE:
            self.issues.add(
                code=ISSUE_UNPARSEABLE_SQL,
                severity=Severity.ERROR,
                message=(
                    f"the body is tagged {dialect!r}, which is not Ossie SQL this converter can "
                    f"read, so its field references cannot be restored and AQL cannot see them"
                ),
                object_ref=scope,
                remedy="supply an ANSI_SQL or warehouse-SQL dialect for this expression",
            )
            return expression
        grouping = sqlrefs.window_grouping(expression, dialect)
        if grouping:
            self.issues.add(
                code=ISSUE_WINDOW_NEEDS_GROUPING,
                severity=Severity.WARNING,
                message=(
                    f"the body has a window function whose frame reads "
                    f"{', '.join(grouping)}. AML carries it as written, and the warehouse SQL "
                    f"it generates is valid only for a query that groups by those. A query "
                    f"that does not, a grand total for one, produces warehouse SQL the "
                    f"database rejects, and Holistics does not check this at conversion time"
                ),
                object_ref=scope,
                remedy="query it only alongside those fields, or rewrite the body in AQL",
            )
        try:
            return sqlrefs.rewrite(
                expression, dialect, lambda column: self.to_interpolation(column, model, scope)
            )
        except sqlrefs.UnparseableSQL as exc:
            self.issues.add(
                code=ISSUE_UNPARSEABLE_SQL,
                severity=Severity.ERROR,
                message=(
                    f"sqlglot cannot read the body as {dialect} ({exc}), so its field "
                    f"references cannot be restored and AQL cannot see them"
                ),
                object_ref=scope,
                remedy="fix the expression, or convert it by hand",
            )
            return expression

    def to_interpolation(self, column: sqlrefs.ColumnReference, model: Model, scope: str) -> str:
        """One Ossie column reference as the AML interpolation it came from."""
        if not column.table:
            if column.name in self.metric_aml_name:
                return self._measure_reference(column.name, model, scope)
            return interpolation.Source(column.name).to_aml()

        target = self.by_ossie_name.get(column.table)
        if target is None:
            self.issues.add(
                code=ISSUE_UNKNOWN_QUALIFIER,
                severity=Severity.ERROR,
                message=(
                    f"the body qualifies {column.name!r} with {column.table!r}, which is not "
                    f"a dataset in this document, so it is left unresolved"
                ),
                object_ref=scope,
                remedy="qualify the reference with a dataset name, or drop the field",
            )
            return f"{column.table}.{column.name}"
        if target is model:
            # A qualifier naming this model usually names one of its fields. It
            # can also name a metric this converter put somewhere else, and a
            # sibling reference to that spells a field the model does not have.
            if column.name in self.metric_aml_name and self.metric_owner.get(column.name) is not model:
                return self._measure_reference(column.name, model, scope)
            return interpolation.Sibling(self.name_in_aml(column.name, scope)).to_aml()
        self.issues.add(
            code=ISSUE_CROSS_MODEL_SQL,
            severity=Severity.ERROR,
            message=(
                f"the body names {column.table}.{column.name}, a field of another dataset. A `@sql` "
                f"body resolves inside one model, and Holistics rejects this at SQL generation "
                f"with \"Cannot refer to external field in TableModel/QueryModel\" even though "
                f"`holistics aml validate` accepts the file"
            ),
            object_ref=scope,
            remedy="move the reference into a dimension on its own model, or rewrite the body in AQL",
        )
        return interpolation.ModelField(target.aml_fqn, self.name_in_aml(column.name, scope)).to_aml()

    def _measure_reference(self, metric_name: str, model: Model, scope: str) -> str:
        owner = self.metric_owner.get(metric_name)
        aml_name = self.metric_aml_name[metric_name]
        if owner is model:
            return interpolation.Sibling(self.name_in_aml(aml_name, scope)).to_aml()
        if owner is None:
            self.issues.add(
                code=ISSUE_CROSS_MODEL_SQL,
                severity=Severity.ERROR,
                message=(
                    f"the body references the dataset-level metric {metric_name!r}. A `@sql` "
                    f"body resolves inside one model, so there is no AML spelling for it"
                ),
                object_ref=scope,
                remedy="rewrite the expression in AQL, which does span models",
            )
            return metric_name
        self.issues.add(
            code=ISSUE_CROSS_MODEL_SQL,
            severity=Severity.ERROR,
            message=(
                f"the body references {metric_name!r}, a measure of model {owner.aml_fqn!r}. A "
                f"`@sql` body resolves inside one model, and Holistics rejects this at SQL "
                f"generation with \"Cannot refer to external field in TableModel/QueryModel\" "
                f"even though `holistics aml validate` accepts the file"
            ),
            object_ref=scope,
            remedy="rewrite the expression in AQL, which does span models",
        )
        return interpolation.ModelField(owner.aml_fqn, self.name_in_aml(aml_name, scope)).to_aml()

    # -- fields -----------------------------------------------------------

    def aml_type(
        self, payload: dict[str, Any], data: dict[str, Any], scope: str, model: Model | None = None
    ) -> str:
        stashed = data.get("type")
        if stashed:
            return stashed
        datatype = payload.get("datatype")
        mapped = OSSIE_TO_AML_TYPE.get(datatype) if datatype else None
        if mapped:
            return mapped
        if model is not None and payload["name"] in model.numeric:
            # A metric aggregates this field with `sum` or `avg`, so it is a
            # number whatever the document failed to say.
            return "number"
        self.issues.add(
            code=ISSUE_ASSUMED_TYPE,
            severity=Severity.WARNING,
            message=(
                f"AML requires a type and the Ossie datatype is {datatype!r}, "
                f"so {FALLBACK_TYPE!r} is written"
            ),
            object_ref=scope,
            remedy="set a datatype on the field, or edit the AML type after conversion",
        )
        return FALLBACK_TYPE

    def common_properties(
        self,
        block: Block,
        payload: dict[str, Any],
        data: dict[str, Any],
        scope: str,
        model: Model | None = None,
    ) -> None:
        block.text("label", data.get("label") or titleize(payload["name"]))
        block.property("type", quote(self.aml_type(payload, data, scope, model)))
        block.text("description", payload.get("description"))
        block.flag("hidden", bool(data.get("hidden")))

    def dimension(self, model: Model, payload: dict[str, Any], primary_key: set[str]) -> Block:
        name = payload["name"]
        scope = f"{model.ossie_name}.{name}"
        data = stash.read(payload)
        block = Block(f"dimension {self.name_in_aml(name, scope)}")
        self.common_properties(block, payload, data, scope, model)
        self.definition(block, payload.get("expression"), model, scope)
        block.text("format", data.get("format"))
        block.flag("primary_key", name in primary_key)
        return block

    def dataset_dimension(self, model: Model, payload: dict[str, Any]) -> Block:
        """A field the forward path took from a dataset-level AML dimension."""
        name = payload["name"]
        scope = f"{model.ossie_name}.{name}"
        data = stash.read(payload)
        block = Block(f"dimension {self.name_in_aml(name, scope)}")
        block.property("model", model.reference)
        self.common_properties(block, payload, data, scope, model)
        self.definition(block, payload.get("expression"), model, scope)
        block.text("format", data.get("format"))
        return block

    def measure(self, model: Model, payload: dict[str, Any]) -> Block:
        """One Ossie metric as a `measure` on its owning model.

        `aggregation_type` and the body compose into one Ossie expression on the
        way out, so this splits them back apart. When the stashed aggregation
        does not wrap the whole expression, the expression was edited after the
        forward path wrote it, and `custom` keeps it whole rather than letting
        the measure aggregate twice.
        """
        metric_name = payload["name"]
        aml_name = self.metric_aml_name[metric_name]
        scope = f"{model.ossie_name}.{aml_name}"
        data = stash.read(payload)
        block = Block(f"measure {self.name_in_aml(aml_name, scope)}")
        block.text("label", data.get("label") or titleize(aml_name))
        block.property("type", quote(self.aml_type(payload, data, scope)))
        block.text("description", payload.get("description"))
        block.flag("hidden", bool(data.get("hidden")))

        dialect, text = self.pick_body(payload.get("expression"), scope)
        aggregation = data.get("aggregation_type") or CUSTOM_AGGREGATION
        if dialect == AQL_DIALECT:
            block.heredoc("definition", "aql", text)
        else:
            if aggregation in AGGREGATIONS:
                inner = strip_aggregation(aggregation, text)
                if inner is None:
                    self.issues.add(
                        code=ISSUE_AGGREGATION_NOT_STRIPPED,
                        severity=Severity.WARNING,
                        message=(
                            f"the stashed aggregation {aggregation!r} does not wrap the whole "
                            f"expression, so the measure is written as 'custom' with the "
                            f"expression kept whole"
                        ),
                        object_ref=scope,
                        remedy="check the expression against the aggregation",
                    )
                    aggregation = CUSTOM_AGGREGATION
                else:
                    text = inner
            block.heredoc("definition", "sql", self.to_aml_sql(text, dialect, model, scope))
        block.property("aggregation_type", quote(aggregation))
        block.text("format", data.get("format"))
        return block

    def as_aql(self, text: str, dialect: str, scope: str) -> str | None:
        """A dataset-level metric's Ossie SQL body rendered as AQL, or None if it cannot be.

        AML accepts only `@aql` here, so an Ossie SQL body either becomes AQL or has no
        AML form at all. `aql.translate` is a name map over functions this
        converter has paired with an AQL equivalent. Anything outside that table
        raises `Untranslatable`, and the metric is dropped with an ERROR.
        """
        try:
            rendered, numeric = aql.translate(
                text, dialect, lambda table, column: self.aql_reference(table, column, scope)
            )
        except aql.Untranslatable as exc:
            self.issues.add(
                code=ISSUE_METRIC_NOT_TRANSLATED,
                severity=Severity.ERROR,
                message=(
                    f"the metric sits on the dataset, where AML accepts only an AQL body. The "
                    f"converter did not translate this {dialect} body, and the metric is "
                    f"dropped. {exc}"
                ),
                object_ref=scope,
                remedy="rewrite the metric in AQL, or scope it to a single dataset",
            )
            return None
        self.issues.add(
            code=ISSUE_RENDERED_AS_AQL,
            severity=Severity.WARNING,
            message=(
                f"the metric sits on the dataset, where AML accepts only an AQL body. The "
                f"{dialect} expression was rendered as AQL: {text!r} became {rendered!r}"
            ),
            object_ref=scope,
            remedy="check the AQL against the Ossie SQL it came from",
        )
        for table, column in numeric:
            target = self.by_ossie_name.get(table)
            if target is not None:
                target.numeric.add(self.name_in_aml(column, scope))
        return rendered

    def aql_reference(self, table: str, column: str, scope: str) -> str:
        """One column of an Ossie SQL metric body as the AQL reference it becomes.

        AQL names a field `model.field`, which is the shape the Ossie expression
        already uses, so this maps the Ossie dataset name onto the AML model
        name and keeps the field name.

        An Ossie SQL body may name a warehouse column the document does not declare as
        a field. `SUM(orders.subtotal) / COUNT(DISTINCT customers.customer_id)`
        in `converters/nvidia/tests/fixtures/sales.ossie.yaml` is one: `orders`
        declares no `subtotal`. Ossie SQL resolves that against the table, and AQL
        does not, answering `Field `subtotal` not found in model `orders``. So
        the column becomes a hidden dimension on that model, and the AQL
        reference points at it.
        """
        if not table:
            # An unqualified name that is a metric is a metric reference, which
            # AQL takes: a dataset-level one by its bare name, and a measure by
            # the model that holds it.
            if column in self.metric_aml_name:
                referenced = self.name_in_aml(self.metric_aml_name[column], scope)
                owner = self.metric_owner.get(column)
                return referenced if owner is None else f"{owner.aml_fqn}.{referenced}"
            raise aql.Untranslatable(
                f"{column!r} names no dataset, and a metric spanning models has no single "
                f"model to resolve it against."
            )
        target = self.by_ossie_name.get(table)
        if target is None:
            raise aql.Untranslatable(f"{table!r} is not a dataset in this document.")
        name = self.name_in_aml(column, scope)
        # Compared on the AML spelling, because `name` is already sanitized.
        # `sanitize` rather than `name_in_aml`, which logs a rename per call.
        declared = {
            sanitize(field["name"]) for field in target.payload.get("fields") or []
        } | {sanitize(self.metric_aml_name[m["name"]]) for m in target.measures}
        if name not in declared and name not in target.borrowed:
            target.borrowed.append(name)
            self.issues.add(
                code=ISSUE_BORROWED_COLUMN,
                severity=Severity.WARNING,
                message=(
                    f"the metric reads {table}.{column}, which the dataset does not declare "
                    f"as a field. AQL resolves a reference against declared fields only, so "
                    f"{column!r} is added to model {target.aml_fqn!r} as a hidden dimension "
                    f"over the column of that name"
                ),
                object_ref=scope,
                remedy=f"declare {column!r} as a field of {table!r} in Ossie",
            )
        return f"{target.aml_fqn}.{name}"

    def dataset_metric(self, payload: dict[str, Any]) -> Block | None:
        """One Ossie metric as a dataset-level AML `metric`.

        AML accepts only an `@aql` body here. The compiler rejects `@sql` with
        "Incompatible property 'definition' of 'Metric'", so a cross-model
        metric carrying Ossie SQL has no AML form at all.
        """
        name = payload["name"]
        scope = name
        data = stash.read(payload)
        dialect, text = self.pick_body(payload.get("expression"), scope)
        if dialect != AQL_DIALECT:
            rendered = self.as_aql(text, dialect, scope)
            if rendered is None:
                return None
            text = rendered
        block = Block(f"metric {self.name_in_aml(name, scope)}")
        block.text("label", data.get("label") or titleize(name))
        block.property("type", quote(self.aml_type(payload, data, scope)))
        block.text("description", payload.get("description"))
        block.flag("hidden", bool(data.get("hidden")))
        block.heredoc("definition", "aql", text)
        block.text("format", data.get("format"))
        return block

    # -- models -----------------------------------------------------------

    def model_file(self, model: Model) -> str:
        payload = model.payload
        data = model.stash
        scope = model.ossie_name
        is_query = data.get("aml_type") == "QueryModel" or (
            "aml_type" not in data and bool(interpolation.query_forms_used(payload.get("source", "")))
        )
        source = payload.get("source", "")
        if "aml_type" not in data and not is_query:
            is_query = _looks_like_query(source)

        block = Block(f"Model {model.aml_name}")
        block.property("type", quote("query" if is_query else "table"))
        block.text("label", data.get("label") or titleize(model.aml_name))
        block.text("description", payload.get("description"))
        block.text("data_source_name", data.get("data_source_name") or self.data_source_name)
        if is_query:
            referenced = _models_referenced(source)
            if referenced:
                block.property("models", f"[{', '.join(sorted(referenced))}]")
            block.blank()
            block.heredoc("query", "sql", source)
        else:
            block.text("table_name", data.get("table_name") or source)

        for param in data.get("param") or []:
            block.blank()
            param_block = block.block(f"param {self.name_in_aml(param['name'], scope)}")
            param_block.text("label", param.get("label") or titleize(param["name"]))
            param_block.property("type", quote(param.get("type") or FALLBACK_TYPE))

        persistence = data.get("persistence")
        if persistence:
            block.blank()
            _write_persistence(block, persistence, scope)

        primary_key = set(payload.get("primary_key") or [])
        for field in payload.get("fields") or []:
            if stash.read(field).get("scope") == "dataset":
                continue
            block.blank()
            block.lines.append(self.dimension(model, field, primary_key))

        for metric in model.measures:
            block.blank()
            block.lines.append(self.measure(model, metric))

        for column in model.borrowed:
            block.blank()
            borrowed = block.block(f"dimension {column}")
            borrowed.text("label", titleize(column))
            borrowed.property("type", quote(FALLBACK_TYPE_FOR_BORROWED))
            borrowed.flag("hidden", True)
            borrowed.heredoc("definition", "sql", interpolation.Source(column).to_aml())

        owner = data.get("owner") or self.root_stash.get("owner")
        if owner:
            block.blank()
            block.text("owner", owner)
        return amlgen.document(block, license_header=True)

    # -- dataset ----------------------------------------------------------

    def relationship(self, payload: dict[str, Any]) -> str:
        data = stash.read(payload)
        kind = data.get("type", "many_to_one")
        active = "true" if data.get("active", True) else "false"
        from_model = self.by_ossie_name.get(payload.get("from"))
        to_model = self.by_ossie_name.get(payload.get("to"))
        if from_model is None or to_model is None:
            missing = payload.get("from") if from_model is None else payload.get("to")
            raise ConversionError(
                f"relationship {payload.get('name')!r} names dataset {missing!r}, "
                f"which is not in this document"
            )
        name = payload.get("name")
        from_columns = payload.get("from_columns") or []
        to_columns = payload.get("to_columns") or []
        if not from_columns or len(from_columns) != len(to_columns):
            raise ConversionError(
                f"relationship {name!r} has {len(from_columns)} from_columns and "
                f"{len(to_columns)} to_columns. AML joins a column to a column, so the "
                f"two lists must be the same length and must not be empty"
            )

        extra = self._relationship_properties(data, name)
        where = data.get("where")
        if where is not None:
            return self._named_relationship(
                payload, kind, active, from_model, to_model,
                from_columns, to_columns, extra, where,
            )
        if len(from_columns) == 1 and kind in ("many_to_one", "one_to_one") and not extra:
            operator = ">" if kind == "many_to_one" else "-"
            return (
                f"relationship({from_model.reference}.{from_columns[0]} {operator} "
                f"{to_model.reference}.{to_columns[0]}, {active})"
            )

        config = Block("RelationshipConfig")
        rel = config.block("EqualityRelationship")
        rel.header = "rel: EqualityRelationship"
        rel.property("type", quote(kind))
        rel.property("from", _field_ref(from_model.reference, from_columns[0]))
        rel.property("to", _field_ref(to_model.reference, to_columns[0]))
        for from_column, to_column in zip(from_columns[1:], to_columns[1:]):
            on = rel.block("on")
            on.property("from", _field_ref(from_model.reference, from_column))
            on.property("to", _field_ref(to_model.reference, to_column))
        config.property("active", active)
        for key, value in extra:
            config.property(key, value)
        return config.render(2).lstrip()

    def _named_relationship(
        self,
        payload: dict[str, Any],
        kind: str,
        active: str,
        from_model: Model,
        to_model: Model,
        from_columns: list[str],
        to_columns: list[str],
        extra: list[tuple[str, str]],
        where: Any,
    ) -> str:
        """A relationship carrying a `where` filter, as a named declaration.

        A `where` block belongs to the relationship, and one written inline in
        `relationships:` has nowhere to put it. So the relationship is declared
        above the `Dataset` block under a name, and the list holds
        `RelationshipConfig { rel: <name>, ... }`.

        `Relationship` rather than `EqualityRelationship`, because it is the
        supertype and the compiler narrows it from `type`. The compiled payload
        comes back tagged `EqualityRelationship` either way.
        """
        scope = f"relationship {payload.get('name')}"
        rel_name = self.name_in_aml(str(payload.get("name")), scope)
        declaration = Block(f"Relationship {rel_name}")
        declaration.property("type", quote(kind))
        declaration.property("from", _field_ref(from_model.reference, from_columns[0]))
        declaration.property("to", _field_ref(to_model.reference, to_columns[0]))
        for from_column, to_column in zip(from_columns[1:], to_columns[1:]):
            on = declaration.block("on")
            on.property("from", _field_ref(from_model.reference, from_column))
            on.property("to", _field_ref(to_model.reference, to_column))
        self._write_where(declaration, where, scope)
        self.relationship_declarations.append(amlgen.document(declaration, license_header=False))

        config = Block("RelationshipConfig")
        config.property("rel", rel_name)
        config.property("active", active)
        for key, value in extra:
            config.property(key, value)
        return config.render(2).lstrip()

    def _write_where(self, block: Block, where: Any, scope: str) -> None:
        """The stashed `RelationshipFilter` as a `where { ... }` block.

        Each side is an AQL predicate narrowing the join on that end, and
        either may be absent. `comment_photo` in the Holistics AMQL test corpus
        carries only `from`.
        """
        if not isinstance(where, dict):
            raise ConversionError(
                f"{scope}: the stashed `where` is {type(where).__name__}, and AML writes "
                f"it as a block holding a `from` and a `to` predicate"
            )
        child = block.block("where")
        written = False
        for side in ("from", "to"):
            heredoc = where.get(side)
            if heredoc is None:
                continue
            if not isinstance(heredoc, dict) or "content" not in heredoc:
                raise ConversionError(
                    f"{scope}: the stashed `where.{side}` is not a heredoc object, so "
                    f"there is no predicate to write"
                )
            # Verbatim, including trailing space: the round trip compares the
            # compiled payload, and the compiler keeps what the heredoc held.
            child.heredoc(side, heredoc.get("name") or "aql", heredoc["content"])
            written = True
        if not written:
            raise ConversionError(
                f"{scope}: the stashed `where` names neither a `from` nor a `to` "
                f"predicate, so it narrows nothing"
            )

    def _relationship_properties(self, data: dict[str, Any], name: Any) -> list[tuple[str, str]]:
        """The stashed `RelationshipConfig` properties worth writing back.

        `direction`, `nullable` and `rlp_propagation` are all settable on a
        `RelationshipConfig`. Each carries a compiler default on every payload,
        so only a value that differs from it is written.
        """
        extra: list[tuple[str, str]] = []
        for key, default in RELATIONSHIP_DEFAULTS.items():
            value = data.get(key)
            if value is not None and value != default:
                extra.append((key, _aml_value(value, f"relationship {name}")))
        return extra

    def dataset_file(self) -> tuple[str, str]:
        name = self.dataset_name
        block = Block(f"Dataset {name}")
        block.text(
            "label",
            self.root_stash.get("label") or titleize(self.document.get("name") or name),
        )
        block.text("description", self.document.get("description"))
        block.text("data_source_name", self.data_source_name)
        block.blank()
        block.property("models", amlgen.array([m.reference for m in self.models]))
        block.blank()
        block.property(
            "relationships",
            amlgen.array(
                [self.relationship(r) for r in self.document.get("relationships") or []]
            ),
        )

        for model in self.models:
            for field in model.payload.get("fields") or []:
                if stash.read(field).get("scope") == "dataset":
                    block.blank()
                    block.lines.append(self.dataset_dimension(model, field))

        for metric in self.dataset_metrics:
            written = self.dataset_metric(metric)
            if written is not None:
                block.blank()
                block.lines.append(written)

        owner = self.root_stash.get("owner")
        if owner:
            block.blank()
            block.text("owner", owner)
        body = amlgen.document(block, license_header=True)
        if self.relationship_declarations:
            head, separator, rest = body.partition(f"Dataset {name} {{")
            body = head + "".join(self.relationship_declarations) + "\n" + separator + rest
        return f"{name}.dataset.aml", body

    def run(self) -> Result:
        # The dataset file is rendered first because its metrics decide which
        # columns each model has to borrow, and a model file has to carry those
        # as dimensions. The output order is unchanged: models, then dataset.
        dataset = self.dataset_file()
        files = [(m.path, self.model_file(m)) for m in self.models]
        files.append(dataset)
        return Result(files, self.issues)


def _field_ref(model_name: str, column: str) -> str:
    """`ref()` is aml-std's constructor for a FieldRef, and is how AML spells one."""
    return f"ref({quote(model_name)}, {quote(column)})"


def _aml_value(value: Any, where: str) -> str:
    """One stashed value as the AML source text that spells it.

    A type with no spelling here stops the run, because a body AML cannot parse
    fails later and further from its cause.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, list):
        return "[" + ", ".join(_aml_value(member, where) for member in value) + "]"
    raise ConversionError(
        f"{where}: the stash holds {type(value).__name__}, which this converter cannot "
        f"write as AML. Drop the key from custom_extensions, or add a spelling for it"
    )


def _write_stashed_object(block: Block, key: str, payload: dict[str, Any], where: str) -> None:
    """One stashed object as `key: <Type> { ... }`.

    `__type__` names the AML type, and every key the compiler added for its own
    bookkeeping starts and ends with `__`, so those are skipped. A nested object
    carries its own `__type__` and becomes a nested block under its own key.
    """
    kind = payload.get("__type__")
    if not kind:
        raise ConversionError(
            f"{where}: {key} is an object with no __type__, so there is no AML type "
            f"name to write it under"
        )
    child = block.block(f"{key}: {kind}")
    for name, value in payload.items():
        if name.startswith("__"):
            continue
        if isinstance(value, dict):
            _write_stashed_object(child, name, value, f"{where}.{name}")
            continue
        child.property(name, _aml_value(value, f"{where}.{name}"))


def _write_persistence(block: Block, persistence: dict[str, Any], where: str) -> None:
    """The stashed persistence object as its AML block."""
    payload = dict(persistence)
    payload.setdefault("__type__", "FullPersistence")
    _write_stashed_object(block, "persistence", payload, f"{where}.persistence")


def _models_referenced(query: str) -> set[str]:
    """Every model a query body names, so the AML `models:` list can be rebuilt.

    A `QueryModel` must declare the models its body references. The forward path
    carries the body verbatim, so the references are still in the text.
    """
    found = set()
    for pattern in ("model_relation", "model_columns"):
        for match in interpolation.QUERY_FORMS[pattern].finditer(query):
            inner = match.group(0).strip("{} ").lstrip("#")
            found.add(inner[:-2] if inner.endswith(".*") else inner)
    return found


def _looks_like_query(source: str) -> bool:
    """Whether an Ossie `source` holds a query rather than a table reference.

    Only reached for a document with no stashed `aml_type`, meaning one this
    converter did not write. `source` is a single string, so the shape is read
    from the text. This takes the permissive reading, counting a leading
    `SELECT` or `WITH`, or any whitespace outside a quoted identifier.
    """
    text = source.strip()
    if text[:6].upper() in ("SELECT",) or text[:4].upper() == "WITH":
        return True
    return any(character.isspace() for character in text.strip("`\"[]"))


def convert(
    document: Any, sql_dialect: str, data_source_name: str | None = None
) -> Result:
    """One Ossie semantic model to AML source files.

    `sql_dialect` names the warehouse the Holistics connection reads. Every
    `@sql` body written is in that dialect, rendered from a portable expression
    where the document carries no body in it already.

    `data_source_name` names the connection itself. AML requires it on a dataset
    and Ossie has no field for it, so a document this converter did not write
    needs it supplied.
    """
    if not isinstance(document, dict):
        raise ConversionError("the Ossie document is not a mapping")
    return _Reverse(document, data_source_name, sql_dialect).run()
