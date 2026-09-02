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

"""The reverse-direction inventory: ThoughtSpot functions with no counterpart in the
Ossie specification.

`CATALOG` (catalog.py) is the specification's own inventory, Ossie construct -> ThoughtSpot
rendering. This module is the other half of a bidirectional converter: ThoughtSpot's own
native functions that the 146-row `CATALOG` never targets, sourced from the "Reverse
direction (ThoughtSpot -> Ossie)" section of
docs/ossie/ts-ossie-function-mapping.md (thoughtspot-agent-skills repo, not vendored here) —
its three sub-sections (conditional aggregates and arithmetic helpers; window, LOD and
semi-additive functions; runtime, display and calendar concepts).

Rule E10 governs the whole module: prefer composition over the stash. Most of ThoughtSpot's
apparently-proprietary functions are sugar over constructs the specification already has
(`sum_if` -> `SUM(CASE WHEN ...)`, `safe_divide` -> `COALESCE(a / NULLIF(b, 0), 0)`,
`group_sum` over a fixed grain -> `SUM(x) OVER (PARTITION BY attr)`). The stash
(`custom_extensions` + issue, rule E12) is for what genuinely has no expression — a short
list dominated by *runtime* concepts (parameters, signed-in-user identity, display markup,
fiscal calendars), not by missing mathematics.

Four dispositions, not the forward module's three
------------------------------------------------------------------------------------------
`Classification` (catalog.py's DIRECT/PASSTHROUGH/UNMAPPABLE) does not fit this direction
cleanly, so this module defines its own `ReverseDisposition` rather than bending it (as
instructed): a reverse row can compose fully, compose *partially* (with a real fidelity
loss that still deserves an issue and, per E11, a preserved verbatim rendering), resolve to
the Ossie `dialects[]` mechanism instead of a portable expression at all, or have no
expression whatsoever.

    COMPOSE   a full Ossie expression is produced. May still carry a caveat issue (locale
              dependence, a week-start-day or DAYOFWEEK-base assumption) without being lossy
              in the way PARTIAL is — the composition is exact, the caveat is about the
              *specification's* own portability, not about this construct's translation.
    PARTIAL   a real Ossie expression is produced, but it is provably incomplete (the
              `moving_*`/`cumulative_*` family: the frame and order translate exactly, the
              partition does not, rule E13/ask A10). Always logs a WARNING and, per rule
              E11, the caller should pair the composed expression with a THOUGHTSPOT dialect
              entry (`thoughtspot_dialect_entry`) carrying the verbatim original — and, per
              learnings P8, an ANSI_SQL sibling (`portable_dialect_entry`) for the composed
              expression itself, since it *is* portable, just incomplete.
    DIALECT   the construct's natural home is the Ossie `dialects[]` mechanism, not a
              portable expression — the ten `sql_*_op` / `sql_*_aggregate_op` names. No
              ANSI_SQL sibling is ever emitted for these (the document is explicit: raw
              warehouse SQL's portability is exactly what is unknown).
    STASH     no Ossie expression exists at all. Always logs an ERROR (mirroring
              `emit_unmappable`'s severity choice for the same "no representation, preserved
              only for roundtrip" shape) and, per E11, the caller should attach a THOUGHTSPOT
              dialect entry with the verbatim call.

Argument abstraction level
------------------------------------------------------------------------------------------
`translate_thoughtspot(name, args, log, *, object_ref, ...)` takes `args` as already-
extracted operand strings, exactly the abstraction level `emit_direct`/`emit_passthrough`
take in the forward direction (never raw ThoughtSpot formula text with nested calls or
brace/quote syntax to parse) — this plan's own open items record that the expression parser
does not exist yet, so there is nothing to parse from either direction yet. A caller with a
real parsed formula tree supplies the resolved operand strings positionally.

Interface note against the brief: `IssueLog.add` requires `object_ref` as a mandatory
keyword argument, so `translate_thoughtspot` carries `object_ref` as a required keyword-only
parameter beyond the brief's literal `(name, args, log)` — the same shape
`emit_passthrough`/`emit_unmappable` already use for the identical reason. A second keyword-
only parameter, `connection_dialect`, is added for the DIALECT family only (see
`_dispatch_sql_op`) — the document itself says resolving these needs the connection's own
dialect, which is not derivable from a bare function name and argument list.

Two constructs are cross-cutting rather than name-keyed, so they are not `REVERSE` entries
looked up by `name` at all:

- **The fiscal-calendar argument.** The document describes this as "the rest of the fiscal
  family" without enumerating every date function it can decorate, so `translate_thoughtspot`
  checks for a trailing literal `fiscal` argument up front, for any `name` at all, before
  falling through to a normal lookup.
- **The runtime-parameter reference.** A bracketed name (`[Discount Threshold]`) is
  syntactically identical to an ordinary column reference (`[Table::Column]`) — this module
  has no model metadata to tell the two apart, so there is no way to safely auto-detect it in
  `translate_thoughtspot`. `stash_runtime_parameter` is exposed separately; the caller (which
  does have the model's declared parameter list) invokes it directly once it has confirmed
  the name in hand is a declared parameter and not a column.

Two more are shape-dependent rather than purely name-keyed, and use `ReverseConstruct`'s
`dispatch_fn` escape hatch (full control over composing vs. stashing, bypassing the
declarative `template`/`compose_fn` path entirely): `group_aggregate` and its four named
shorthands (`group_sum`, `group_count`, `group_stddev`, `group_variance`), whose disposition
depends on the shape of the grouping and filter arguments (see `_compose_grouped`); and the
ten `sql_*_op` names, whose disposition depends on whether the caller can supply
`connection_dialect` (see `_dispatch_sql_op`). `concat` is a third, narrower case: plain
`concat` has a spec counterpart already in `CATALOG` and is not this module's concern at all
(returns `None`, no issue) — only the ThoughtSpot hyperlink-markup content pattern inside its
string arguments (`{caption}` / `{/caption}`) is reverse-inventory territory.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..constants import DIALECT, PORTABLE_DIALECT, VENDOR_KEY
from ..issues import IssueLog, Severity


class ReverseDisposition(str, Enum):
    """How a ThoughtSpot-only construct reaches an Ossie document. See the module
    docstring's "Four dispositions" section for the full reasoning behind each.
    """

    COMPOSE = "compose"
    PARTIAL = "partial"
    DIALECT = "dialect"
    STASH = "stash"


# A dispatch_fn takes (args, log, object_ref, connection_dialect) and returns the composed
# Ossie expression, or None if it decides — internally, based on argument shape — to stash
# instead. It owns its own issue logging; disposition on such a row is documentation only.
DispatchFn = Callable[[list, IssueLog, str, str | None], str | None]
ComposeFn = Callable[[list], str]


@dataclass(frozen=True)
class ReverseConstruct:
    """One row of the reverse-direction inventory.

    `thoughtspot_name`  the construct as ThoughtSpot spells it, e.g. "sum_if". May be a
                         synthetic, non-callable key for a construct the document describes
                         by content pattern rather than by name (e.g. the hyperlink-markup
                         row) — always documented as such at the registration site.
    `disposition`       see `ReverseDisposition`.
    `template`          for a plain positional COMPOSE/PARTIAL row: the Ossie expression
                         with {0}, {1}, ... placeholders, rendered via `str.format`. Mutually
                         exclusive with `compose_fn` and unused when `dispatch_fn` is set.
    `compose_fn`        for a COMPOSE/PARTIAL row whose composition is not a simple
                         positional substitution (variable arity, a transform on an
                         argument's literal value). Takes precedence over `template`.
    `dispatch_fn`       full override: decides composing vs. stashing itself from argument
                         shape, and does its own issue logging. When set, `disposition`
                         above is documentation only and no other field is validated.
    `issue_code`        `IssueLog.add(code=...)` for this row's issue (rule E12: never a
                         bare "untranslatable" message).
    `issue_severity`    WARNING for a COMPOSE-with-caveat or PARTIAL row (something usable
                         is still produced); ERROR for STASH (nothing is — mirrors
                         `emit_unmappable`'s choice for the same "no representation" shape).
    `issue_message`     may contain a `{name}` placeholder, filled with the actual
                         ThoughtSpot name/reference at translation time — the same message
                         template serves every case sharing one reason (the five identity
                         functions, the fiscal-calendar family).
    `note`               the row's caveat, traceable to the document, same convention as
                         catalog.py's `Construct.note`.
    """

    thoughtspot_name: str
    disposition: ReverseDisposition
    template: str | None = None
    compose_fn: ComposeFn | None = None
    dispatch_fn: DispatchFn | None = None
    issue_code: str = ""
    issue_severity: Severity = Severity.WARNING
    issue_message: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.dispatch_fn is not None:
            # Full custom control - no further shape validation applies (see class docstring).
            return
        needs_body = self.disposition in (ReverseDisposition.COMPOSE, ReverseDisposition.PARTIAL)
        has_body = self.template is not None or self.compose_fn is not None
        if needs_body and not has_body:
            raise ValueError(
                f"{self.thoughtspot_name}: a {self.disposition.value} row needs a "
                "template or compose_fn"
            )
        if not needs_body and has_body:
            raise ValueError(
                f"{self.thoughtspot_name}: a {self.disposition.value} row must not carry "
                "a template or compose_fn"
            )
        if self.disposition in (ReverseDisposition.PARTIAL, ReverseDisposition.STASH) and not self.issue_message:
            raise ValueError(
                f"{self.thoughtspot_name}: a {self.disposition.value} row must carry an "
                "issue message (E12) — never a bare 'untranslatable'"
            )


REVERSE: dict[str, ReverseConstruct] = {}

_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


def _placeholder_count(template: str) -> int:
    indices = {int(m) for m in _PLACEHOLDER_RE.findall(template)}
    return max(indices) + 1 if indices else 0


def _render(construct: ReverseConstruct, args: list[str]) -> str:
    if construct.compose_fn is not None:
        return construct.compose_fn(args)
    expected = _placeholder_count(construct.template)
    if len(args) != expected:
        plural = "argument" if expected == 1 else "arguments"
        raise ValueError(f"{construct.thoughtspot_name} expects {expected} {plural}, got {len(args)}")
    return construct.template.format(*args)


def _apply_stash(construct: ReverseConstruct, name: str, log: IssueLog, *, object_ref: str) -> None:
    log.add(
        code=construct.issue_code,
        severity=construct.issue_severity,
        message=construct.issue_message.format(name=name),
        object_ref=object_ref,
    )
    return None


# --------------------------------------------------------------------------
# Conditional aggregates and arithmetic helpers — 28 names, all COMPOSE.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Reverse direction ->
# Conditional aggregates and arithmetic helpers".
# --------------------------------------------------------------------------

for _name, _agg in (
    ("sum_if", "SUM"),
    ("count_if", "COUNT"),
    ("average_if", "AVG"),
    ("min_if", "MIN"),
    ("max_if", "MAX"),
    ("stddev_if", "STDDEV"),
    ("variance_if", "VARIANCE"),
):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.COMPOSE,
        template=f"{_agg}(CASE WHEN {{0}} THEN {{1}} END)",
        note=f"{_name} ( cond , x ) -> {_agg}(CASE WHEN cond THEN x END), rule E10.",
    )

REVERSE["unique_count_if"] = ReverseConstruct(
    thoughtspot_name="unique_count_if",
    disposition=ReverseDisposition.COMPOSE,
    template="COUNT(DISTINCT CASE WHEN {0} THEN {1} END)",
    note="unique_count_if ( cond , x ) -> COUNT(DISTINCT CASE WHEN cond THEN x END).",
)

REVERSE["unique count"] = ReverseConstruct(
    thoughtspot_name="unique count",
    disposition=ReverseDisposition.COMPOSE,
    template="COUNT(DISTINCT {0})",
    note="ThoughtSpot's own spelling has a space, not an underscore.",
)

REVERSE["safe_divide"] = ReverseConstruct(
    thoughtspot_name="safe_divide",
    disposition=ReverseDisposition.COMPOSE,
    template="COALESCE({0} / NULLIF({1}, 0), 0)",
    note="The zero-not-null result is preserved by the explicit COALESCE.",
)

REVERSE["pow"] = ReverseConstruct(
    thoughtspot_name="pow", disposition=ReverseDisposition.COMPOSE, template="POWER({0}, {1})",
)
REVERSE["log2"] = ReverseConstruct(
    thoughtspot_name="log2", disposition=ReverseDisposition.COMPOSE, template="LOG(2, {0})",
)
REVERSE["strlen"] = ReverseConstruct(
    thoughtspot_name="strlen", disposition=ReverseDisposition.COMPOSE, template="LENGTH({0})",
)
REVERSE["strpos"] = ReverseConstruct(
    thoughtspot_name="strpos",
    disposition=ReverseDisposition.COMPOSE,
    template="POSITION({1} IN {0})",
    note="ThoughtSpot strpos(s, sub) -> Ossie POSITION(sub IN s); operand order reverses.",
)
REVERSE["substr"] = ReverseConstruct(
    thoughtspot_name="substr",
    disposition=ReverseDisposition.COMPOSE,
    template="SUBSTRING({0}, {1} + 1, {2})",
    note="ThoughtSpot's substr is 0-based; the +1 is mandatory going this way.",
)
REVERSE["left"] = ReverseConstruct(
    thoughtspot_name="left", disposition=ReverseDisposition.COMPOSE, template="LEFT({0}, {1})",
)
REVERSE["right"] = ReverseConstruct(
    thoughtspot_name="right", disposition=ReverseDisposition.COMPOSE, template="RIGHT({0}, {1})",
)

for _name in ("sin", "cos", "tan"):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.COMPOSE,
        template=f"{_name.upper()}(RADIANS({{0}}))",
        note="ThoughtSpot trigonometry is in degrees; the conversion reverses.",
    )
for _name in ("asin", "acos", "atan"):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.COMPOSE,
        template=f"DEGREES({_name.upper()}({{0}}))",
        note="ThoughtSpot's inverse trig functions return degrees.",
    )

REVERSE["to_integer"] = ReverseConstruct(
    thoughtspot_name="to_integer", disposition=ReverseDisposition.COMPOSE, template="CAST({0} AS INTEGER)",
)
REVERSE["to_double"] = ReverseConstruct(
    thoughtspot_name="to_double", disposition=ReverseDisposition.COMPOSE, template="CAST({0} AS DOUBLE)",
)
REVERSE["to_string"] = ReverseConstruct(
    thoughtspot_name="to_string", disposition=ReverseDisposition.COMPOSE, template="CAST({0} AS VARCHAR)",
)
REVERSE["to_date"] = ReverseConstruct(
    thoughtspot_name="to_date",
    disposition=ReverseDisposition.COMPOSE,
    template="TO_DATE({0}, {1})",
    issue_code="E10-FORMAT-TOKENS-PASSTHROUGH",
    issue_severity=Severity.INFO,
    issue_message=(
        "{name}'s format string is passed through verbatim, not mechanically translated "
        "through the TO_DATE/TO_CHAR format-token table — the expression parser this would "
        "need does not exist yet (see task-9-report.md). TO_DATE(s, format) is EXPERIMENTAL "
        "on the Ossie side."
    ),
    note="Judgment call: format-token reversal deferred to Plans C/D's parser.",
)
REVERSE["if"] = ReverseConstruct(
    thoughtspot_name="if",
    disposition=ReverseDisposition.COMPOSE,
    template="CASE WHEN {0} THEN {1} ELSE {2} END",
    note="if ( c ) then a else b -> CASE WHEN c THEN a ELSE b END, or IF(c, a, b).",
)


# --------------------------------------------------------------------------
# Window, LOD and semi-additive functions.
# Source: "Reverse direction -> Window, LOD and semi-additive functions".
# --------------------------------------------------------------------------

def _direction_keyword(literal: str) -> str:
    bare = literal.strip().strip("'\"").lower()
    if not bare.startswith(("asc", "desc")):
        raise ValueError(f"unrecognised rank direction literal: {literal!r}")
    return "DESC" if bare.startswith("desc") else "ASC"


def _compose_rank(args: list[str]) -> str:
    if len(args) != 2:
        raise ValueError(f"rank expects 2 arguments, got {len(args)}")
    agg, direction = args
    return f"RANK() OVER (ORDER BY {agg} {_direction_keyword(direction)})"


def _compose_rank_percentile(args: list[str]) -> str:
    if len(args) != 2:
        raise ValueError(f"rank_percentile expects 2 arguments, got {len(args)}")
    agg, direction = args
    return f"(1.0 - PERCENT_RANK() OVER (ORDER BY {agg} {_direction_keyword(direction)})) * 100"


REVERSE["rank"] = ReverseConstruct(
    thoughtspot_name="rank", disposition=ReverseDisposition.COMPOSE, compose_fn=_compose_rank,
    note="Global, ORDER-BY-only shape only — rank's arity is fixed at exactly two "
         "(live-confirmed), so there is never a partition to lose in this direction.",
)
REVERSE["rank_percentile"] = ReverseConstruct(
    thoughtspot_name="rank_percentile",
    disposition=ReverseDisposition.COMPOSE,
    compose_fn=_compose_rank_percentile,
    note="Scale (0-100 -> 0-1) and inversion both reverse.",
)


def _frame_bound(offset: str) -> str:
    n = int(offset.strip())
    if n > 0:
        return f"{n} PRECEDING"
    if n == 0:
        return "CURRENT ROW"
    return f"{-n} FOLLOWING"


_PARTITION_LOST_ISSUE = (
    "{name}'s emitted OVER clause has no PARTITION BY: ThoughtSpot completes the partition "
    "dynamically from the query's own dimensions minus the order columns, which a static "
    "Ossie window cannot express (rule E13, ask A10). The composed expression is correct "
    "only when the search returns exactly the grain this formula assumed. Per rule E11, "
    "pair this with a THOUGHTSPOT dialect entry carrying the verbatim original."
)


def _compose_moving(agg: str) -> ComposeFn:
    def _compose(args: list[str]) -> str:
        if len(args) < 4:
            raise ValueError(
                f"moving_{agg.lower()} expects at least 4 arguments (m, start, end, order...), "
                f"got {len(args)}"
            )
        m, start, end, *order_cols = args
        order_clause = ", ".join(order_cols)
        return (
            f"{agg}({m}) OVER (ORDER BY {order_clause} "
            f"ROWS BETWEEN {_frame_bound(start)} AND {_frame_bound(end)})"
        )
    return _compose


def _compose_cumulative(agg: str) -> ComposeFn:
    def _compose(args: list[str]) -> str:
        if len(args) < 2:
            raise ValueError(
                f"cumulative_{agg.lower()} expects at least 2 arguments (m, order...), got {len(args)}"
            )
        m, *order_cols = args
        order_clause = ", ".join(order_cols)
        return (
            f"{agg}({m}) OVER (ORDER BY {order_clause} "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
        )
    return _compose


for _agg in ("SUM", "AVERAGE", "MAX", "MIN"):
    _ansi_agg = "AVG" if _agg == "AVERAGE" else _agg
    REVERSE[f"moving_{_agg.lower()}"] = ReverseConstruct(
        thoughtspot_name=f"moving_{_agg.lower()}",
        disposition=ReverseDisposition.PARTIAL,
        compose_fn=_compose_moving(_ansi_agg),
        issue_code="E13-PARTIAL-PARTITION",
        issue_severity=Severity.WARNING,
        issue_message=_PARTITION_LOST_ISSUE,
        note="Frame and order translate exactly; the partition does not (E13/A10).",
    )
    REVERSE[f"cumulative_{_agg.lower()}"] = ReverseConstruct(
        thoughtspot_name=f"cumulative_{_agg.lower()}",
        disposition=ReverseDisposition.PARTIAL,
        compose_fn=_compose_cumulative(_ansi_agg),
        issue_code="E13-PARTIAL-PARTITION",
        issue_severity=Severity.WARNING,
        issue_message=_PARTITION_LOST_ISSUE,
        note="Frame and order translate exactly; the partition does not (E13/A10).",
    )


def _compose_grouped(
    agg_call: str,
    grouping_arg: str,
    filter_arg: str,
    log: IssueLog,
    *,
    object_ref: str,
    source_name: str,
) -> str | None:
    """Shared shape dispatch for `group_aggregate` and its named shorthands.

    Three dispositions live under one ThoughtSpot spelling, distinguished only by the
    grouping/filter arguments' shape (all three shapes are live-confirmed real, per the
    document's "Window rows live-confirmed" section):

    - a `query_filters ( )`-only filter and a fixed `{ ... }` grouping (or `query_groups ( )`
      alone) composes cleanly — this is the one ThoughtSpot windowing form that is clean in
      this direction, because its partition is declared in the formula rather than completed
      from the query;
    - a `query_groups ( ) ± { attr }` dynamic grouping has no expression (the largest
      reverse-direction fidelity gap, ask A10);
    - any filter argument other than `query_filters ( )` has no expression either (filter
      scoping is excluded from Ossie expressions, ask A3).
    """
    grouping = grouping_arg.strip()
    filt = filter_arg.strip()
    if filt != "query_filters ( )":
        log.add(
            code="E10-GROUP-FILTER-SCOPE",
            severity=Severity.ERROR,
            message=(
                f"{source_name}'s filter argument ({filter_arg!r}) scopes the aggregate to "
                "a filtered subset of the query; the specification excludes filter scoping "
                "from expressions (ask A3). Preserved verbatim for roundtrip (rule E11)."
            ),
            object_ref=object_ref,
        )
        return None
    if "query_groups ( )" in grouping and ("-" in grouping or "+" in grouping):
        log.add(
            code="E10-GROUP-DYNAMIC-PARTITION",
            severity=Severity.ERROR,
            message=(
                f"{source_name}'s grouping argument ({grouping_arg!r}) completes the "
                "partition dynamically from the query's own dimensions, which the "
                "specification cannot express (ask A10) — the largest reverse-direction "
                "fidelity gap. Preserved verbatim for roundtrip (rule E11)."
            ),
            object_ref=object_ref,
        )
        return None
    if grouping == "query_groups ( )":
        return agg_call
    if grouping.startswith("{") and grouping.endswith("}"):
        cols = grouping[1:-1].strip()
        return f"{agg_call} OVER ()" if not cols else f"{agg_call} OVER (PARTITION BY {cols})"
    raise ValueError(f"{source_name}: unrecognised grouping argument shape {grouping_arg!r}")


def _dispatch_group_aggregate(
    args: list[str], log: IssueLog, object_ref: str, connection_dialect: str | None
) -> str | None:
    if len(args) != 3:
        raise ValueError(f"group_aggregate expects 3 arguments (agg, grouping, filter), got {len(args)}")
    agg_call, grouping_arg, filter_arg = args
    return _compose_grouped(agg_call, grouping_arg, filter_arg, log, object_ref=object_ref, source_name="group_aggregate")


REVERSE["group_aggregate"] = ReverseConstruct(
    thoughtspot_name="group_aggregate",
    disposition=ReverseDisposition.COMPOSE,
    dispatch_fn=_dispatch_group_aggregate,
    note="Shape dispatch on the grouping/filter arguments — see _compose_grouped.",
)

_GROUP_SHORTHAND_AGGREGATES = {
    # Only the shorthands the document names explicitly (E10's own example, "group_sum",
    # and line ~420's "group_count / group_stddev / group_variance") — no group_average,
    # group_max or group_min is invented, since the document never names them.
    "group_sum": "SUM",
    "group_count": "COUNT",
    "group_stddev": "STDDEV",
    "group_variance": "VARIANCE",
}


def _make_group_shorthand_dispatch(agg: str, source_name: str) -> DispatchFn:
    def _dispatch(args: list[str], log: IssueLog, object_ref: str, connection_dialect: str | None) -> str | None:
        if len(args) != 3:
            raise ValueError(f"{source_name} expects 3 arguments (m, grouping, filter), got {len(args)}")
        m, grouping_arg, filter_arg = args
        return _compose_grouped(f"{agg}({m})", grouping_arg, filter_arg, log, object_ref=object_ref, source_name=source_name)
    return _dispatch


for _name, _agg in _GROUP_SHORTHAND_AGGREGATES.items():
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.COMPOSE,
        dispatch_fn=_make_group_shorthand_dispatch(_agg, _name),
        note=(
            f"Shorthand for group_aggregate({_agg.lower()}(m), ...) — same shape dispatch. "
            "Judgment call: the (m, grouping, filter) 3-argument shape is assumed by analogy "
            "with group_aggregate's live-confirmed form; the shorthand family's own arity "
            "was not independently live-tested (see task-9-report.md)."
        ),
    )

_SEMI_ADDITIVE_ISSUE = (
    "{name} declares a genuine partition and order axis, and that window clause round-trips "
    "faithfully — but semi-additivity is a roll-up declaration (do not re-sum this measure "
    "across the axis), not an expression, and the specification has no such declaration "
    "(ask A12). Preserved verbatim for roundtrip (rule E11)."
)
for _name in ("last_value", "first_value", "last_value_in_period", "first_value_in_period"):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.STASH,
        issue_code="E12-SEMI-ADDITIVE",
        issue_severity=Severity.ERROR,
        issue_message=_SEMI_ADDITIVE_ISSUE,
        note="The window clause itself round-trips; only the roll-up declaration is lost.",
    )


def _dispatch_sql_op(
    args: list[str], log: IssueLog, object_ref: str, connection_dialect: str | None
) -> str | None:
    """The ten `sql_*_op` / `sql_*_aggregate_op` names. `args[0]` is the unquoted template
    body (this module's argument abstraction level — see the module docstring), `args[1:]`
    are the already-resolved column expressions the template's `{0}`, `{1}`, ... refer to.

    Without a known `connection_dialect` there is nothing to build a `dialects[]` entry
    for, and — per the document — the converter must not guess a dialect label, so this
    stashes (ERROR) exactly like any other total loss. With one, the body is rendered as
    static SQL for that dialect's entry and logged as a WARNING (a pass-through is always
    reviewable raw SQL) — never paired with an ANSI_SQL sibling, since the document is
    explicit that this template's portability is exactly what is unknown.
    """
    if not args:
        raise ValueError("a sql_*_op call needs at least its template-body argument")
    body_template, *cols = args
    if connection_dialect is None:
        log.add(
            code="E10-DIALECT-UNKNOWN",
            severity=Severity.ERROR,
            message=(
                "sql_*_op resolves to a dialects[] entry for the connection's own dialect, "
                "which could not be derived from TML here; the converter must not guess a "
                "dialect label. Preserved verbatim for roundtrip (rule E11)."
            ),
            object_ref=object_ref,
        )
        return None
    try:
        body = body_template.format(*cols)
    except (IndexError, KeyError) as exc:
        raise ValueError(
            f"sql_*_op template {body_template!r} does not match {len(cols)} argument(s)"
        ) from exc
    log.add(
        code="E10-DIALECT-PASSTHROUGH",
        severity=Severity.WARNING,
        message=(
            f"Raw {connection_dialect} SQL, emitted as a dialects[] entry for that dialect; "
            "opaque to any consumer that does not implement it. No ANSI_SQL sibling is "
            "emitted — this template's portability is exactly what is unknown. Review "
            "before use."
        ),
        object_ref=object_ref,
    )
    return body


for _name in (
    "sql_string_op", "sql_int_op", "sql_double_op", "sql_bool_op", "sql_date_op",
    "sql_date_time_op", "sql_string_aggregate_op", "sql_int_aggregate_op",
    "sql_number_aggregate_op", "sql_date_time_aggregate_op",
):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.DIALECT,
        dispatch_fn=_dispatch_sql_op,
        note="Resolves to the Ossie dialects[] mechanism for the connection's own dialect, "
             "not a portable expression — the right home for raw warehouse SQL.",
    )


# --------------------------------------------------------------------------
# Runtime, display and calendar concepts.
# Source: "Reverse direction -> Runtime, display and calendar concepts".
# --------------------------------------------------------------------------

REVERSE["<runtime parameter reference>"] = ReverseConstruct(
    thoughtspot_name="<runtime parameter reference>",
    disposition=ReverseDisposition.STASH,
    issue_code="E12-RUNTIME-PARAMETER",
    issue_severity=Severity.ERROR,
    issue_message=(
        "Runtime parameter reference {name} is resolved per-query from user input; the "
        "definitions are stashed at model level (owned by the construct-mapping document). "
        "The expression itself stops being portable once it references a parameter. "
        "Preserved verbatim for roundtrip (rule E11)."
    ),
    note="Synthetic key — not a callable name. See stash_runtime_parameter().",
)


def stash_runtime_parameter(parameter_name: str, log: IssueLog, *, object_ref: str) -> None:
    """The 'Runtime parameter reference' reverse-direction row.

    Not auto-detected inside `translate_thoughtspot`: a bracketed name
    (`[Discount Threshold]`) is syntactically identical to an ordinary column reference
    (`[Table::Column]`), and this module has no model metadata to distinguish the two. The
    caller — which does have the model's declared parameter list — invokes this directly
    once it has confirmed `parameter_name` names a declared parameter, not a column.
    """
    return _apply_stash(REVERSE["<runtime parameter reference>"], parameter_name, log, object_ref=object_ref)


_RUNTIME_IDENTITY_ISSUE = (
    "{name} resolves signed-in-user identity at query time; an interchange document that "
    "carried it would describe an access-control decision, not semantics (construct-mapping "
    "document's NM2). Preserved verbatim for roundtrip (rule E11)."
)
for _name in ("ts_username", "ts_groups", "ts_groups_int", "ts_org", "ts_email_domain", "ts_var"):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.STASH,
        issue_code="E12-RUNTIME-IDENTITY",
        issue_severity=Severity.ERROR,
        issue_message=_RUNTIME_IDENTITY_ISSUE,
    )

_HYPERLINK_MARKUP_TOKENS = ("{caption}", "{/caption}")


def _has_hyperlink_markup(args: list[str]) -> bool:
    return any(token in a for a in args for token in _HYPERLINK_MARKUP_TOKENS)


REVERSE["concat (hyperlink markup)"] = ReverseConstruct(
    thoughtspot_name="concat (hyperlink markup)",
    disposition=ReverseDisposition.STASH,
    issue_code="E12-HYPERLINK-MARKUP",
    issue_severity=Severity.ERROR,
    issue_message=(
        "{name}'s string arguments carry ThoughtSpot's {{caption}}/{{/caption}} hyperlink "
        "display markup; concat itself maps (it has a spec counterpart, CONCAT), but a "
        "consumer that rendered the tags literally would show them to users. Preserved "
        "verbatim for roundtrip (rule E11)."
    ),
    note="Synthetic key, reached only via the content-pattern check in translate_thoughtspot "
         "-- plain concat (no markup) is out of this module's scope entirely.",
)

_FISCAL_MARKERS = {"fiscal", "'fiscal'"}


def _is_fiscal_variant(args: list[str]) -> bool:
    return bool(args) and args[-1].strip().lower() in _FISCAL_MARKERS


_FISCAL_ISSUE_MESSAGE = (
    "{name}'s trailing 'fiscal' argument has no expression: the specification has no "
    "fiscal-calendar concept, and the fiscal year's start month is model-level metadata no "
    "per-expression rewrite can recover (ask A11). Emitting the calendar-year composition "
    "instead would be silently wrong for any organisation whose year does not start in "
    "January. Preserved verbatim for roundtrip (rule E11)."
)


def _stash_fiscal_variant(name: str, log: IssueLog, *, object_ref: str) -> None:
    log.add(
        code="E11-FISCAL-CALENDAR",
        severity=Severity.ERROR,
        message=_FISCAL_ISSUE_MESSAGE.format(name=name),
        object_ref=object_ref,
    )
    return None


_LOCALE_ISSUE_MESSAGE = (
    "{name} composes via TO_CHAR, which is EXPERIMENTAL on the Ossie side, and its name "
    "tokens are locale-dependent by the specification's own admission. Review the target "
    "locale before relying on this column."
)
for _name, _fmt in (("month", "MONTH"), ("year_name", "YYYY"), ("day_of_week", "DAY")):
    REVERSE[_name] = ReverseConstruct(
        thoughtspot_name=_name,
        disposition=ReverseDisposition.COMPOSE,
        template=f"TO_CHAR({{0}}, '{_fmt}')",
        issue_code="E10-LOCALE-DEPENDENT",
        issue_severity=Severity.WARNING,
        issue_message=_LOCALE_ISSUE_MESSAGE,
        note="Name-returning form, distinct from month_number/year/day_number_of_week.",
    )

REVERSE["month_number_of_quarter"] = ReverseConstruct(
    thoughtspot_name="month_number_of_quarter",
    disposition=ReverseDisposition.COMPOSE,
    template="MOD(MONTH({0}) - 1, 3) + 1",
)
REVERSE["day_number_of_quarter"] = ReverseConstruct(
    thoughtspot_name="day_number_of_quarter",
    disposition=ReverseDisposition.COMPOSE,
    template="DATEDIFF(day, DATE_TRUNC('quarter', {0}), {0}) + 1",
)

_WEEK_START_ISSUE = (
    "{name} is correct only if the target engine's week start agrees with the "
    "specification's fixed Monday start; ThoughtSpot's week start is an instance setting. "
    "Verify alignment before relying on this column."
)
REVERSE["week_number_of_month"] = ReverseConstruct(
    thoughtspot_name="week_number_of_month",
    disposition=ReverseDisposition.COMPOSE,
    template="DATEDIFF(week, DATE_TRUNC('month', {0}), {0}) + 1",
    issue_code="E10-WEEK-START-ASSUMED",
    issue_severity=Severity.WARNING,
    issue_message=_WEEK_START_ISSUE,
)
REVERSE["week_number_of_quarter"] = ReverseConstruct(
    thoughtspot_name="week_number_of_quarter",
    disposition=ReverseDisposition.COMPOSE,
    template="DATEDIFF(week, DATE_TRUNC('quarter', {0}), {0}) + 1",
    issue_code="E10-WEEK-START-ASSUMED",
    issue_severity=Severity.WARNING,
    issue_message=_WEEK_START_ISSUE,
)
REVERSE["is_weekend"] = ReverseConstruct(
    thoughtspot_name="is_weekend",
    disposition=ReverseDisposition.COMPOSE,
    template="DATE_PART('dayofweek', {0}) IN (6, 7)",
    issue_code="E10-DAYOFWEEK-BASE",
    issue_severity=Severity.WARNING,
    issue_message=(
        "{name}'s member list (6, 7) uses ThoughtSpot's own DAYOFWEEK base (1 = Monday); "
        "the specification does not fix a base and engines disagree (ask A11) — confirm "
        "the target engine's base agrees before relying on this column."
    ),
)
REVERSE["start_of_hour"] = ReverseConstruct(
    thoughtspot_name="start_of_hour", disposition=ReverseDisposition.COMPOSE,
    template="DATE_TRUNC('hour', {0})",
)
REVERSE["start_of_min"] = ReverseConstruct(
    thoughtspot_name="start_of_min", disposition=ReverseDisposition.COMPOSE,
    template="DATE_TRUNC('minute', {0})",
)
REVERSE["date"] = ReverseConstruct(
    thoughtspot_name="date", disposition=ReverseDisposition.COMPOSE,
    template="DATE_TRUNC('day', {0})",
)
REVERSE["time"] = ReverseConstruct(
    thoughtspot_name="time", disposition=ReverseDisposition.COMPOSE,
    template="CAST({0} AS TIME)",
)


def _compose_variadic(fn: str) -> ComposeFn:
    def _compose(args: list[str]) -> str:
        if not args:
            raise ValueError(f"{fn} expects at least one argument")
        return f"{fn}({', '.join(args)})"
    return _compose


REVERSE["greatest"] = ReverseConstruct(
    thoughtspot_name="greatest",
    disposition=ReverseDisposition.COMPOSE,
    compose_fn=_compose_variadic("GREATEST"),
    note="Never MAX — that would turn a row-wise attribute into an aggregate measure.",
)
REVERSE["least"] = ReverseConstruct(
    thoughtspot_name="least",
    disposition=ReverseDisposition.COMPOSE,
    compose_fn=_compose_variadic("LEAST"),
    note="Never MIN, for the same reason.",
)


# --------------------------------------------------------------------------
# The dispatcher.
# --------------------------------------------------------------------------

def translate_thoughtspot(
    name: str,
    args: list[str],
    log: IssueLog,
    *,
    object_ref: str,
    connection_dialect: str | None = None,
) -> str | None:
    """Translate one ThoughtSpot-only construct call into an Ossie expression, or stash it.

    Returns the composed Ossie expression string, or `None` when the construct stashes (an
    issue is always logged in that case, rule E12) or when `name` has no entry in this
    module's reverse inventory at all (nothing is logged — that name is either a plain
    column/measure reference or a construct with a spec counterpart already covered by the
    forward `CATALOG`, neither of which is this module's concern).

    See the module docstring for the two cross-cutting checks below (fiscal-calendar
    argument, concat hyperlink markup) and for why `object_ref` and `connection_dialect` are
    keyword-only additions beyond the brief's literal three-argument signature.
    """
    if _is_fiscal_variant(args):
        _stash_fiscal_variant(name, log, object_ref=object_ref)
        return None

    if name == "concat" and _has_hyperlink_markup(args):
        return _apply_stash(REVERSE["concat (hyperlink markup)"], name, log, object_ref=object_ref)

    construct = REVERSE.get(name)
    if construct is None:
        return None

    if construct.dispatch_fn is not None:
        return construct.dispatch_fn(args, log, object_ref, connection_dialect)

    if construct.disposition is ReverseDisposition.STASH:
        return _apply_stash(construct, name, log, object_ref=object_ref)

    expression = _render(construct, args)
    if construct.issue_message:
        log.add(
            code=construct.issue_code,
            severity=construct.issue_severity,
            message=construct.issue_message.format(name=name),
            object_ref=object_ref,
        )
    return expression


# --------------------------------------------------------------------------
# E11/P8 — dialect-entry and custom_extensions helpers.
#
# translate_thoughtspot's own return type is `str | None` (the brief's contract), so it
# cannot itself hand back a dialects[] entry or a custom_extensions payload — those are
# object-level document concerns, one level above a single expression. These three helpers
# are what a caller (Plan C/D, which does operate at the object level) combines with
# translate_thoughtspot's result to satisfy rule E11 and learnings P8 in full.
# --------------------------------------------------------------------------

def thoughtspot_dialect_entry(name: str, args: list[str]) -> dict[str, str]:
    """Rule E11 — the verbatim ThoughtSpot call, reconstructed textually (this module never
    has the original formula's exact whitespace, only the parsed name/args) so a PARTIAL or
    STASH construct still round-trips losslessly through a THOUGHTSPOT dialect entry even
    where no full — or no — portable Ossie expression exists.
    """
    return {"dialect": DIALECT, "expression": f"{name} ( {' , '.join(args)} )"}


def portable_dialect_entry(expression: str) -> dict[str, str]:
    """Learnings P8 — pair the THOUGHTSPOT dialect entry with a PORTABLE_DIALECT (ANSI_SQL)
    sibling wherever the expression alongside it is itself portable. Applies to PARTIAL rows
    (the frame/order composition is genuine, portable ANSI SQL, just an incomplete window) —
    never to a pure STASH (there is no portable expression to pair) and never to the
    `sql_*_op` DIALECT family (the document is explicit: no ANSI_SQL sibling is emitted
    there, because that template's portability is exactly what is unknown).
    """
    return {"dialect": PORTABLE_DIALECT, "expression": expression}


def custom_extensions_fragment(name: str, args: list[str]) -> dict[str, dict[str, str]]:
    """The payload fragment this module contributes toward an object's
    `custom_extensions[VENDOR_KEY]` entry (`stash.write_stash`, rule X1) for one construct
    this module could not fully compose. This module operates at the single-expression
    level and has no access to the enclosing object, so the caller merges fragments across
    an object's columns (typically keyed by the Ossie metric/column name) before calling
    `stash.write_stash` once per object.
    """
    return {VENDOR_KEY: {"reverse_thoughtspot_call": f"{name} ( {' , '.join(args)} )"}}
