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
from enum import Enum


class Classification(str, Enum):
    """How a specification construct reaches ThoughtSpot.

    DIRECT      a native ThoughtSpot equivalent exists, possibly as a documented
                composition of native functions (rule E2).
    PASSTHROUGH requires a sql_*_op pass-through: warehouse-dialect-specific, and
                opaque to ThoughtSpot's query planner.
    UNMAPPABLE  no representation; the converter raises an issue and preserves the
                construct in custom_extensions. Never a silent drop.
    """

    DIRECT = "direct"
    PASSTHROUGH = "passthrough"
    UNMAPPABLE = "unmappable"


class Variant(str, Enum):
    """The sql_*_op family. Rule E7: the variant fixes the emitted column's type AND
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
    `variant`     required for PASSTHROUGH (rule E4), forbidden otherwise.
    `note`        the row's caveat, verbatim enough to be traceable to the document.
    """

    spec_name: str
    classification: Classification
    template: str | None = None
    variant: Variant | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.classification is Classification.PASSTHROUGH and self.variant is None:
            raise ValueError(f"{self.spec_name}: a passthrough row must name its variant (E4)")
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
