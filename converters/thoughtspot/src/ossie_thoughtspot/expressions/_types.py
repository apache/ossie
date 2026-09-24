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

"""The small shared vocabulary the catalog, emitters and reverse map all use."""
from dataclasses import dataclass
import re

from enum import Enum


class Classification(str, Enum):
    """How a specification construct reaches ThoughtSpot.

    DIRECT      a native ThoughtSpot equivalent exists, possibly as a documented
                composition of native functions.
    PASSTHROUGH requires a sql_*_op pass-through: warehouse-dialect-specific, and
                opaque to ThoughtSpot's query planner.
    UNMAPPABLE  no representation; the converter raises an issue and preserves the
                construct in custom_extensions. Never a silent drop.
    """

    DIRECT = "direct"
    PASSTHROUGH = "passthrough"
    UNMAPPABLE = "unmappable"


class Variant(str, Enum):
    """The sql_*_op family. The variant fixes the emitted column's type AND
    its measure/attribute role. The scalar variants produce attributes; the
    *_aggregate_op variants produce measures. Emitting sql_int_op where
    sql_int_aggregate_op was needed yields a column that imports cleanly and then
    aggregates wrongly — worse than a rejected import.
    """

    BOOL = "sql_bool_op"
    DATE_TIME = "sql_date_time_op"
    DOUBLE = "sql_double_op"
    INT = "sql_int_op"
    NUMBER = "sql_number_op"
    STRING = "sql_string_op"
    INT_AGGREGATE = "sql_int_aggregate_op"
    NUMBER_AGGREGATE = "sql_number_aggregate_op"
    # Declared for completeness of the *_aggregate_op family even though no
    # catalog row targets them today: `tml_to_ossie` derives "what already
    # aggregates" by filtering this enum on the `_aggregate_op` suffix, so a
    # missing member there is a missed double-aggregation guard, not merely an
    # absent rendering option. Both are named in the reverse inventory.
    STRING_AGGREGATE = "sql_string_aggregate_op"
    DATE_TIME_AGGREGATE = "sql_date_time_aggregate_op"


class VariadicStyle(str, Enum):
    """How a construct that takes any number of arguments is rendered.

    A fixed `{0} , {1}` template cannot express one: it either rejects the real
    arity or -- worse -- bakes a literal `...` into the output, which is how
    `concat ( {0} , {1} , ... )` came to emit `concat ( [T::A] , [T::B] , ... )`
    as if that were a formula.
    """

    #: `{*}` is replaced by the argument tail, joined with " , ". The whole
    #: list lives inside one call: `concat ( a , b , c )`.
    JOIN = "join"
    #: The template is BINARY and applied right-associatively over the whole
    #: list, for a ThoughtSpot function that has no n-ary form:
    #: `ifnull ( a , ifnull ( b , c ) )`.
    FOLD = "fold"


@dataclass(frozen=True)
class Variadic:
    """The arity rule for a construct whose argument count is not fixed.

    `min_args`  fewest arguments that render to something meaningful (2 for
                every construct here -- a one-argument CONCAT or COALESCE is
                the argument itself, not a call).
    `tail_from` index at which the repeating tail begins, for JOIN. `IN` keeps
                argument 0 positional (the column) and repeats from 1.
    """

    style: VariadicStyle
    min_args: int = 2
    tail_from: int = 0


#: A `{0}`-style placeholder, removed before scanning for baked-in constants so
#: the digits inside one are never mistaken for a literal.
_ARGUMENT_PLACEHOLDER_RE = re.compile(r"\{\d+\}")
#: A bare number or quoted string sitting in an argument position.
_BAKED_LITERAL_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])|'[^']*'")
#: An aggregate CALL baked into a template. `SUM` in
#: `NTILE(4) OVER (ORDER BY SUM({0}))` stands for "whatever the caller is ranking
#: by" exactly as the `4` stands for `n`, but it is a bare keyword rather than a
#: number or a quoted string, so `_BAKED_LITERAL_RE` cannot see it. Five rows sat
#: in that state undeclared while passing the literal gate, and the generated
#: document offered `SUM` as the mapping for a row whose own `spec_name` says
#: `AGG(expr)`.
_BAKED_AGGREGATE_RE = re.compile(
    r"(?<![\w.])(SUM|AVG|COUNT|MIN|MAX|MEDIAN|STDDEV|STDDEV_POP|STDDEV_SAMP"
    r"|VARIANCE|VAR_POP|VAR_SAMP)\s*\(",
    re.IGNORECASE,
)


def baked_literals(template: str | None) -> list[str]:
    """Every bare number or quoted string `template` holds in an argument position."""
    return _BAKED_LITERAL_RE.findall(_ARGUMENT_PLACEHOLDER_RE.sub("", template or ""))


def baked_aggregates(template: str | None, spec_name: str) -> set[str]:
    """Aggregate calls `template` bakes in that `spec_name` does not itself name.

    A row whose specification IS the aggregate (`SUM(expr)`) is not baking an
    exemplar in by writing `SUM` -- it is naming its own construct. A row whose
    specification is `NTILE(n) OVER (...)`, `DENSE_RANK() OVER (...)` or
    `Window aggregation -- AGG(expr) OVER (...)` and whose template nonetheless
    says `SUM` has picked one aggregate to illustrate with, and has to say so.
    """
    body = _ARGUMENT_PLACEHOLDER_RE.sub("", template or "")
    own = spec_name.split("(")[0].strip().upper()
    return {
        match.group(1).upper()
        for match in _BAKED_AGGREGATE_RE.finditer(body)
        if match.group(1).upper() != own
    }


def declares_an_aggregate_exemplar(exemplar_literals: tuple[str, ...]) -> bool:
    """Whether some declared exemplar names an aggregate as the thing baked in.

    Deliberately a substring test rather than an enum: these declarations are
    free text destined for a generated document ("the ordering aggregate"), and
    the check only has to be strong enough that a row baking `SUM` in cannot
    satisfy it by declaring something unrelated, the way `NTILE` satisfied the
    literal gate with `n` while its `SUM` went unmentioned.
    """
    return any("aggregate" in entry.lower() for entry in exemplar_literals)


@dataclass(frozen=True)
class Construct:
    """One row of the function-mapping document.

    `spec_name`   the construct as the specification writes it, e.g. "SUM(expr)".
    `template`    for DIRECT, the ThoughtSpot formula with {0}, {1}... placeholders;
                  for PASSTHROUGH, the SQL body passed to the variant; None if UNMAPPABLE.

                  Not every DIRECT/PASSTHROUGH template is a complete, positionally
                  substitutable one — two shapes diverge from that default, and both fail
                  loud (a raised ValueError, or rejection at TML import) rather than
                  silently producing a wrong answer:

                  - A "dispatch" template — literal text such as "per-type — see note" or
                    "per-pattern-shape — see note" — for a row whose actual ThoughtSpot
                    rendering depends on a runtime value not known at catalog-construction
                    time (CAST's per-type table, the EXTRACT/DATE_PART/DATE_TRUNC/DATEADD
                    family, TRUE/FALSE, both CASE forms, the column/metric reference, the
                    unary +/- row, and several window rows). A caller building a uniform
                    `.format()` dispatcher off this field alone will hit these ~20 rows and
                    must special-case them; each row's `note` says so and describes the
                    real dispatch.
                  - An "exemplar" PASSTHROUGH template — a complete, renderable body that
                    bakes ONE caller-supplied value in as a literal while still declaring a
                    satisfiable arity (`PERCENTILE_CONT`/`DISC`'s `0.75`, `NTILE`'s `4`,
                    `LAG`/`LEAD`'s offset `1`, and others — see `emit_passthrough`'s
                    docstring for the full convention). This kind renders without error, so
                    the arg-count guard alone does not distinguish it from a genuinely
                    complete template: treating the baked-in literal as universal instead of
                    rebuilding the template per real occurrence is silently wrong, not
                    loud — `PERCENTILE_CONT(0.9)` would render as a P75 measure that imports
                    and runs. Each such row's `note` names the baked-in value.
    `variant`     required for PASSTHROUGH, forbidden otherwise.
    `note`        the row's caveat, verbatim enough to be traceable to the document.
    """

    spec_name: str
    classification: Classification
    template: str | None = None
    variant: Variant | None = None
    note: str = ""
    variadic: Variadic | None = None
    exemplar_literals: tuple[str, ...] = ()

    #: Names of the parameters this row's template bakes in as a LITERAL rather
    #: than exposing as a placeholder -- `PERCENTILE_CONT`'s `p` rendered as
    #: `0.75`, `NTILE`'s `n` as `4`, `LAG`'s `offset` as `1`. Such a row renders
    #: without error, so the arity guard cannot tell it from a complete
    #: template, and the generated reference document presented the example
    #: value as if it were the mapping. Declaring it here is what lets the
    #: document say so, and what lets the gate below refuse a NEW undeclared one.

    def __post_init__(self) -> None:
        if self.variadic is not None:
            if self.classification is Classification.UNMAPPABLE:
                raise ValueError(f"{self.spec_name}: an unmappable row has no arity to vary")
            if self.variadic.style is VariadicStyle.JOIN and "{*}" not in (self.template or ""):
                raise ValueError(
                    f"{self.spec_name}: a join-variadic template must carry the {{*}} tail marker"
                )
            if self.variadic.style is VariadicStyle.FOLD and "{*}" in (self.template or ""):
                raise ValueError(
                    f"{self.spec_name}: a fold-variadic template is binary and takes no {{*}}"
                )
        # A literal "..." is how this defect shipped: every DIRECT variadic row
        # carried the specification's own ellipsis notation straight into its
        # template, where `str.format` never fills it, so a two-argument CONCAT
        # rendered `concat ( a , b , ... )` -- syntactically invalid, and caught
        # by nothing because the catalog sweep only asserted emission did not
        # raise. Rejecting it here means a new row cannot reintroduce it.
        # Fail-closed on baked-in literals: a PASSTHROUGH body carrying a bare
        # numeric or quoted constant in an argument position is either an
        # exemplar -- which must SAY so -- or a defect. Eleven rows were the
        # former and said nothing, so the generated reference document offered
        # `NTILE(4)` as the mapping for `NTILE(n)` with no sign that the 4 was
        # illustrative. An allowlist, not a blocklist: a new row is refused
        # until it declares itself.
        if self.classification is Classification.PASSTHROUGH:
            if not self.exemplar_literals:
                baked = baked_literals(self.template)
                if baked:
                    raise ValueError(
                        f"{self.spec_name}: template bakes in literal(s) {baked} while "
                        f"declaring no exemplar_literals. Name the parameter(s) the "
                        f"literal stands for, or parameterise the template"
                    )
            # The same rule for a baked-in AGGREGATE, which the literal scan above
            # cannot see. Checked independently of `exemplar_literals` being empty:
            # `NTILE` declared `n` and so satisfied the literal gate while the `SUM`
            # its template also bakes in went unmentioned, which is exactly the state
            # this catches.
            aggregates = baked_aggregates(self.template, self.spec_name)
            if aggregates and not declares_an_aggregate_exemplar(self.exemplar_literals):
                raise ValueError(
                    f"{self.spec_name}: template bakes in the aggregate(s) "
                    f"{sorted(aggregates)} while no declared exemplar names an "
                    f"aggregate. Add one that does (e.g. 'the ordering aggregate'), "
                    f"or parameterise the template"
                )
        if self.template and "..." in self.template:
            raise ValueError(
                f"{self.spec_name}: a template cannot contain a literal '...'; a construct "
                f"that takes any number of arguments declares `variadic=` instead"
            )
        if self.classification is Classification.PASSTHROUGH and self.variant is None:
            raise ValueError(f"{self.spec_name}: a passthrough row must name its variant")
        if self.classification is not Classification.PASSTHROUGH and self.variant is not None:
            raise ValueError(f"{self.spec_name}: only a passthrough row may name a variant")
        if self.classification is Classification.UNMAPPABLE and self.template is not None:
            raise ValueError(f"{self.spec_name}: an unmappable row has no template")
        if self.classification is not Classification.UNMAPPABLE and not self.template:
            raise ValueError(
                f"{self.spec_name}: a {self.classification.value} row must have a template"
            )
        # A PASSTHROUGH template holds only the bare inner SQL body (e.g.
        # "LOWER({0})") — emit_passthrough builds the `variant.value ( "..." , args )`
        # wrapper itself. A template that already contains its own variant call
        # (e.g. 'sql_string_op ( "LOWER({0})" , {0} )', copied verbatim from the
        # mapping document's ThoughtSpot-column cell) double-wraps at emission time:
        # `sql_string_op ( "sql_string_op ( ""LOWER({0})"" , {0} )" , {0} )`. That
        # reads as fine in the catalog file and is wrong the moment it runs — a real
        # transcription mistake this check exists to catch. Matching on
        # "{variant} (" (the space and paren) rather than a bare substring guards
        # against a coincidental token inside a legitimate body; a `sql_*_op` name
        # is a ThoughtSpot-side synthetic formula-function name, so it cannot
        # legitimately appear inside raw warehouse SQL either.
        if self.classification is Classification.PASSTHROUGH:
            marker = f"{self.variant.value} ("
            if marker in self.template:
                raise ValueError(
                    f"{self.spec_name}: passthrough template already contains "
                    f"'{marker}' — the template must hold only the bare inner SQL "
                    "body; emit_passthrough builds the variant(...) wrapper itself, "
                    "so this template would double-wrap at emission time"
                )
