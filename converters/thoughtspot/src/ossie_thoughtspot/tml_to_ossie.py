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

"""Convert a ThoughtSpot Model column into an Ossie field.

A ThoughtSpot Model `columns[]` entry becomes an Ossie field when it declares
`column_type: ATTRIBUTE`. A `MEASURE` column belongs to a metric instead, converted
elsewhere — this module returns `None` for one rather than building a field that would
duplicate the metric conversion.

Four properties of the mapping are easy to get subtly wrong, because getting them wrong
still produces a document that imports and looks plausible.

The formula string carried in the THOUGHTSPOT dialect entry is the exact `expr` text from
the source document, untouched — never rebuilt from a parsed name/arguments shape. The
reverse direction reads this entry, and the two are compared by exact string equality, so
any reformatting here — even whitespace-only — breaks that comparison on the way back,
regardless of how well the rest of the conversion went.

A portable ANSI_SQL sibling is only ever added next to that verbatim entry, and only when
it can be produced with certainty rather than a guess: a bare column reference is the one
shape handled here. Anything else — a function call, an expression combining several
references, a reference this document's resolver cannot place — is left as
THOUGHTSPOT-only, with an issue recording why, rather than emitting a translation nobody
checked.

A field's identifier and its display label are two different values. `name` is a
normalised, portable identifier derived from the ThoughtSpot column's display name;
`label` carries that display name exactly as written. Writing the display name into
`name`, or the normalised form into `label`, silently breaks both.

Finally, a computed column is model-scoped in ThoughtSpot but has to live inside exactly
one dataset in Ossie. It is attributed to the dataset every one of its column references
resolves to. When those references disagree — two or more different datasets, one that
cannot be resolved at all, or none at all to go on — no dataset is obviously correct, so
none is guessed: the attribution fails, an issue records why, and the field is not built.
Preserving the formula for a caller's model-level stash is that caller's job from there —
this module only sees one column at a time and has no access to the enclosing document.

A `MEASURE` column becomes a metric instead of a field, built by `convert_metric` below.
Its one genuinely tricky rule is easy to get backwards in a way that still imports cleanly
and produces wrong numbers: the surfacing column's `aggregation` is load-bearing on a
`column_id` metric and on a *scalar*-formula metric (the two compose — `AGG(<scalar
expr>)`, never the bare scalar) but a no-op on a formula whose own outer call already
aggregates (`sum ( ... )`, `group_aggregate ( ... )`, ...) — a common, correct shape
ThoughtSpot's UI produces routinely, so discarding a redundant column aggregation there
is silent by design. Composing when the rule says no-op, or leaving bare when the rule
says compose, silently changes the grain the metric evaluates at while the model still
imports. There is a third, rarer case an outer-call check alone cannot see: an aggregate
*nested inside* a still-scalar outer call, as in `round ( sum ( ... ) , 2 )` — `round` is
not itself an aggregate, but the expression as a whole already is one. That case is the
one worth a warning, because it is the one shape where a reader might reasonably expect
composition and not get it. `_outer_call_is_aggregate` decides the first two cases;
`_contains_aggregate_call` — checked only once the outer call is not itself an aggregate —
decides the third. Both read ThoughtSpot's aggregate call names off the same expression
catalog `_compose_aggregate_entries` uses to build the composed rendering — one source for
every one of these jobs, so they cannot silently drift apart the way independently
hand-typed lists
could. And unlike a field, a metric has no `label`: when ID1 normalisation changes the
identifier, the exact display name has nowhere to go but the `custom_extensions` stash.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import datatypes, formula, identifiers, keys, stash
from .constants import (
    DATASET_STASH_ALIAS,
    DATASET_STASH_CONNECTION_NAME,
    DATASET_STASH_SOURCE_PARTS,
    DATASET_STASH_SOURCE_PARTS_DB,
    DATASET_STASH_SOURCE_PARTS_DB_TABLE,
    DATASET_STASH_SOURCE_PARTS_SCHEMA,
    DATASET_STASH_SQL_OUTPUT_COLUMNS,
    DATASET_STASH_SQL_QUERY,
    DATASET_STASH_TABLE_NAME,
    DATASET_STASH_TML_OBJECT,
    DATASET_STASH_UNSURFACED_COLUMNS,
    DIALECT,
    DOCUMENT_VERSION,
    FIELD_STASH_COLUMN_PROPERTIES,
    FIELD_STASH_DATA_TYPE,
    FIELD_STASH_DB_COLUMN_NAME,
    METRIC_SHAPE_COLUMN_AGGREGATION,
    METRIC_SHAPE_FORMULA,
    METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION,
    METRIC_STASH_SHAPE,
    MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS,
    MODEL_STASH_COLUMN_GROUPS,
    MODEL_STASH_CONSTRAINTS,
    MODEL_STASH_FILTERS,
    MODEL_STASH_LESSON_PLANS,
    MODEL_STASH_MODEL_JOINS_WITH,
    MODEL_STASH_MODEL_PROPERTIES,
    MODEL_STASH_PARAMETERS,
    MODEL_STASH_UNATTRIBUTED_FORMULAS,
    MODEL_STASH_UNREPRESENTABLE_JOINS,
    PORTABLE_DIALECT,
    RELATIONSHIP_STASH_CARDINALITY,
    RELATIONSHIP_STASH_JOIN_SHAPE,
    RELATIONSHIP_STASH_ON_EXPRESSION,
    RELATIONSHIP_STASH_REFERENCING_JOIN,
    RELATIONSHIP_STASH_RESIDUAL_PREDICATES,
    RELATIONSHIP_STASH_TYPE,
    STASH_TML_NAME,
)
from .errors import ConversionError
from .expressions import CATALOG, Variant, emit_direct
from .issues import IssueLog, Severity
from .tml import DocumentSet


def expression_entries(
    expr: str,
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
    *,
    object_ref: str,
    kind: str = "field",
) -> list[dict[str, str]]:
    """The dialect entries for one ThoughtSpot expression.

    The THOUGHTSPOT entry always comes first and always carries `expr` unmodified —
    whatever else this function decides, that entry is what makes the expression
    recoverable later, character for character. A second, ANSI_SQL entry is appended
    only when the whole expression is a bare column reference the resolver can place in
    a dataset. Every other shape — a runtime parameter, an unresolvable reference, a
    function call, a compound expression — gets an issue instead of a guessed
    translation, and only the verbatim entry is returned.

    `kind` names the Ossie object this expression belongs to ("field" or "metric") —
    used only in issue text, so a metric's non-portability issue reads "...evaluate
    this metric" rather than the field-shaped default. `object_ref` already carries
    this distinction (`field:...` vs `metric:...`); `kind` exists so the message
    itself agrees with it instead of contradicting it.
    """
    entries: list[dict[str, str]] = [{"dialect": DIALECT, "expression": expr}]

    parameters = formula.find_parameter_refs(expr)
    if parameters:
        log.add(
            code="TS-EXPR-PARAM",
            severity=Severity.WARNING,
            message=(
                f"expression references the ThoughtSpot runtime parameter(s) "
                f"{', '.join(parameters)}, which have no Ossie equivalent; no "
                f"portable expression is emitted"
            ),
            object_ref=object_ref,
        )
        return entries

    bare = formula.is_bare_column_ref(expr)
    if bare is not None:
        target = resolve(*bare)
        if target is None:
            log.add(
                code="TS-EXPR-UNRESOLVED",
                severity=Severity.WARNING,
                message=(
                    f"reference {identifiers.format_column_ref(*bare)} resolves to "
                    f"no dataset field; no portable expression is emitted"
                ),
                object_ref=object_ref,
            )
            return entries
        entries.append({"dialect": PORTABLE_DIALECT, "expression": target})
        return entries

    # Anything else is a function call or a multi-reference expression. Producing a
    # portable sibling for one would need an expression tree this converter does not
    # build — see the module docstring — so the non-portability is recorded instead
    # of guessed at.
    log.add(
        code="TS-EXPR-THOUGHTSPOT-ONLY",
        severity=Severity.INFO,
        message=(
            "expression is emitted in the THOUGHTSPOT dialect only; a consumer that "
            f"does not implement it will not be able to evaluate this {kind}"
        ),
        object_ref=object_ref,
    )
    return entries


def attribute_dataset(
    expr: str,
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> str | None:
    """The single dataset every column reference in `expr` resolves to, or `None`.

    A computed column has to be placed inside exactly one Ossie dataset. That is safe
    only when every reference in the expression agrees on the same one: no references
    at all is no evidence to place it by, a reference the resolver cannot place is
    missing evidence, and references landing in two or more datasets is contradictory
    evidence. Each of those returns `None` and logs why, rather than falling back to
    the first candidate found or any other default — a wrong guess here would silently
    move a field into the wrong dataset, or invent a home for one that references
    nothing at all.

    Runtime parameter references carry no dataset and take no part in this decision;
    an expression can be fully attributed while still not being portable, and
    `expression_entries` is what reports the latter.
    """
    refs = formula.find_column_refs(expr)
    if not refs:
        log.add(
            code="TS-FIELD-NO-REFERENCES",
            severity=Severity.WARNING,
            message=(
                "expression contains no column references, so it cannot be "
                "attributed to a dataset"
            ),
            object_ref=object_ref,
        )
        return None

    datasets: list[str] = []
    unresolved: list[str] = []
    for table, column in refs:
        target = resolve(table, column)
        if target is None:
            unresolved.append(identifiers.format_column_ref(table, column))
            continue
        dataset = target.split(".", 1)[0]
        if dataset not in datasets:
            datasets.append(dataset)

    if unresolved:
        log.add(
            code="TS-FIELD-UNRESOLVED-REFERENCE",
            severity=Severity.WARNING,
            message=(
                f"reference(s) {', '.join(unresolved)} resolve to no dataset field; "
                f"the expression cannot be attributed with confidence"
            ),
            object_ref=object_ref,
        )
        return None

    if len(datasets) > 1:
        log.add(
            code="TS-FIELD-UNATTRIBUTED",
            severity=Severity.WARNING,
            message=(
                f"references resolve to {len(datasets)} different datasets "
                f"({', '.join(datasets)}); a computed field spanning more than one "
                f"dataset cannot be attributed, and should be preserved as an "
                f"unattributed formula rather than emitted as a field"
            ),
            object_ref=object_ref,
        )
        return None

    return datasets[0]


def _physical_datatype(
    table_name: str,
    column_name: str,
    table_lookup: Callable[[str], dict | None],
    log: IssueLog,
    *,
    object_ref: str,
    kind: str = "field",
) -> str | None:
    """The Ossie datatype for a physical column, or `None` when it cannot be found.

    Matched by the physical column's own display name — what a Model `column_id`
    suffix names — not by its warehouse column name.

    `None` covers two different situations, and only one of them is a loss worth
    logging. A column with no `data_type` at all has nothing to drop — `datatype`
    is optional in Ossie, and `datatypes.to_ossie` documents omission as a
    legitimate answer, so this stays silent. A column whose `data_type` *is*
    present but unrecognised by `datatypes.to_ossie` is different: the warehouse
    told us the type and it is about to be dropped on the floor, so that case
    logs an issue naming the type before returning `None`.

    `kind` ("field" or "metric") names the Ossie object being built, both in the
    issue code (`TS-FIELD-...` vs `TS-METRIC-...`) and in the message text, so a
    metric calling this does not raise a `TS-FIELD-*` code or say "field" about
    itself — `object_ref` already says `metric:...`, and the code and message
    need to agree with it.
    """
    code_prefix = f"TS-{kind.upper()}"
    table = table_lookup(table_name)
    physical = None
    if table is not None:
        for candidate in table.get("columns", []):
            if candidate.get("name") == column_name:
                physical = candidate
                break
    if physical is None:
        log.add(
            code=f"{code_prefix}-PHYSICAL-COLUMN-MISSING",
            severity=Severity.WARNING,
            message=(
                f"physical column {column_name!r} was not found on table "
                f"{table_name!r}; no datatype is emitted for this {kind}"
            ),
            object_ref=object_ref,
        )
        return None
    data_type = (physical.get("db_column_properties") or {}).get("data_type")
    if data_type is None:
        return None
    ossie_type = datatypes.to_ossie(data_type)
    if ossie_type is None:
        log.add(
            code=f"{code_prefix}-DATATYPE-UNMAPPED",
            severity=Severity.WARNING,
            message=(
                f"physical column {column_name!r} on table {table_name!r} has "
                f"warehouse data_type {data_type!r}, which has no Ossie "
                f"equivalent; no datatype is emitted for this {kind}"
            ),
            object_ref=object_ref,
        )
    return ossie_type


def _ai_context(properties: dict) -> dict | str | None:
    """Fold synonyms and free-text AI context into one Ossie `ai_context` value.

    Synonyms need the object form to have somewhere to live; free-text context on its
    own stays a bare string, the simpler of the two shapes Ossie accepts.
    """
    synonyms = properties.get("synonyms")
    instructions = properties.get("ai_context")
    if synonyms and instructions:
        return {"synonyms": list(synonyms), "instructions": instructions}
    if synonyms:
        return {"synonyms": list(synonyms)}
    if instructions:
        return instructions
    return None


def convert_field(
    column: dict,
    formulas: dict[str, dict],
    table_lookup: Callable[[str], dict | None],
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
) -> dict | None:
    """Convert one Model `columns[]` entry into an Ossie field, or `None`.

    `column` is a ThoughtSpot Model `columns[]` entry. A physical column carries
    `column_id` (`TABLE::Column Name`); a computed column carries `formula_id`
    instead, naming an entry in the model's `formulas[]` list. `formulas` is that
    list reshaped into a lookup keyed by each entry's `id`, value the whole entry,
    so `formulas[column["formula_id"]]["expr"]` is the expression text — this
    function reads the expression from there, never from the column itself. A
    `formula_id` absent from `formulas`, a column with neither key, or a
    `column_type` that is not `ATTRIBUTE`, produces no field.
    """
    properties = column.get("properties") or {}
    if properties.get("column_type") != "ATTRIBUTE":
        return None

    display_name = column["name"]
    object_ref = f"field:{display_name}"
    field: dict = {"name": identifiers.normalise(display_name), "label": display_name}

    if "column_id" in column:
        table_name, column_name = identifiers.split_column_ref(f"[{column['column_id']}]")
        expr = identifiers.format_column_ref(table_name, column_name)
        field["expression"] = {
            "dialects": expression_entries(expr, resolve, log, object_ref=object_ref)
        }
        datatype = _physical_datatype(
            table_name, column_name, table_lookup, log, object_ref=object_ref
        )
        if datatype is not None:
            field["datatype"] = datatype
    elif "formula_id" in column:
        formula_id = column["formula_id"]
        formula_entry = formulas.get(formula_id)
        if formula_entry is None:
            log.add(
                code="TS-FIELD-FORMULA-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r} has formula_id {formula_id!r}, which "
                    f"matches no formulas[] entry; no field can be built"
                ),
                object_ref=object_ref,
            )
            return None
        if "expr" not in formula_entry:
            log.add(
                code="TS-FIELD-FORMULA-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r} has formula_id {formula_id!r}, whose "
                    f"formulas[] entry has no expr; no field can be built"
                ),
                object_ref=object_ref,
            )
            return None
        expr = formula_entry["expr"]
        dataset = attribute_dataset(expr, resolve, log, object_ref=object_ref)
        if dataset is None:
            return None
        field["expression"] = {
            "dialects": expression_entries(expr, resolve, log, object_ref=object_ref)
        }
    else:
        log.add(
            code="TS-FIELD-NO-SOURCE",
            severity=Severity.WARNING,
            message=(
                "column has neither a physical column_id nor a formula_id; "
                "no field can be built"
            ),
            object_ref=object_ref,
        )
        return None

    description = column.get("description")
    if description:
        field["description"] = description

    ai_context = _ai_context(properties)
    if ai_context is not None:
        field["ai_context"] = ai_context

    return field


#: TML column aggregation -> the Ossie aggregate applied to the column expression.
#: `NONE` means the column carries no aggregate at all, which is distinct from absent.
_AGGREGATION = {
    "SUM": "SUM", "COUNT": "COUNT", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX",
    "COUNT_DISTINCT": "COUNT_DISTINCT", "STD_DEVIATION": "STDDEV", "VARIANCE": "VARIANCE",
    "NONE": None,
}

#: Aggregations that always report themselves as Integer, regardless of the underlying
#: physical column's own warehouse type — a COUNT of DOUBLEs is still a whole number
#: of rows.
_COUNT_AGGREGATIONS = frozenset({"COUNT", "COUNT_DISTINCT"})

#: The three TML shapes a metric can arrive as (the stash's `shape` key), so a
#: return trip can reproduce the source shape instead of collapsing every metric
#: into the same one. The values themselves live in constants.py
#: (METRIC_SHAPE_*) — shared with ossie_to_thoughtspot.py, the reader.
#: `METRIC_SHAPE_FORMULA` is also what a document with no stash at all defaults
#: to on the way back — a plain formulas[] entry, aggregate already baked into
#: its expr — so it is the one value never worth writing to the stash: writing
#: it or omitting it produces the same reconstruction either way.

#: TML column aggregation -> the catalog `spec_name` whose DIRECT template is
#: ThoughtSpot's own native rendering of that aggregate (`"sum ( {0} )"`,
#: `"unique count ( {0} )"`, ...). Reused for two different jobs: composing the
#: THOUGHTSPOT dialect entry for a load-bearing aggregation (`_compose_aggregate_entries`)
#: and — via `_AGGREGATE_CALL_NAMES` below — recognising when a formula's own outer call
#: is *already* one of these. Both jobs read the same catalog rows, so "what do we
#: render" and "is this already rendered" cannot silently disagree the way two
#: independently hand-typed lists could.
_AGGREGATION_CATALOG_SPEC = {
    "SUM": "SUM(expr)", "COUNT": "COUNT(expr)", "AVERAGE": "AVG(expr)",
    "MIN": "MIN(expr)", "MAX": "MAX(expr)", "COUNT_DISTINCT": "COUNT(DISTINCT expr)",
    "STD_DEVIATION": "STDDEV(expr)", "VARIANCE": "VARIANCE(expr)",
}

#: MEDIAN(expr) has no TML `aggregation` enum counterpart at all, but `median ( ... )`
#: is a genuine native ThoughtSpot aggregate and must still be recognised as one when
#: it appears in an expression — see `_contains_aggregate_call`.
_NATIVE_AGGREGATE_SPECS = (*_AGGREGATION_CATALOG_SPEC.values(), "MEDIAN(expr)")

#: `group_aggregate` is ThoughtSpot's own construct for a grouped/windowed
#: aggregation — the *performant* pattern the catalog's window-function rows
#: prefer over a raw `sql_*_aggregate_op` pass-through — and it is not a target of
#: any TML `aggregation` enum value, so it cannot come from `_AGGREGATION_CATALOG_SPEC`.
#: There is exactly one such construct, so it is named directly rather than derived.
_GROUP_AGGREGATE_CALL = "group_aggregate"

#: Every `Variant` that denotes an *aggregate* `sql_*_op` pass-through wrapper,
#: derived by filtering the enum on its own `_aggregate_op` naming convention
#: rather than listing `sql_int_aggregate_op` / `sql_number_aggregate_op` by hand,
#: so a future aggregate variant is covered the moment it is added to `_types.py`
#: without a second edit here.
_SQL_AGGREGATE_OP_CALLS = frozenset(
    variant.value for variant in Variant if variant.value.endswith("_aggregate_op")
)

#: Every ThoughtSpot call name that already aggregates: the native DIRECT catalog
#: templates (derived from the catalog itself, never retyped by hand, via the same
#: `emit_direct` the rest of this package uses to render them — see the task report
#: for why), plus `group_aggregate` and the `sql_*_aggregate_op` pass-through family.
#: This set alone is not the whole safety story — see `_contains_aggregate_call`,
#: which also scans for these names at *any* nesting depth, not only as an
#: expression's own outer call, because the catalog will always hold aggregate
#: constructs beyond whatever a fixed enumeration lists.
_AGGREGATE_CALL_NAMES = (
    frozenset(
        formula.split_call(emit_direct(CATALOG[spec], ["x"]))[0].lower()
        for spec in _NATIVE_AGGREGATE_SPECS
    )
    | {_GROUP_AGGREGATE_CALL}
    | _SQL_AGGREGATE_OP_CALLS
)


def _outer_call_is_aggregate(expr: str) -> bool:
    """Whether `expr`'s own outer call (not something nested inside it) is a
    native ThoughtSpot aggregate.

    `formula.split_call` returning `None` — not a single outer call, as in
    `[A::x] - [B::y]` — means there is no outer call for it to be one. Matching
    is case-insensitive (ThoughtSpot's formula functions are not case-sensitive)
    and compares the whole call name as one unit, so a two-word name like
    `unique count` is matched by both words together, never by either alone.

    This is the *documented no-op* case: `sum ( [T::x] )` with a column
    aggregation of `SUM`, `AVERAGE`, or anything else is a real, common shape —
    ThoughtSpot's UI sets an aggregation on a formula column routinely, whether
    or not the formula's own expression already aggregates — and discarding a
    redundant one here is expected behaviour, not a loss. See `convert_metric`
    for why this case stays silent while `_contains_aggregate_call` below (an
    aggregate *nested inside*, not as the outer call) is reported.
    """
    call = formula.split_call(expr)
    if call is None:
        return False
    name, _args = call
    return name.lower() in _AGGREGATE_CALL_NAMES


def _contains_aggregate_call(expr: str) -> bool:
    """Whether an aggregate call appears anywhere in `expr`, at any nesting depth.

    Checking only `expr`'s own outer call (`_outer_call_is_aggregate`) is not
    enough: an aggregate can be buried inside a scalar wrapper the outer call
    does not name at all — `round ( sum ( [T::x] ) , 2 )` has `round` as its
    outer call, not `sum`, but the expression as a whole still aggregates.
    `formula.find_call_names` finds every call at every depth, so this checks
    the whole expression rather than the single outer position. Matching is
    case-insensitive and compares each call's whole name, exactly as
    `_outer_call_is_aggregate` does.

    Used only for the case `_outer_call_is_aggregate` already says `False` for:
    see `convert_metric`, where an aggregate nested here (but not as the outer
    call) is the one shape worth a warning — the outer-call case is silent by
    design, and warning there too would fire on the common, correct case and
    train readers to ignore the issue log.
    """
    return any(name.lower() in _AGGREGATE_CALL_NAMES for name in formula.find_call_names(expr))


def _compose_aggregate_entries(
    inner_expr: str,
    aggregation_raw: str,
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> list[dict[str, str]]:
    """Dialect entries for a load-bearing column aggregation wrapping `inner_expr`.

    `inner_expr` is `[TABLE::Column]` for a physical column, or the verbatim scalar
    `formulas[].expr` text for a formula-backed one — in both cases the text the
    column-level `aggregation` rolls up. There is no single TML string that already
    represents "this column plus its aggregation": TML records the two as separate
    values (the Metric-level `aggregation` row in the construct-mapping document), so
    — unlike `expression_entries` — building the THOUGHTSPOT entry here is a genuine
    construction, not a reconstruction of something that already existed as one
    string. `inner_expr` itself still travels through untouched, inside the wrapper
    `emit_direct` builds around it.

    The portable ANSI_SQL sibling is composed the same way, but only when
    `inner_expr` itself produces one via `expression_entries` — wrapping a guess
    around a non-portable inner expression would be exactly the kind of invented
    translation this converter otherwise refuses to emit, and `expression_entries`
    already logs why it can't when that happens.
    """
    construct = CATALOG[_AGGREGATION_CATALOG_SPEC[aggregation_raw]]
    ts_expr = emit_direct(construct, [inner_expr])
    entries: list[dict[str, str]] = [{"dialect": DIALECT, "expression": ts_expr}]

    # This helper only ever composes a metric's aggregation (never a field's), so
    # "metric" is hardcoded here rather than threaded through as a parameter.
    inner_entries = expression_entries(
        inner_expr, resolve, log, object_ref=object_ref, kind="metric"
    )
    inner_ansi = next(
        (e["expression"] for e in inner_entries if e["dialect"] == PORTABLE_DIALECT), None
    )
    if inner_ansi is not None:
        if aggregation_raw == "COUNT_DISTINCT":
            ansi_expr = f"COUNT(DISTINCT {inner_ansi})"
        else:
            ansi_expr = f"{_AGGREGATION[aggregation_raw]}({inner_ansi})"
        entries.append({"dialect": PORTABLE_DIALECT, "expression": ansi_expr})
    return entries


def _metric_datatype(
    table_name: str,
    column_name: str,
    aggregation_raw: str,
    table_lookup: Callable[[str], dict | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> str | None:
    """The Ossie datatype for a bare-aggregate-over-physical-column metric, or `None`.

    `COUNT` and `COUNT(DISTINCT ...)` always report `Integer`, regardless of the
    underlying column's own warehouse type — counting DOUBLEs still counts whole
    rows. Every other aggregation (including `NONE`, a bare unaggregated column)
    reports the physical column's own mapped type, via the same `_physical_datatype`
    a field uses, so the absent-vs-unmapped distinction it already makes (nothing to
    log for a column with no declared type; an issue for one whose declared type has
    no Ossie mapping) applies here unchanged.
    """
    if aggregation_raw in _COUNT_AGGREGATIONS:
        return "Integer"
    return _physical_datatype(
        table_name, column_name, table_lookup, log, object_ref=object_ref, kind="metric"
    )


def convert_metric(
    column: dict,
    formulas: dict[str, dict],
    table_lookup: Callable[[str], dict | None],
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
) -> dict | None:
    """Convert one Model `columns[]` entry into an Ossie metric, or `None`.

    `column` is a ThoughtSpot Model `columns[]` entry; only `column_type: MEASURE`
    becomes a metric — an `ATTRIBUTE` column belongs to `convert_field` instead, and
    building a metric for one here would produce two competing Ossie objects
    surfacing the same TML column.

    As with `convert_field`, a physical column carries `column_id` (`TABLE::Column
    Name`) and a computed one carries `formula_id`, resolved against `formulas`
    (keyed by each entry's `id`) exactly the same way. Either shape composes with
    `properties.aggregation` per the Metric-level `aggregation` row: load-bearing on
    a `column_id` metric and on a *scalar* formula (the two compose into
    `AGG(<scalar expr>)`). Two different shapes are a no-op instead, and only one
    of them is reported:

    - The formula's own outer call already aggregates (`sum ( ... )`,
      `group_aggregate ( ... )`, ...). This is the documented, common case —
      ThoughtSpot's UI sets a column aggregation on a formula column routinely,
      redundant or not — so the column-level value is discarded silently, without
      logging anything. A warning here would fire on a large fraction of ordinary,
      correct metrics and teach readers to stop reading the issue log.
    - An aggregate is *nested* inside a still-scalar outer call
      (`round ( sum ( ... ) , 2 )` — `round` is scalar, `sum` is buried one level
      in). Composing here would silently double-aggregate an already-reduced
      value, exactly as the first case would, but this is the one shape where a
      reader might reasonably expect composition and not get it — so it is
      reported.

    See `_outer_call_is_aggregate` and `_contains_aggregate_call` for how the two
    are told apart, and the module docstring for why detection and composition
    share one source.

    An unrecognised `aggregation` value (not one of TML's documented enum members)
    is treated as `NONE` and logged — the value was present and could not be
    understood, which is a loss worth reporting, unlike an absent `aggregation` key,
    which defaults to `NONE` silently.

    Metrics have no `label` field (unlike fields): when ID1 normalisation changes
    the identifier, the exact ThoughtSpot display name is stashed as `tml_name`
    rather than carried in a dedicated field.

    Which of the three TML shapes produced this metric — `column_aggregation`,
    `scalar_formula_plus_aggregation`, or `formula` — is stashed as `shape`, so a
    return trip can reproduce the source shape instead of collapsing all three
    into one. `formula` is omitted rather than written: it is also what a
    document with no stash defaults to on the way back, so writing it would
    change nothing about the reconstruction while making the payload heavier.
    """
    properties = column.get("properties") or {}
    if properties.get("column_type") != "MEASURE":
        return None

    display_name = column["name"]
    object_ref = f"metric:{display_name}"

    aggregation_raw = properties.get("aggregation", "NONE")
    if aggregation_raw not in _AGGREGATION:
        log.add(
            code="TS-METRIC-AGGREGATION-UNKNOWN",
            severity=Severity.WARNING,
            message=(
                f"column {display_name!r} has aggregation {aggregation_raw!r}, which "
                f"is not one of the TML aggregation values this converter recognises; "
                f"treated as NONE"
            ),
            object_ref=object_ref,
        )
        aggregation_raw = "NONE"
    aggregation = _AGGREGATION[aggregation_raw]

    normalised_name = identifiers.normalise(display_name)
    metric: dict = {"name": normalised_name}

    if "column_id" in column:
        metric_shape = METRIC_SHAPE_COLUMN_AGGREGATION
        table_name, column_name = identifiers.split_column_ref(f"[{column['column_id']}]")
        field_ref = identifiers.format_column_ref(table_name, column_name)
        if aggregation is None:
            dialects = expression_entries(
                field_ref, resolve, log, object_ref=object_ref, kind="metric"
            )
        else:
            dialects = _compose_aggregate_entries(
                field_ref, aggregation_raw, resolve, log, object_ref=object_ref
            )
        metric["expression"] = {"dialects": dialects}
        datatype = _metric_datatype(
            table_name, column_name, aggregation_raw, table_lookup, log, object_ref=object_ref
        )
        if datatype is not None:
            metric["datatype"] = datatype
    elif "formula_id" in column:
        formula_id = column["formula_id"]
        formula_entry = formulas.get(formula_id)
        if formula_entry is None:
            log.add(
                code="TS-METRIC-FORMULA-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r} has formula_id {formula_id!r}, which "
                    f"matches no formulas[] entry; no metric can be built"
                ),
                object_ref=object_ref,
            )
            return None
        if "expr" not in formula_entry:
            log.add(
                code="TS-METRIC-FORMULA-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r} has formula_id {formula_id!r}, whose "
                    f"formulas[] entry has no expr; no metric can be built"
                ),
                object_ref=object_ref,
            )
            return None
        expr = formula_entry["expr"]
        if aggregation is None:
            # Nothing to compose: the verbatim expr, untouched, is the whole metric.
            metric_shape = METRIC_SHAPE_FORMULA
            dialects = expression_entries(
                expr, resolve, log, object_ref=object_ref, kind="metric"
            )
        elif _outer_call_is_aggregate(expr):
            # The documented no-op: the expression's own call already
            # aggregates (sum ( ... ), group_aggregate ( ... ), ...), and
            # ThoughtSpot's UI sets a column aggregation on a formula column
            # like this routinely, whether or not it is redundant. Discarding
            # it here is expected, not a loss, so nothing is logged --
            # warning on this common, correct shape would train readers to
            # ignore the issue log entirely.
            metric_shape = METRIC_SHAPE_FORMULA
            dialects = expression_entries(
                expr, resolve, log, object_ref=object_ref, kind="metric"
            )
        elif _contains_aggregate_call(expr):
            # Not the outer call, but an aggregate is nested somewhere inside
            # (round ( sum ( ... ) , 2 ), or a sql_*_aggregate_op pass-through
            # buried in a larger expression). This is the one shape where a
            # reader might reasonably expect composition and not get it, so
            # it is the one shape worth telling them about: composing here
            # would silently double-aggregate an already-reduced value.
            log.add(
                code="TS-METRIC-AGGREGATION-ALREADY-AGGREGATED",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r}'s expression already contains an "
                    f"aggregate; the column-level aggregation {aggregation_raw!r} "
                    f"was ignored to avoid double-aggregating"
                ),
                object_ref=object_ref,
            )
            metric_shape = METRIC_SHAPE_FORMULA
            dialects = expression_entries(
                expr, resolve, log, object_ref=object_ref, kind="metric"
            )
        else:
            # A genuinely scalar expr: the column aggregation is load-bearing, so
            # compose it.
            metric_shape = METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION
            dialects = _compose_aggregate_entries(
                expr, aggregation_raw, resolve, log, object_ref=object_ref
            )
        metric["expression"] = {"dialects": dialects}
        # A formula carries no declared type anywhere in TML — neither columns[] nor
        # formulas[] has a data_type key (rule X9) — so datatype is always omitted
        # here, and never logged: there was never a value here to lose.
    else:
        log.add(
            code="TS-METRIC-NO-SOURCE",
            severity=Severity.WARNING,
            message=(
                "column has neither a physical column_id nor a formula_id; "
                "no metric can be built"
            ),
            object_ref=object_ref,
        )
        return None

    stash_payload: dict = {}
    if normalised_name != display_name:
        stash_payload[STASH_TML_NAME] = display_name
    if metric_shape != METRIC_SHAPE_FORMULA:
        stash_payload[METRIC_STASH_SHAPE] = metric_shape
    metric = _write_stash_safely(metric, stash_payload, log, object_ref)

    description = column.get("description")
    if description:
        metric["description"] = description

    ai_context = _ai_context(properties)
    if ai_context is not None:
        metric["ai_context"] = ai_context

    return metric


# ---------------------------------------------------------------------------
# Assembly: datasets, the cross-model resolver, relationships, and convert()
# ---------------------------------------------------------------------------
#
# Everything above converts one column at a time and takes `resolve` as a
# given. Nothing above can build `resolve` itself -- it maps a raw TML
# reference to "dataset.field", and that mapping cannot exist until every
# dataset in the model is known. That is the one job only this section can
# do, and everything else here exists to support it: datasets have to be
# built first (so their names -- the model_tables[] alias-or-name, never
# normalised -- are known), then `resolve` is a closure over that, then
# fields and metrics are converted through it, then joins become
# relationships, then keys are derived from the relationships that qualify.
#
# A malformed reference anywhere in a TML document (an ambiguous `column_id`
# or join condition -- `identifiers.split_column_ref` raising on purpose
# rather than mis-splitting) is caught per object: the object is skipped, an
# issue names it, and the rest of the model still converts. Nothing here lets
# one bad reference abort the whole conversion.


def _index_attribute_columns(
    columns: list[dict], log: IssueLog
) -> dict[tuple[str, str], str]:
    """`(TABLE, physical column display name) -> Ossie field identifier`, for
    every ATTRIBUTE `columns[]` entry bound to a physical `column_id`.

    This is the data `resolve()` is built from: a bare `[TABLE::Column]`
    reference is portable only when it names a column the model actually
    surfaces as a field, and the identifier it resolves to has to be the
    exact one `convert_field` independently computes for that same column --
    plain `identifiers.normalise`, not run through an `identifiers.Allocator`.
    Neither `convert_field` nor `convert_metric` resolve ID2 collisions
    (display-name folds that only clash after normalisation) themselves; this
    index deliberately matches that rather than silently picking a different,
    collision-safe name `resolve()` would return but the built field would
    not actually have. See the module docstring's identifier note in the
    task report for why closing that gap here is out of scope.

    A malformed `column_id` is caught here, per column, rather than aborting
    the whole model: the column is left out of the index -- any expression
    that references it resolves to nothing, which every caller already
    treats as an ordinary unresolved reference -- and an issue names it.
    """
    index: dict[tuple[str, str], str] = {}
    for column in columns:
        properties = column.get("properties") or {}
        if properties.get("column_type") != "ATTRIBUTE":
            continue
        column_id = column.get("column_id")
        if not column_id:
            continue
        try:
            table_name, physical_name = identifiers.split_column_ref(f"[{column_id}]")
        except ValueError as exc:
            log.add(
                code="TS-COLUMN-ID-MALFORMED",
                severity=Severity.WARNING,
                message=(
                    f"column {column.get('name', '<unnamed>')!r} has a malformed "
                    f"column_id {column_id!r} ({exc}); it cannot be resolved by any "
                    f"expression that references it"
                ),
                object_ref=f"field:{column.get('name', '<unnamed>')}",
            )
            continue
        index[(table_name, physical_name)] = identifiers.normalise(column["name"])
    return index


def _referenced_physical_columns(model_columns: list[dict]) -> set[tuple[str, str]]:
    """Every `(TABLE, physical column display name)` pair some Model
    `columns[]` entry's `column_id` names -- ATTRIBUTE and MEASURE alike.

    This is broader than `_index_attribute_columns` on purpose: a
    `column_aggregation`-shape metric surfaces its physical column just as
    much as an ATTRIBUTE field does, so both count as "surfaced" for the
    Dataset-level `unsurfaced_columns` question this feeds -- a physical
    column referenced only by a metric is still part of the semantic model,
    just not as a field. A malformed `column_id` is skipped silently here
    rather than logged again: the field/metric conversion loop already logs
    it once, from the same source data, and a second identical issue would
    only be noise.
    """
    referenced: set[tuple[str, str]] = set()
    for column in model_columns:
        column_id = column.get("column_id")
        if not column_id:
            continue
        try:
            table_name, physical_name = identifiers.split_column_ref(f"[{column_id}]")
        except ValueError:
            continue
        referenced.add((table_name, physical_name))
    return referenced


def _raw_physical_columns(body: dict, kind: str) -> list[dict]:
    """The verbatim physical-column list for a Table or SQL View document --
    `columns[]` for a `table:`, `sql_view_columns[]` for a `sql_view:`.

    Per the mapping document's SQL View row, a SQL View's columns live under
    a different key entirely, not merely a differently-shaped entry under
    the same one -- reading `.get("columns")` unconditionally finds nothing
    on a SQL View document and every one of its columns silently vanishes
    (no datatype, no unsurfaced_columns entry, nothing). This is the single
    place that knows which key each kind uses; every reader of "this
    dataset's physical columns" goes through here or through
    `_normalized_physical_columns` below, never `body.get("columns")` directly.
    """
    key = "sql_view_columns" if kind == "sql_view" else "columns"
    return body.get(key) or []


def _normalize_physical_column(entry: dict, kind: str) -> dict:
    """One physical column entry, reshaped so datatype lookup
    (`_physical_datatype` in the field/metric converters) can read `name` /
    `db_column_name` / `db_column_properties` the same way regardless of
    which document kind it came from.

    A Table column already has exactly this shape. A SQL View column binds
    its physical reference via `sql_output_column` instead of
    `db_column_name` -- "each bound to a query output alias via
    sql_output_column", per the mapping document -- but is otherwise
    documented as playing the same role, so `name` and
    `db_column_properties` carry over unchanged.
    """
    if kind != "sql_view":
        return entry
    return {
        "name": entry.get("name"),
        "db_column_name": entry.get("sql_output_column"),
        "db_column_properties": entry.get("db_column_properties"),
    }


def _normalized_physical_columns(body: dict, kind: str) -> list[dict]:
    """`_raw_physical_columns`, each entry passed through
    `_normalize_physical_column` -- the shape `table_lookup` hands to
    `_physical_datatype`."""
    return [_normalize_physical_column(entry, kind) for entry in _raw_physical_columns(body, kind)]


#: The TML `db_column_properties.data_type` spelling `datatypes.to_tml` would
#: emit by default for each Ossie datatype whose TML source has more than one
#: valid spelling (the datatype map's Boolean and Float rows). Stashing the
#: canonical spelling itself would be noise -- the reverse direction's own
#: default already produces it; only the non-canonical spelling (`BOOL`,
#: `FLOAT`) is worth recording.
_CANONICAL_TML_SPELLING = {"Boolean": "BOOLEAN", "Float": "DOUBLE"}


def _physical_column_stash(
    column_id: str,
    ossie_datatype: str | None,
    physical_columns_by_prefix: dict[str, list[dict]],
    dataset_stashes: dict[str, dict],
) -> dict:
    """Field/metric-level stash additions a physical column needs that
    nothing else in this module records:

    * `data_type` -- the exact warehouse spelling, only when it is not the
      canonical one `datatypes.to_tml` would emit by default for
      `ossie_datatype` (see `_CANONICAL_TML_SPELLING`). Documented in the
      datatype map's Boolean row: "the connection's spelling is recorded in
      the field stash's data_type key so the return trip re-emits the same
      one" -- the same reasoning applies to Float's DOUBLE/FLOAT pair.

    * `db_column_name` -- the exact warehouse column name, only when it
      differs from the column's own display name. The forward direction
      matches a physical column by display name only, so a round-tripped
      bracket reference (`[TABLE::Column]`) carries the display name, never
      the warehouse name, and the reverse direction has no other way to
      recover it -- today it defaults to assuming the two are equal and
      logs that assumption. This key is not in the pinned payload schema;
      it closes a gap the schema itself does not yet cover. Only ever
      stashed for a Table-backed column: a SQL View's own physical binding
      (`sql_output_column`) already has its own dataset-level stash key
      (`sql_output_columns`), so recording the same fact again here under a
      different name would be redundant.
    """
    try:
        table_name, physical_name = identifiers.split_column_ref(f"[{column_id}]")
    except ValueError:
        return {}
    physical = next(
        (p for p in physical_columns_by_prefix.get(table_name, []) if p.get("name") == physical_name),
        None,
    )
    if physical is None:
        return {}

    payload: dict = {}
    is_table = dataset_stashes.get(table_name, {}).get(DATASET_STASH_TML_OBJECT) != "sql_view"
    db_column_name = physical.get("db_column_name")
    if is_table and db_column_name is not None and db_column_name != physical_name:
        payload[FIELD_STASH_DB_COLUMN_NAME] = db_column_name

    raw_data_type = (physical.get("db_column_properties") or {}).get("data_type")
    canonical = _CANONICAL_TML_SPELLING.get(ossie_datatype) if ossie_datatype else None
    if raw_data_type is not None and canonical is not None and raw_data_type != canonical:
        payload[FIELD_STASH_DATA_TYPE] = raw_data_type

    return payload


#: Every `properties` key `convert_field` reads on the ATTRIBUTE path.
#: Anything else in a column's `properties` dict is unconsumed and, per the
#: fail-closed rule `_unconsumed_properties` implements, is stashed rather
#: than silently dropped.
_FIELD_CONSUMED_PROPERTIES = frozenset({"column_type", "synonyms", "ai_context"})

#: Same, for `convert_metric`'s MEASURE path -- one key more than the field
#: set: `aggregation` is load-bearing only for a metric.
_METRIC_CONSUMED_PROPERTIES = _FIELD_CONSUMED_PROPERTIES | {"aggregation"}


#: Identity-shaped keys that must never reach the portable document at any
#: depth -- broader than `stash._FORBIDDEN_KEYS` (X8's own `guid`/`obj_id`/
#: `fqn`, which `stash.write_stash` scans every payload for regardless of
#: caller). `_unconsumed_properties` is the one place in this module that
#: copies a property's *value* wholesale rather than rebuilding it field by
#: field, so it is also the one place the two further identity keys the
#: mapping document's NM1 names -- `dataset_id`, and `geo_config.
#: custom_file_guid` naming a custom map -- are worth checking for
#: specifically, ahead of `write_stash`'s own narrower check: the scan is
#: `stash.find_forbidden_key`'s, shared rather than reimplemented here, only
#: the wider vocabulary to check it against is local to this one call site.
_DEEP_IDENTITY_KEYS = stash._FORBIDDEN_KEYS | {"dataset_id", "custom_file_guid"}


def _unconsumed_properties(
    properties: dict, consumed: frozenset[str], log: IssueLog, object_ref: str
) -> dict:
    """Every key in a column's `properties` dict that the converter did not
    read, minus anything carrying instance-local identity (rule X8) at any
    depth.

    Deliberately the complement of `consumed`, not an enumeration of the
    ThoughtSpot-only property names this module happens to know about today
    (`index_type`, `value_casing`, ...): an enumeration silently drops the
    next property ThoughtSpot adds, where the complement preserves it and is
    correct by construction. `consumed` is what `convert_field`/
    `convert_metric` actually read, reused here rather than duplicated, so
    the two lists cannot drift apart the way two independently maintained
    ones could.

    A property whose value contains a forbidden key anywhere inside it is
    dropped here -- with a WARNING logged naming it, so a per-column loss
    stays a survivable one rather than the hard `ConversionError`
    `stash.write_stash` would otherwise raise for it -- rather than
    aborting the whole column's conversion over one contaminated property.
    `write_stash` still re-checks (against its own narrower vocabulary)
    whatever reaches it, so this is a caller earning its place with a softer
    landing for a known case, not the only thing standing between identity
    content and the output.
    """
    remainder: dict = {}
    for key, value in properties.items():
        if key in consumed:
            continue
        # Wrapping `{key: value}` rather than scanning `value` alone catches
        # both shapes in one call: the property's own name being forbidden
        # (a scalar `properties: {"guid": "..."}`, unlikely but not ruled
        # out) and a forbidden key nested inside its value.
        if stash.find_forbidden_key({key: value}, _DEEP_IDENTITY_KEYS) is not None:
            log.add(
                code="TS-PROPERTY-IDENTITY-DROPPED",
                severity=Severity.WARNING,
                message=(
                    f"property {key!r} contains instance-local identity "
                    f"content; it is dropped rather than carried into the "
                    f"portable document"
                ),
                object_ref=object_ref,
            )
            continue
        remainder[key] = value
    return remainder


def _write_stash_safely(obj: dict, payload: dict, log: IssueLog, object_ref: str) -> dict:
    """`stash.write_stash(obj, payload)`, catching its X8 guard and turning a
    would-be hard failure into a survivable, logged drop.

    The payload content this module stashes is TML data read out of a
    source file, not something the converter itself constructed -- an
    identity key surfacing somewhere inside it is expected input, not a
    programming error, and expected input must not abort the whole
    conversion the way every other loss in this module does not. The guard
    itself still lives at `stash.write_stash`, and still raises: that is
    what makes it impossible to bypass, present caller or future one. This
    is the one place that catches the raise and keeps going, generalising
    the same choice `_unconsumed_properties` already makes for column
    properties to every other stash site, rather than repeating a bespoke
    pre-filter at each one.

    A payload can have more than one contaminated top-level key, so this
    retries after removing one at a time rather than assuming a single
    pass suffices. If `stash.write_stash` ever raises for a reason other
    than a forbidden key found in `payload` itself (a malformed *existing*
    stash entry on `obj`, surfaced via its internal `read_stash` call, is
    the one other case it can raise for) there is no payload key to blame,
    and the exception is left to propagate rather than being swallowed.

    Every call to `stash.write_stash` in this module goes through this
    function -- including `convert_metric`'s own `tml_name`/`shape` payload,
    which is hardcoded scalars today and so never actually exercises the
    catch, but a future change that puts TML-derived content into it would
    otherwise silently reinstate the abort-the-whole-conversion behaviour
    this function exists to remove. A new call to `stash.write_stash`
    added anywhere in this module should be a call to this function instead,
    not a second bespoke exception.
    """
    cleaned = dict(payload)
    while True:
        try:
            return stash.write_stash(obj, cleaned)
        except ConversionError:
            offender = next(
                (
                    key for key, value in cleaned.items()
                    if stash.find_forbidden_key({key: value}) is not None
                ),
                None,
            )
            if offender is None:
                raise
            log.add(
                code="TS-STASH-IDENTITY-DROPPED",
                severity=Severity.WARNING,
                message=(
                    f"stash field {offender!r} contains instance-local identity "
                    f"content; it is dropped rather than carried into the "
                    f"portable document"
                ),
                object_ref=object_ref,
            )
            del cleaned[offender]


def _field_owner_dataset(
    column: dict, formulas: dict[str, dict], resolve: Callable[[str, str], str | None]
) -> str | None:
    """Which dataset a *successfully built* field belongs in.

    Only ever called after `convert_field` has already returned a non-`None`
    field for this exact column, which makes every path here provably safe:
    the physical branch re-parses the same `column_id` `convert_field` just
    parsed without raising, and the formula branch re-runs `attribute_dataset`
    on the same expression `convert_field` just attributed successfully --
    and `attribute_dataset`'s success path never logs (only its failure paths
    do), so repeating it here adds nothing to the issue log.
    """
    if "column_id" in column:
        table_name, _column_name = identifiers.split_column_ref(f"[{column['column_id']}]")
        return table_name
    formula_entry = formulas.get(column.get("formula_id"))
    if formula_entry is None or "expr" not in formula_entry:
        return None
    return attribute_dataset(formula_entry["expr"], resolve, IssueLog(), object_ref="")


def _build_dataset(prefix: str, entry: dict, table_doc, log: IssueLog) -> tuple[dict, dict]:
    """One `model_tables[]` entry, paired with its Table/SQL-View document,
    into `(base Ossie dataset dict, its custom_extensions[THOUGHTSPOT] payload)`.

    The base dict carries `name`/`source`/`description` only -- no `fields`
    key yet. The caller fills that in once every dataset (and therefore the
    resolver) exists, and calls `stash.write_stash` with the returned payload
    once fields are attached, so key order in the final dict reads naturally
    even though this function runs long before fields are known.

    `prefix` becomes the dataset's Ossie `name` verbatim: `entry["alias"]`
    when present, else `entry["name"]`, never run through
    `identifiers.normalise` -- the Dataset-level mapping requires it to match
    the model_tables[] reference name exactly, case-sensitive, since that is
    also the prefix every `column_id`/join reference in this dataset uses.
    """
    body = table_doc.body
    table_ref = entry.get("name")
    alias = entry.get("alias")
    kind = table_doc.kind

    ds_stash: dict = {DATASET_STASH_TML_OBJECT: kind}
    if alias:
        ds_stash[DATASET_STASH_ALIAS] = alias
        ds_stash[DATASET_STASH_TABLE_NAME] = table_ref

    connection_name = (body.get("connection") or {}).get("name")
    if connection_name:
        ds_stash[DATASET_STASH_CONNECTION_NAME] = connection_name

    if kind == "sql_view":
        source = body.get("sql_query") or ""
        ds_stash[DATASET_STASH_SQL_QUERY] = source
    else:
        db = body.get("db") or ""
        schema = body.get("schema") or ""
        db_table = body.get("db_table") or table_ref or ""
        if any("." in part for part in (db, schema, db_table)):
            # A dotted source string would be ambiguous -- keep the parts too.
            ds_stash[DATASET_STASH_SOURCE_PARTS] = {
                DATASET_STASH_SOURCE_PARTS_DB: db,
                DATASET_STASH_SOURCE_PARTS_SCHEMA: schema,
                DATASET_STASH_SOURCE_PARTS_DB_TABLE: db_table,
            }
        source = ".".join((db, schema, db_table))

    dataset: dict = {"name": prefix, "source": source}
    description = body.get("description")
    if description:
        dataset["description"] = description

    if body.get("rls_rules"):
        # NM2: row-level security policy is instance-local (it names groups
        # that only exist on the source instance) and is never carried into
        # the portable document. Per ThoughtSpot domain review this is now
        # the primary RLS mechanism customers are migrating onto, so this is
        # an ERROR-severity issue naming the table, not a quiet declared loss.
        log.add(
            code="TS-DATASET-RLS-RULES",
            severity=Severity.ERROR,
            message=(
                f"table {table_ref!r} has row-level security rules (rls_rules); "
                f"these reference instance-local groups and are not carried into "
                f"the portable document -- data that was previously restricted is "
                f"unrestricted until row-level security is reapplied on the "
                f"target instance"
            ),
            object_ref=f"dataset:{prefix}",
            remedy=(
                "Reapply the table's row-level security rules manually on the "
                "target instance after import."
            ),
        )

    return dataset, ds_stash


#: One equality pair, and nothing but: two bracketed references either side of
#: a bare `=`. Anything else -- `>=`/`>`/`<`/`<=`, a literal on either side, or
#: a genuine `=` between something that isn't two whole `[TABLE::Column]`
#: references -- does not match, and is therefore a residual predicate.
_EQUALITY_PAIR_RE = re.compile(r"^\s*(\[[^\]]+\])\s*=\s*(\[[^\]]+\])\s*$")
_AND_RE = re.compile(r"\band\b", re.IGNORECASE)


def _split_top_level_and(text: str) -> list[str]:
    """Split a join condition on its top-level ` and ` operators.

    Reuses `formula._scan` -- the same quote/bracket-depth tracker every
    other reference-aware split in this package is built on -- so a literal
    "and" inside a quoted literal, or inside a `[TABLE::Column]` body (a
    table or column display name can genuinely contain the word, e.g.
    `[Research and Development::Col]`), is never mistaken for the boolean
    operator. An empty or whitespace-only `text` yields no parts.
    """
    if not text or not text.strip():
        return []
    context = {i: (depth, in_quote) for i, _ch, depth, in_quote in formula._scan(text)}
    parts: list[str] = []
    start = 0
    for match in _AND_RE.finditer(text):
        depth, in_quote = context.get(match.start(), (0, False))
        if depth == 0 and not in_quote:
            parts.append(text[start : match.start()].strip())
            start = match.end()
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _parse_join_condition(
    on_expression: str, from_prefix: str, to_prefix: str
) -> tuple[list[tuple[str, str]], list[str]]:
    """Split a join condition into equality pairs and residual predicates.

    Per the mapping document's *Non-equality joins* section: the condition is
    split on its top-level `and`s; a part that is exactly `[FROM::a] = [TO::x]`
    (in either orientation -- the equality is symmetric in TML, so the pair is
    reoriented to `(from_col, to_col)` regardless of which side of `=` each
    reference was written on) becomes one pair. Everything else -- `>=`, `>`,
    `<`, `<=`, a comparison against a literal, or an equality naming some
    table other than `from_prefix`/`to_prefix` -- is a residual predicate,
    kept verbatim.

    Raises `ValueError` (via `identifiers.split_column_ref`) on an ambiguous
    column reference. The caller (`_relationship_from_join`) catches this per
    relationship rather than letting it abort the whole conversion.
    """
    equality_pairs: list[tuple[str, str]] = []
    residuals: list[str] = []
    for part in _split_top_level_and(on_expression):
        match = _EQUALITY_PAIR_RE.match(part)
        if match is None:
            residuals.append(part)
            continue
        left_table, left_column = identifiers.split_column_ref(match.group(1))
        right_table, right_column = identifiers.split_column_ref(match.group(2))
        if left_table == from_prefix and right_table == to_prefix:
            equality_pairs.append((left_column, right_column))
        elif left_table == to_prefix and right_table == from_prefix:
            equality_pairs.append((right_column, left_column))
        else:
            # An equality pair, but not one naming both sides of *this* join --
            # cannot be expressed as one of its from_columns/to_columns.
            residuals.append(part)
    return equality_pairs, residuals


def _unrepresentable_entry(
    from_prefix: str,
    to_prefix: str,
    on_expression: str,
    join_type: str | None,
    cardinality: str | None,
    join_shape: str,
    referencing_join: str | None,
) -> dict:
    """One `unrepresentable_joins[]` entry -- everything schema-required, plus
    whatever else about the join is known, verbatim."""
    entry: dict = {
        "from": from_prefix,
        "to": to_prefix,
        RELATIONSHIP_STASH_ON_EXPRESSION: on_expression,
        RELATIONSHIP_STASH_JOIN_SHAPE: join_shape,
    }
    if join_type:
        entry[RELATIONSHIP_STASH_TYPE] = join_type
    if cardinality:
        entry[RELATIONSHIP_STASH_CARDINALITY] = cardinality
    if referencing_join:
        entry[RELATIONSHIP_STASH_REFERENCING_JOIN] = referencing_join
    return entry


def _relationship_from_join(
    *,
    name: str,
    from_prefix: str,
    to_prefix: str,
    on_expression: str | None,
    join_type: str | None,
    cardinality: str | None,
    join_shape: str,
    referencing_join: str | None,
    log: IssueLog,
) -> tuple[dict | None, dict | None, bool]:
    """One join -> `(relationship, unrepresentable_entry, has_residual_predicates)`.

    Exactly one of `relationship`/`unrepresentable_entry` is non-`None` (or
    both `None` when there is no condition at all to report). Implements the
    *Non-equality joins* table: at least one equality pair emits a
    `Relationship`, with any residual predicates riding along in its own
    `custom_extensions` rather than withholding the relationship; zero
    equality pairs -- including when the condition could not be parsed at all
    -- emits nothing, because Ossie's schema requires `from_columns`/
    `to_columns` non-empty, and the condition goes to the model-scope
    `unrepresentable_joins` stash instead.
    """
    object_ref = f"relationship:{name}"
    if not on_expression or not on_expression.strip():
        log.add(
            code="TS-JOIN-NO-CONDITION",
            severity=Severity.WARNING,
            message=(
                f"join {name!r} from {from_prefix!r} to {to_prefix!r} has no "
                f"condition; it cannot be represented as a relationship"
            ),
            object_ref=object_ref,
        )
        return None, None, False

    try:
        equality_pairs, residuals = _parse_join_condition(on_expression, from_prefix, to_prefix)
    except ValueError as exc:
        log.add(
            code="TS-JOIN-MALFORMED",
            severity=Severity.WARNING,
            message=(
                f"join {name!r} condition {on_expression!r} could not be parsed "
                f"({exc}); it is preserved verbatim as an unrepresentable join "
                f"rather than as a relationship"
            ),
            object_ref=object_ref,
        )
        entry = _unrepresentable_entry(
            from_prefix, to_prefix, on_expression, join_type, cardinality,
            join_shape, referencing_join,
        )
        return None, entry, False

    if not equality_pairs:
        log.add(
            code="TS-JOIN-UNREPRESENTABLE",
            severity=Severity.WARNING,
            message=(
                f"join {name!r} from {from_prefix!r} to {to_prefix!r} has no "
                f"equality pair in its condition ({on_expression!r}); Ossie requires "
                f"from_columns/to_columns to be non-empty, so no relationship is "
                f"emitted for it"
            ),
            object_ref=object_ref,
        )
        entry = _unrepresentable_entry(
            from_prefix, to_prefix, on_expression, join_type, cardinality,
            join_shape, referencing_join,
        )
        return None, entry, False

    relationship: dict = {
        "name": name,
        "from": from_prefix,
        "to": to_prefix,
        "from_columns": [pair[0] for pair in equality_pairs],
        "to_columns": [pair[1] for pair in equality_pairs],
    }
    rel_stash: dict = {RELATIONSHIP_STASH_JOIN_SHAPE: join_shape}
    if join_type:
        rel_stash[RELATIONSHIP_STASH_TYPE] = join_type
    if cardinality:
        rel_stash[RELATIONSHIP_STASH_CARDINALITY] = cardinality
    if referencing_join:
        rel_stash[RELATIONSHIP_STASH_REFERENCING_JOIN] = referencing_join
    has_residuals = bool(residuals)
    if has_residuals:
        rel_stash[RELATIONSHIP_STASH_ON_EXPRESSION] = on_expression
        rel_stash[RELATIONSHIP_STASH_RESIDUAL_PREDICATES] = residuals
        log.add(
            code="TS-JOIN-RESIDUAL-PREDICATES",
            severity=Severity.WARNING,
            message=(
                f"relationship {name!r} carries residual predicate(s) beyond its "
                f"equality pairs; a consumer that reads only from_columns/"
                f"to_columns will join more rows than ThoughtSpot does"
            ),
            object_ref=object_ref,
        )
    relationship = _write_stash_safely(relationship, rel_stash, log, object_ref)
    return relationship, None, has_residuals


def _convert_join(
    from_prefix: str, join: dict, from_table_body: dict, known_datasets: frozenset[str],
    log: IssueLog,
) -> tuple[dict | None, dict | None, keys.Relationship | None]:
    """One `model_tables[].joins[]` entry -> `(relationship,
    unrepresentable_entry, key_candidate)`.

    Handles both TML join shapes: `inline` (fully defined here -- `with`/
    `on`/`type`/`cardinality`) and `referencing` (`referencing_join` names an
    entry in the *Table*'s own `joins_with[]`, which supplies `destination`/
    `on`/`type`/`cardinality`; a `type`/`cardinality` also present on this
    entry overrides the Table's and marks the shape
    `referencing_with_inline_attrs`, the real hybrid the 2026-07-30 census
    found on 12 of 493 joins).

    `known_datasets` is checked against the resolved target the same way the
    caller already checks the source before calling this at all: a target
    naming a dataset this model never built -- a table document missing, a
    duplicate alias, or simply a typo -- is dropped with a WARNING rather
    than emitted. Upstream's own validator hard-fails a document with a
    relationship pointing at an unknown dataset (`exit 1`, not a warning),
    so emitting one anyway would make the *whole* document unusable by any
    downstream tool that runs it; dropping the one broken relationship keeps
    everything else in the model valid and usable, which is the more useful
    failure of the two.

    KD1's cardinality-orientation rule is applied here, not in
    `_relationship_from_join`: the *emitted* relationship's `from`/`to` always
    mirrors TML's FK-structural fact unconditionally (the Relationship-level
    mapping's `from` row), but a `ONE_TO_MANY` join is evidence that the FROM
    side -- not the TO side -- is the one covered by a key, so the key
    candidate handed to `keys.derive_keys` targets `from_prefix` with the
    relationship's own `from_columns`, relabelled `MANY_TO_ONE` from that
    flipped perspective (`keys._qualifies` only recognises that spelling).
    `MANY_TO_MANY` needs no such handling -- it is excluded by
    `keys._qualifies` on either side, which is already correct.
    """
    referencing_join = join.get("referencing_join")
    if referencing_join:
        candidates = from_table_body.get("joins_with") or []
        matched = next((jw for jw in candidates if jw.get("name") == referencing_join), None)
        if matched is None:
            log.add(
                code="TS-JOIN-REFERENCING-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"model_tables[] entry {from_prefix!r} references joins_with "
                    f"{referencing_join!r}, which is not defined on its table; the "
                    f"join is skipped"
                ),
                object_ref=f"relationship:{referencing_join}",
            )
            return None, None, None
        to_prefix = (matched.get("destination") or {}).get("name")
        on_expression = matched.get("on")
        join_type = join.get("type", matched.get("type"))
        cardinality = join.get("cardinality", matched.get("cardinality"))
        join_shape = (
            "referencing_with_inline_attrs"
            if ("type" in join or "cardinality" in join)
            else "referencing"
        )
        name = referencing_join
    else:
        to_prefix = join.get("with")
        on_expression = join.get("on")
        join_type = join.get("type")
        cardinality = join.get("cardinality")
        join_shape = "inline"
        name = f"{from_prefix}_to_{to_prefix}" if to_prefix else f"{from_prefix}_to_<unknown>"

    if not to_prefix:
        log.add(
            code="TS-JOIN-NO-TARGET",
            severity=Severity.WARNING,
            message=f"join {name!r} from {from_prefix!r} names no target dataset; it is skipped",
            object_ref=f"relationship:{name}",
        )
        return None, None, None

    if to_prefix not in known_datasets:
        log.add(
            code="TS-JOIN-UNKNOWN-TARGET",
            severity=Severity.WARNING,
            message=(
                f"join {name!r} from {from_prefix!r} targets {to_prefix!r}, which "
                f"is not one of this model's datasets; the relationship is "
                f"dropped rather than emitted pointing at a dataset that does not "
                f"exist"
            ),
            object_ref=f"relationship:{name}",
        )
        return None, None, None

    relationship, unrepresentable, has_residuals = _relationship_from_join(
        name=name,
        from_prefix=from_prefix,
        to_prefix=to_prefix,
        on_expression=on_expression,
        join_type=join_type,
        cardinality=cardinality,
        join_shape=join_shape,
        referencing_join=referencing_join,
        log=log,
    )

    candidate = None
    if relationship is not None:
        if cardinality == "ONE_TO_MANY":
            candidate = keys.Relationship(
                name=name,
                to_dataset=from_prefix,
                to_columns=relationship["from_columns"],
                cardinality="MANY_TO_ONE",
                has_residual_predicates=has_residuals,
            )
        else:
            candidate = keys.Relationship(
                name=name,
                to_dataset=to_prefix,
                to_columns=relationship["to_columns"],
                cardinality=cardinality or "",
                has_residual_predicates=has_residuals,
            )
    return relationship, unrepresentable, candidate


@dataclass(frozen=True)
class OssieConversion:
    """The result of one TML -> Ossie conversion.

    `model` is the full Ossie document -- `{"version": ..., "semantic_model":
    [...]}` -- ready to dump as YAML. `issues` is every declared loss and
    degradation raised while building it: nothing in `model` is missing
    something TML held without a matching entry here.
    """

    model: dict
    issues: IssueLog


def convert(document_set: DocumentSet) -> OssieConversion:
    """Convert one ThoughtSpot TML document set into one Ossie semantic model.

    Order matters and mirrors the module docstring above: datasets first (so
    their names -- the model_tables[] alias-or-name, verbatim -- exist),
    then the cross-model resolver (needs every dataset's name and every
    ATTRIBUTE column's identifier), then fields and metrics (need `resolve`),
    then relationships (need nothing new, but key derivation needs every
    relationship gathered first), then keys.

    A malformed reference anywhere -- an ambiguous `column_id`, an ambiguous
    reference inside a join condition -- is caught per object: that object is
    skipped, an issue names it and why, and every other object still
    converts. A field that could not be attributed to a dataset is either
    entirely omitted (a physical column, or a formula-produced field with no
    attribution -- there is nothing else to build) or, when it is a
    formula-backed ATTRIBUTE column whose formula genuinely exists, preserved
    verbatim in the model-scope `unattributed_formulas` stash rather than
    dropped outright.
    """
    log = IssueLog()
    model_body = document_set.model.body

    model_display_name = model_body.get("name") or ""
    semantic_model_name = (
        identifiers.normalise(model_display_name) if model_display_name else "model"
    )
    semantic_model: dict = {"name": semantic_model_name, "datasets": []}
    model_stash: dict = {}
    if semantic_model_name != model_display_name:
        model_stash[STASH_TML_NAME] = model_display_name

    description = model_body.get("description")
    if description:
        semantic_model["description"] = description

    # -- Phase 1: datasets --------------------------------------------------
    dataset_order: list[str] = []
    dataset_bodies: dict[str, dict] = {}
    dataset_stashes: dict[str, dict] = {}
    table_docs: dict[str, dict] = {}
    physical_columns_by_prefix: dict[str, list[dict]] = {}
    fields_by_dataset: dict[str, list] = {}
    seen_prefixes: set[str] = set()

    model_tables = model_body.get("model_tables") or []
    for entry in model_tables:
        table_ref = entry.get("name")
        prefix = entry.get("alias") or table_ref
        if not prefix:
            log.add(
                code="TS-DATASET-NO-NAME",
                severity=Severity.WARNING,
                message="a model_tables[] entry has no name and no alias; it cannot become a dataset",
                object_ref="dataset:<unnamed>",
            )
            continue
        object_ref = f"dataset:{prefix}"
        if prefix in seen_prefixes:
            log.add(
                code="TS-DATASET-DUPLICATE-PREFIX",
                severity=Severity.WARNING,
                message=(
                    f"more than one model_tables[] entry resolves to the reference "
                    f"name {prefix!r}; only the first is converted"
                ),
                object_ref=object_ref,
            )
            continue
        table_doc = document_set.table_by_name(table_ref) if table_ref else None
        if table_doc is None:
            log.add(
                code="TS-DATASET-TABLE-MISSING",
                severity=Severity.WARNING,
                message=(
                    f"model_tables[] entry {prefix!r} references table {table_ref!r}, "
                    f"which has no matching table/sql_view document; the dataset is "
                    f"skipped"
                ),
                object_ref=object_ref,
            )
            continue

        dataset_dict, ds_stash = _build_dataset(prefix, entry, table_doc, log)
        seen_prefixes.add(prefix)
        dataset_order.append(prefix)
        dataset_bodies[prefix] = dataset_dict
        dataset_stashes[prefix] = ds_stash
        table_docs[prefix] = table_doc.body
        physical_columns_by_prefix[prefix] = _normalized_physical_columns(
            table_doc.body, table_doc.kind
        )
        fields_by_dataset[prefix] = []

    def table_lookup(name: str) -> dict | None:
        columns = physical_columns_by_prefix.get(name)
        if columns is None:
            return None
        return {"columns": columns}

    # -- Phase 2: the cross-model resolver -----------------------------------
    model_columns = model_body.get("columns") or []
    attribute_index = _index_attribute_columns(model_columns, log)

    def resolve(table: str, column: str) -> str | None:
        # The mapping document is explicit for a bare-identifier field: "the
        # identifier is the *physical* column; the display name comes from
        # label/name." So the ANSI_SQL sibling this feeds -- built to be
        # directly executable against the warehouse -- has to carry the
        # actual warehouse column reference (db_column_name, or a SQL
        # View's sql_output_column), never the Ossie field's own
        # display-derived identifier, which is not a column that exists on
        # the underlying table at all. `attribute_index` still gates
        # whether this reference is one the model actually surfaces as a
        # field -- that scope is unchanged -- only the value returned once
        # it passes that gate changes.
        if table not in dataset_bodies:
            return None
        if (table, column) not in attribute_index:
            return None
        physical = next(
            (p for p in physical_columns_by_prefix.get(table, []) if p.get("name") == column),
            None,
        )
        if physical is None:
            return None
        warehouse_reference = physical.get("db_column_name")
        if warehouse_reference is None:
            return None
        return f"{table}.{warehouse_reference}"

    # -- Phase 3: fields and metrics ------------------------------------------
    formulas: dict[str, dict] = {
        f["id"]: f for f in (model_body.get("formulas") or []) if f.get("id")
    }
    metrics: list[dict] = []

    for column in model_columns:
        display_name = column.get("name", "<unnamed>")
        properties = column.get("properties") or {}
        try:
            field = convert_field(column, formulas, table_lookup, resolve, log)
            metric = None if field is not None else convert_metric(
                column, formulas, table_lookup, resolve, log
            )
        except ValueError as exc:
            log.add(
                code="TS-COLUMN-REF-MALFORMED",
                severity=Severity.WARNING,
                message=f"column {display_name!r} could not be converted: {exc}",
                object_ref=f"field:{display_name}",
            )
            continue

        if field is not None:
            extra_properties = _unconsumed_properties(
                properties, _FIELD_CONSUMED_PROPERTIES, log, f"field:{display_name}"
            )
            field_stash_payload: dict = {}
            if extra_properties:
                field_stash_payload[FIELD_STASH_COLUMN_PROPERTIES] = extra_properties
            if "column_id" in column:
                field_stash_payload.update(_physical_column_stash(
                    column["column_id"], field.get("datatype"),
                    physical_columns_by_prefix, dataset_stashes,
                ))
            if field_stash_payload:
                field = _write_stash_safely(field, field_stash_payload, log, f"field:{display_name}")
            owner = _field_owner_dataset(column, formulas, resolve)
            if owner is not None and owner in fields_by_dataset:
                fields_by_dataset[owner].append(field)
            else:
                log.add(
                    code="TS-FIELD-DATASET-MISSING",
                    severity=Severity.WARNING,
                    message=(
                        f"field {display_name!r} resolves to dataset {owner!r}, "
                        f"which was not built; the field is dropped"
                    ),
                    object_ref=f"field:{display_name}",
                )
            continue

        if metric is not None:
            extra_properties = _unconsumed_properties(
                properties, _METRIC_CONSUMED_PROPERTIES, log, f"metric:{display_name}"
            )
            metric_stash_payload: dict = {}
            if extra_properties:
                metric_stash_payload[FIELD_STASH_COLUMN_PROPERTIES] = extra_properties
            if "column_id" in column:
                metric_stash_payload.update(_physical_column_stash(
                    column["column_id"], metric.get("datatype"),
                    physical_columns_by_prefix, dataset_stashes,
                ))
            if metric_stash_payload:
                metric = _write_stash_safely(metric, metric_stash_payload, log, f"metric:{display_name}")
            metrics.append(metric)
            continue

        # Neither a field nor a metric was built.
        column_type = properties.get("column_type")
        if column_type not in ("ATTRIBUTE", "MEASURE"):
            # A column_type this converter does not recognise at all (TML
            # requires one of the two) is a malformed column, not a case
            # convert_field/convert_metric already explained -- name it
            # rather than silently skipping it.
            log.add(
                code="TS-COLUMN-TYPE-UNKNOWN",
                severity=Severity.WARNING,
                message=(
                    f"column {display_name!r} has column_type {column_type!r}, "
                    f"which is neither ATTRIBUTE nor MEASURE; it is not converted"
                ),
                object_ref=f"field:{display_name}",
            )
            continue

        # The one case worth preserving: an ATTRIBUTE formula that genuinely
        # exists (has an expr) but could not be attributed to a single
        # dataset -- convert_field already logged why via attribute_dataset.
        if column_type == "ATTRIBUTE" and "formula_id" in column:
            formula_entry = formulas.get(column["formula_id"])
            if formula_entry is not None and "expr" in formula_entry:
                unattributed: dict = {
                    "name": formula_entry.get("name") or display_name,
                    "expr": formula_entry["expr"],
                }
                if properties:
                    unattributed[FIELD_STASH_COLUMN_PROPERTIES] = properties
                model_stash.setdefault(MODEL_STASH_UNATTRIBUTED_FORMULAS, []).append(unattributed)

    # -- Phase 3.5: unsurfaced physical columns, and SQL View output aliases --
    # A Table/SQL-View column no Model columns[] entry surfaces -- by
    # column_id, field or metric alike -- is not part of the semantic
    # model, but has to be preserved verbatim (Dataset-level mapping,
    # "fields" row) so the source document can be regenerated exactly on
    # the way back. `_raw_physical_columns` reads whichever key this
    # dataset's document kind actually uses (`columns[]` or
    # `sql_view_columns[]`) -- the RAW entries, not the datatype-lookup
    # shape `_normalized_physical_column` builds, since regenerating a SQL
    # View column needs its own `sql_output_column` key back, not a
    # `db_column_name` this converter invented for lookup purposes.
    referenced_columns = _referenced_physical_columns(model_columns)
    for prefix in dataset_order:
        kind = "sql_view" if dataset_stashes[prefix].get(DATASET_STASH_TML_OBJECT) == "sql_view" else "table"
        raw_columns = _raw_physical_columns(table_docs.get(prefix) or {}, kind)
        unsurfaced = [
            column for column in raw_columns
            if (prefix, column.get("name")) not in referenced_columns
        ]
        if unsurfaced:
            dataset_stashes[prefix][DATASET_STASH_UNSURFACED_COLUMNS] = unsurfaced

        if kind == "sql_view":
            # Every SURFACED field on a SQL View needs its own
            # sql_output_column recorded (DatasetLevel schema's
            # sql_output_columns key: "field name -> sql_output_column
            # alias") -- there is no safe way to re-derive a query output
            # alias from an Ossie field's own identifier the way a Table's
            # db_column_name might be guessed at, so this is always
            # necessary, not just when the alias happens to differ from the
            # field's name.
            output_aliases = {}
            for column in raw_columns:
                field_name = attribute_index.get((prefix, column.get("name")))
                if field_name is not None and column.get("sql_output_column") is not None:
                    output_aliases[field_name] = column["sql_output_column"]
            if output_aliases:
                dataset_stashes[prefix][DATASET_STASH_SQL_OUTPUT_COLUMNS] = output_aliases

    # -- Phase 4: relationships ------------------------------------------------
    relationships: list[dict] = []
    key_candidates: list[keys.Relationship] = []
    known_datasets = frozenset(dataset_bodies)

    for entry in model_tables:
        table_ref = entry.get("name")
        from_prefix = entry.get("alias") or table_ref
        if from_prefix not in dataset_bodies:
            continue  # the dataset itself failed to build; already logged
        for join in entry.get("joins") or []:
            relationship, unrepresentable, candidate = _convert_join(
                from_prefix, join, table_docs.get(from_prefix) or {}, known_datasets, log
            )
            if relationship is not None:
                relationships.append(relationship)
            if unrepresentable is not None:
                model_stash.setdefault(MODEL_STASH_UNREPRESENTABLE_JOINS, []).append(unrepresentable)
            if candidate is not None:
                key_candidates.append(candidate)

    # -- Phase 5: keys -----------------------------------------------------
    for prefix in dataset_order:
        primary_key, unique_keys = keys.derive_keys(prefix, key_candidates, log)
        if primary_key:
            dataset_bodies[prefix]["primary_key"] = primary_key
        if unique_keys:
            dataset_bodies[prefix]["unique_keys"] = unique_keys

    # -- Phase 6: assemble datasets ------------------------------------------
    datasets_out: list[dict] = []
    for prefix in dataset_order:
        dataset_dict = dataset_bodies[prefix]
        if fields_by_dataset[prefix]:
            dataset_dict["fields"] = fields_by_dataset[prefix]
        dataset_dict = _write_stash_safely(dataset_dict, dataset_stashes[prefix], log, f"dataset:{prefix}")
        datasets_out.append(dataset_dict)
    semantic_model["datasets"] = datasets_out

    if relationships:
        semantic_model["relationships"] = relationships
    if metrics:
        semantic_model["metrics"] = metrics

    # -- Phase 7: model-scope stash -------------------------------------------
    raw_properties = model_body.get("properties") or {}
    model_properties: dict = {}
    for key_name in ("is_bypass_rls", "join_progressive"):
        if key_name in raw_properties:
            model_properties[key_name] = raw_properties[key_name]
    spotter = raw_properties.get("spotter_config")
    if isinstance(spotter, dict) and "is_spotter_enabled" in spotter:
        model_properties["spotter_config"] = {
            "is_spotter_enabled": spotter["is_spotter_enabled"]
        }
    if model_properties:
        model_stash[MODEL_STASH_MODEL_PROPERTIES] = model_properties

    for key_name in (
        MODEL_STASH_PARAMETERS, MODEL_STASH_FILTERS, MODEL_STASH_COLUMN_GROUPS,
        MODEL_STASH_LESSON_PLANS, MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS,
    ):
        value = model_body.get(key_name)
        if value:
            model_stash[key_name] = value
    constraints = model_body.get(MODEL_STASH_CONSTRAINTS)
    if constraints:
        model_stash[MODEL_STASH_CONSTRAINTS] = constraints
    model_joins_with = model_body.get("joins_with")
    if model_joins_with:
        model_stash[MODEL_STASH_MODEL_JOINS_WITH] = model_joins_with

    if model_body.get("aggregated_models"):
        # Aggregate-model routing associations are GUIDs of other Model
        # objects -- instance-local, so they are never stashed. Stripping
        # them silently disables the routing with no error, so the issue is
        # the only signal a reader gets.
        log.add(
            code="TS-MODEL-AGGREGATED-MODELS",
            severity=Severity.WARNING,
            message=(
                "model has aggregated_models query-routing associations, which "
                "reference instance-local Model GUIDs; they are not carried into "
                "the portable document, so aggregate-aware routing will not be "
                "active after import"
            ),
            object_ref=f"model:{semantic_model_name}",
            remedy="Reconfigure aggregate-model routing manually on the target instance after import.",
        )

    semantic_model = _write_stash_safely(semantic_model, model_stash, log, f"model:{semantic_model_name}")

    document = {"version": DOCUMENT_VERSION, "semantic_model": [semantic_model]}
    return OssieConversion(model=document, issues=log)
