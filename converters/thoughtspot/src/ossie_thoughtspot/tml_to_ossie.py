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
expr>)`, never the bare scalar) but a no-op on an *aggregate*-formula metric, which already
carries its own rollup. Composing when the rule says no-op, or leaving bare when the rule
says compose, silently changes the grain the metric evaluates at while the model still
imports. Whether a formula's own outer call is already a native ThoughtSpot aggregate is
decided by `_is_aggregate_expression`, which reads ThoughtSpot's aggregate call names off
the same expression catalog `_compose_aggregate_entries` uses to build the composed
rendering — one source for both jobs, so they cannot silently drift apart the way two
independently hand-typed lists could. And unlike a field, a metric has no `label`: when
ID1 normalisation changes the identifier, the exact display name has nowhere to go but the
`custom_extensions` stash.
"""
from __future__ import annotations

from typing import Callable

from . import datatypes, formula, identifiers, stash
from .constants import DIALECT, PORTABLE_DIALECT
from .expressions import CATALOG, emit_direct
from .issues import IssueLog, Severity


def expression_entries(
    expr: str,
    resolve: Callable[[str, str], str | None],
    log: IssueLog,
    *,
    object_ref: str,
) -> list[dict[str, str]]:
    """The dialect entries for one ThoughtSpot expression.

    The THOUGHTSPOT entry always comes first and always carries `expr` unmodified —
    whatever else this function decides, that entry is what makes the expression
    recoverable later, character for character. A second, ANSI_SQL entry is appended
    only when the whole expression is a bare column reference the resolver can place in
    a dataset. Every other shape — a runtime parameter, an unresolvable reference, a
    function call, a compound expression — gets an issue instead of a guessed
    translation, and only the verbatim entry is returned.
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
            "does not implement it will not be able to evaluate this field"
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
    """
    table = table_lookup(table_name)
    physical = None
    if table is not None:
        for candidate in table.get("columns", []):
            if candidate.get("name") == column_name:
                physical = candidate
                break
    if physical is None:
        log.add(
            code="TS-FIELD-PHYSICAL-COLUMN-MISSING",
            severity=Severity.WARNING,
            message=(
                f"physical column {column_name!r} was not found on table "
                f"{table_name!r}; no datatype is emitted for this field"
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
            code="TS-FIELD-DATATYPE-UNMAPPED",
            severity=Severity.WARNING,
            message=(
                f"physical column {column_name!r} on table {table_name!r} has "
                f"warehouse data_type {data_type!r}, which has no Ossie "
                f"equivalent; no datatype is emitted for this field"
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
#: it is a formula's own outer call — see `_is_aggregate_expression`.
_NATIVE_AGGREGATE_SPECS = (*_AGGREGATION_CATALOG_SPEC.values(), "MEDIAN(expr)")

#: Every ThoughtSpot native aggregate call name, lower-cased, derived from the
#: catalog's own DIRECT templates via the same `emit_direct` the rest of this package
#: uses to render them — never retyped by hand. See the task report for why this,
#: and not a short hand-written list, was chosen: a hand-written list can silently
#: drift from the catalog (the mapping document's own aggregation row was corrected
#: once already for a related reason), while this recomputes from the templates
#: every time they change.
_AGGREGATE_CALL_NAMES = frozenset(
    formula.split_call(emit_direct(CATALOG[spec], ["x"]))[0].lower()
    for spec in _NATIVE_AGGREGATE_SPECS
)


def _is_aggregate_expression(expr: str) -> bool:
    """Whether `expr`'s own outer call is already a native ThoughtSpot aggregate.

    `formula.split_call` returning `None` — not a single outer call, as in
    `[A::x] - [B::y]` — means `expr` is scalar by definition: there is no outer call
    for it to be an aggregate of. Matching is case-insensitive (ThoughtSpot's formula
    functions are not case-sensitive) and compares the whole call name as one unit, so
    a two-word name like `unique count` is matched by both words together rather than
    by either word alone.

    This is a shallow, single-level check, matching the rest of this package's
    "tokenizer, not a parser" stance (see `formula.py`'s module docstring): an
    aggregate nested inside a scalar wrapper, such as `round ( sum ( x ) , 2 )`, has
    the scalar `round` as its own outer call and is therefore *not* detected as an
    aggregate expression here. See the task report for why that boundary was left
    where it is rather than extended into a real expression-tree walk.
    """
    call = formula.split_call(expr)
    if call is None:
        return False
    name, _args = call
    return name.lower() in _AGGREGATE_CALL_NAMES


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

    inner_entries = expression_entries(inner_expr, resolve, log, object_ref=object_ref)
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
    return _physical_datatype(table_name, column_name, table_lookup, log, object_ref=object_ref)


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
    `AGG(<scalar expr>)`), a no-op on an *aggregate* formula (`sum ( ... )`, which
    already carries its own rollup) — the column-level value is discarded there on
    purpose, without logging anything, because discarding a documented no-op is not
    a loss. See `_is_aggregate_expression` for how the two are told apart, and the
    module docstring for why detection and composition share one source.

    An unrecognised `aggregation` value (not one of TML's documented enum members)
    is treated as `NONE` and logged — the value was present and could not be
    understood, which is a loss worth reporting, unlike an absent `aggregation` key,
    which defaults to `NONE` silently.

    Metrics have no `label` field (unlike fields): when ID1 normalisation changes
    the identifier, the exact ThoughtSpot display name is stashed as `tml_name`
    rather than carried in a dedicated field.
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
        table_name, column_name = identifiers.split_column_ref(f"[{column['column_id']}]")
        field_ref = identifiers.format_column_ref(table_name, column_name)
        if aggregation is None:
            dialects = expression_entries(field_ref, resolve, log, object_ref=object_ref)
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
        if aggregation is not None and not _is_aggregate_expression(expr):
            # A scalar expr: the column aggregation is load-bearing, so compose it.
            dialects = _compose_aggregate_entries(
                expr, aggregation_raw, resolve, log, object_ref=object_ref
            )
        else:
            # Either NONE (nothing to compose) or an expr that is already an
            # aggregate (the column aggregation is a documented no-op) — either way
            # the verbatim expr, untouched, is the whole metric.
            dialects = expression_entries(expr, resolve, log, object_ref=object_ref)
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

    if normalised_name != display_name:
        metric = stash.write_stash(metric, {"tml_name": display_name})

    description = column.get("description")
    if description:
        metric["description"] = description

    ai_context = _ai_context(properties)
    if ai_context is not None:
        metric["ai_context"] = ai_context

    return metric
