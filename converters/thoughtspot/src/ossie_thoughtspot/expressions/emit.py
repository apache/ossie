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


"""Render a catalog `Construct` into an actual ThoughtSpot formula.

Three emitters, one per `Classification` (Task 1's `_types.py`):

- `emit_direct`      — substitutes `args` into the construct's native ThoughtSpot
                        template positionally. Rule E2: a `direct` row may itself be
                        a composition of native functions, not only a rename — that
                        composition is baked into `construct.template` by the family
                        tasks (3-8), not by this function.
- `emit_passthrough` — renders a `sql_*_op` call. Rule E4/E7: the row's `variant`
                        fixes both the emitted function name and, through it, the
                        emitted column's type and measure/attribute role. Every call
                        raises a WARNING issue (E12: names the function and the
                        object) because the body is raw, dialect-specific warehouse
                        SQL, opaque to ThoughtSpot's query planner. Rule E9: a call
                        that would carry a runtime ThoughtSpot parameter is refused
                        outright — it cannot resolve to static SQL, so it is not
                        portable in either direction, and the caller must route it
                        elsewhere (a THOUGHTSPOT-only dialect entry) instead of
                        obtaining a formula string from this function. Rule E8: pass
                        `partition_column` when the passthrough carries a
                        `PARTITION BY` and the wrapped result guarantees that column
                        reaches ThoughtSpot's GROUP BY regardless of what the user's
                        search selects — enforced by a template/kwarg cross-check in
                        both directions, not left to caller convention.
- `emit_unmappable`  — no formula exists; raises an ERROR issue and returns nothing
                        (the two-bucket rule: never a silent drop — the caller is
                        responsible for preserving the construct in custom_extensions).

Two details that are easy to get subtly wrong (both pinned by tests in test_emit.py):

- `emit_passthrough` does NOT substitute `args` into the SQL body. The body is
  rendered as a quoted *template string*, followed by the arguments as separate
  `sql_*_op` positional arguments — ThoughtSpot resolves the `{0}`, `{1}`, ...
  placeholders itself at formula-evaluation time. Substituting them here would
  produce a formula that looks right and is wrong.
- `emit_direct` DOES substitute positionally (via `str.format`), and rejects an
  argument-count mismatch rather than silently dropping or reusing an argument,
  which would compute the wrong thing while still importing cleanly.
"""
import json
import re

from ._types import Classification, Construct
from ..issues import IssueLog, Severity

_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


def _placeholder_count(template: str) -> int:
    """How many distinct positional `{n}` placeholders a template declares.

    Assumes contiguous 0-based indices (`{0}`, `{1}`, ...), which is the only
    shape `str.format(*args)` accepts positionally and the only shape any
    catalog template uses.
    """
    indices = {int(m) for m in _PLACEHOLDER_RE.findall(template)}
    return max(indices) + 1 if indices else 0


def emit_direct(construct: Construct, args: list[str]) -> str:
    """Render a DIRECT construct: substitute `args` into its template positionally.

    Raises ValueError if `construct` is not classified DIRECT, or if `args` does
    not have exactly the number of positional arguments the template declares —
    silently dropping or reusing an argument would produce a formula that imports
    cleanly and computes the wrong thing.
    """
    if construct.classification is not Classification.DIRECT:
        raise ValueError(
            f"{construct.spec_name}: emit_direct called on a "
            f"{construct.classification.value} construct, not direct"
        )
    expected = _placeholder_count(construct.template)
    if len(args) != expected:
        plural = "argument" if expected == 1 else "arguments"
        raise ValueError(
            f"{construct.spec_name} expects {expected} {plural}, got {len(args)}"
        )
    return construct.template.format(*args)


def emit_passthrough(
    construct: Construct,
    args: list[str],
    log: IssueLog,
    *,
    object_ref: str,
    has_parameter: bool = False,
    partition_column: str | None = None,
) -> str:
    """Render a PASSTHROUGH construct as a `sql_*_op` call and log a warning (E4/E7/E12).

    `has_parameter=True` (E9) refuses the call outright: a `sql_*_op` whose
    arguments include a ThoughtSpot parameter cannot resolve to static SQL, so it
    is not portable in either direction. The caller must not obtain a formula
    string from this function in that case — it routes the construct to a
    THOUGHTSPOT-only dialect entry instead.

    `partition_column` (E8): when the pass-through's SQL carries a `PARTITION BY`,
    pass the column it partitions on and the result comes back wrapped in
    `group_aggregate ( <passthrough> , query_groups ( ) + { <partition_column> } ,
    query_filters ( ) )`, so the partition column reaches ThoughtSpot's GROUP BY
    even when the user's search omits it. This is enforced, not left to caller
    convention: a template that carries `PARTITION BY` (case-insensitive) but no
    `partition_column` raises, and a `partition_column` supplied for a template
    with no `PARTITION BY` raises too — a mis-transcribed catalog row (Tasks 3-8)
    fails loudly here instead of silently emitting an unwrapped, only-sometimes-
    correct pass-through.
    """
    if construct.classification is not Classification.PASSTHROUGH:
        raise ValueError(
            f"{construct.spec_name}: emit_passthrough called on a "
            f"{construct.classification.value} construct, not passthrough"
        )
    if has_parameter:
        raise ValueError(
            f"{construct.spec_name}: a passthrough cannot carry a runtime parameter "
            "(E9) — it cannot resolve to static SQL"
        )

    # E8, enforced rather than left to caller convention: every passthrough row
    # that needs the group_aggregate wrap carries the literal string "PARTITION BY"
    # in its SQL template (ROW_NUMBER, LAG, LEAD, the OVER fallback, window
    # aggregation, and the RANK/PERCENT_RANK/CUME_DIST fallbacks all do). Checking
    # the template against the kwarg in both directions turns "Tasks 3-8 must
    # remember to pass this" into something this function refuses to get wrong.
    carries_partition_by = "partition by" in construct.template.lower()
    if carries_partition_by and partition_column is None:
        raise ValueError(
            f"{construct.spec_name}: template carries PARTITION BY but no "
            "partition_column was supplied — the E8 group_aggregate wrapper is required"
        )
    if partition_column is not None and not carries_partition_by:
        raise ValueError(
            f"{construct.spec_name}: partition_column was supplied but the template "
            "carries no PARTITION BY — there is nothing to wrap"
        )

    # E4: variant is guaranteed non-None for a PASSTHROUGH row by Construct.__post_init__.
    variant = construct.variant
    quoted_template = json.dumps(construct.template)
    body = " , ".join([quoted_template, *args])
    call = f"{variant.value} ( {body} )"

    log.add(
        code="E7-PASSTHROUGH",
        severity=Severity.WARNING,
        message=(
            f"{construct.spec_name} is emitted as a {variant.value} pass-through: "
            "raw warehouse SQL, opaque to ThoughtSpot's query planner. Review before use."
        ),
        object_ref=object_ref,
    )

    if partition_column is not None:
        return (
            f"group_aggregate ( {call} , "
            f"query_groups ( ) + {{ {partition_column} }} , query_filters ( ) )"
        )
    return call


def emit_unmappable(construct: Construct, log: IssueLog, *, object_ref: str) -> None:
    """Raise an ERROR issue for an UNMAPPABLE construct. Never a silent drop (E12).

    Returns nothing — the caller is responsible for preserving the construct in
    `custom_extensions` for roundtrip; that stash is out of this function's scope.
    """
    if construct.classification is not Classification.UNMAPPABLE:
        raise ValueError(
            f"{construct.spec_name}: emit_unmappable called on a "
            f"{construct.classification.value} construct, not unmappable"
        )
    log.add(
        code="E12-UNMAPPABLE",
        severity=Severity.ERROR,
        message=(
            f"{construct.spec_name} has no ThoughtSpot representation; "
            "preserved in custom_extensions for roundtrip."
        ),
        object_ref=object_ref,
    )
    return None
