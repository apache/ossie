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


"""Tests for the expression emitters.

Three emitter signatures, one per `Classification`:

    emit_direct(construct, args) -> str
    emit_passthrough(construct, args, log, *, object_ref, has_parameter=False) -> str
    emit_unmappable(construct, log, *, object_ref) -> None

`object_ref` is required on both issue-raising emitters (rule E12: an issue names
the function, the object and the reason — an emitter that cannot name the object
structurally cannot satisfy it).
"""
import re

import pytest

from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Construct, Variant
from ossie_thoughtspot.expressions.emit import _placeholder_count, emit_direct, emit_passthrough, emit_unmappable
from ossie_thoughtspot.issues import IssueLog, Severity

SUM = Construct("SUM(expr)", Classification.DIRECT, template="sum ( {0} )")
STDDEV_POP = Construct(
    "STDDEV_POP(expr)", Classification.PASSTHROUGH,
    template="STDDEV_POP({0})", variant=Variant.NUMBER_AGGREGATE,
)


def test_emit_direct_substitutes_positionally():
    assert emit_direct(SUM, ["[ORDERS::Amount]"]) == "sum ( [ORDERS::Amount] )"


def test_emit_direct_rejects_an_argument_count_mismatch():
    # Silently dropping or reusing an argument would produce a formula that imports
    # and computes the wrong thing.
    with pytest.raises(ValueError, match="expects 1 argument"):
        emit_direct(SUM, ["a", "b"])


def test_emit_direct_refuses_a_non_direct_construct():
    with pytest.raises(ValueError, match="STDDEV_POP.*direct"):
        emit_direct(STDDEV_POP, ["[x]"])


def test_emit_passthrough_wraps_the_body_in_its_variant():
    log = IssueLog()
    out = emit_passthrough(STDDEV_POP, ["[ORDERS::Amount]"], log, object_ref="metric:Revenue")
    assert out == 'sql_number_aggregate_op ( "STDDEV_POP({0})" , [ORDERS::Amount] )'


def test_emit_passthrough_always_raises_a_warning_issue():
    # The mapping document requires every pass-through to surface, because it embeds
    # raw warehouse SQL and is opaque to ThoughtSpot's query planner.
    log = IssueLog()
    emit_passthrough(STDDEV_POP, ["[ORDERS::Amount]"], log, object_ref="metric:Revenue")
    assert log.count_by_severity() == {"WARNING": 1}
    issue = log.as_dicts()[0]
    assert "STDDEV_POP" in issue["message"]          # E12: names the function
    assert issue["object_ref"]                        # E12: names the object


def test_emit_passthrough_refuses_a_runtime_parameter():
    # E9. A sql_*_op whose arguments include a ThoughtSpot parameter cannot resolve to
    # static SQL, so it is not portable in either direction.
    log = IssueLog()
    with pytest.raises(ValueError, match="runtime parameter"):
        emit_passthrough(
            STDDEV_POP, ["[Threshold Parameter]"], log,
            object_ref="metric:Revenue", has_parameter=True,
        )
    # The raise must precede any log.add — a refused call must never also emit a
    # misleading WARNING that tells the user to "review" a formula they never got.
    assert log.as_dicts() == []


def test_emit_passthrough_refuses_a_non_passthrough_construct():
    with pytest.raises(ValueError, match="SUM.*passthrough"):
        emit_passthrough(SUM, ["[x]"], IssueLog(), object_ref="metric:Revenue")


def test_emit_passthrough_rejects_an_argument_count_mismatch():
    # emit_direct already refuses a mismatch (test_emit_direct_rejects_an_argument_count_
    # mismatch above); emit_passthrough previously had no equivalent guard. Reproduces the
    # concrete failure on a real catalog row: TIMESTAMP_NTZ's template is a zero-placeholder
    # exemplar (its literal 2024-01-15 date is baked in — see the Construct.template
    # docstring's "exemplar" case), so passing it a real value silently appended an unused
    # sql_date_time_op argument the template never consumes, keeping the hardcoded date in
    # the rendered SQL instead of failing loudly.
    literal_timestamp = CATALOG["TIMESTAMP_NTZ '2024-01-15 10:30:00'"]
    log = IssueLog()
    with pytest.raises(ValueError, match="expects 0 arguments"):
        emit_passthrough(
            literal_timestamp, ["'2026-03-04 09:00:00'"], log, object_ref="metric:X",
        )
    # Same discipline as the E9 refusal above: no misleading WARNING for a call that
    # was refused.
    assert log.as_dicts() == []


def test_emit_passthrough_renders_the_exemplar_with_its_own_natural_arity():
    literal_timestamp = CATALOG["TIMESTAMP_NTZ '2024-01-15 10:30:00'"]
    log = IssueLog()
    out = emit_passthrough(literal_timestamp, [], log, object_ref="metric:X")
    assert out == 'sql_date_time_op ( "CAST(\'2024-01-15 10:30:00\' AS TIMESTAMP)" )'


def test_emit_unmappable_raises_an_issue_and_returns_nothing():
    c = Construct("EXISTS_IN(x)", Classification.UNMAPPABLE)
    log = IssueLog()
    assert emit_unmappable(c, log, object_ref="metric:Revenue") is None
    issue = log.as_dicts()[0]
    assert issue["severity"] == Severity.ERROR.value
    assert "EXISTS_IN" in issue["message"] and "metric:Revenue" == issue["object_ref"]


def test_emit_unmappable_refuses_a_mappable_construct():
    with pytest.raises(ValueError, match="SUM.*unmappable"):
        emit_unmappable(SUM, IssueLog(), object_ref="metric:Revenue")


# --------------------------------------------------------------------------
# E8: a pass-through carrying PARTITION BY is wrapped in group_aggregate so the
# partition column reaches GROUP BY even when the user's search omits it.
# --------------------------------------------------------------------------

ROW_NUMBER = Construct(
    "ROW_NUMBER() OVER (...)", Classification.PASSTHROUGH,
    template="ROW_NUMBER() OVER (PARTITION BY {0} ORDER BY {1})",
    variant=Variant.INT_AGGREGATE,
)


def test_emit_passthrough_wraps_a_partitioned_call_in_group_aggregate():
    log = IssueLog()
    out = emit_passthrough(
        ROW_NUMBER, ["[T::Region]", "[T::OrderDate]"], log,
        object_ref="metric:RowNum", partition_column="[T::Region]",
    )
    assert out == (
        'group_aggregate ( sql_int_aggregate_op ( '
        '"ROW_NUMBER() OVER (PARTITION BY {0} ORDER BY {1})" , '
        '[T::Region] , [T::OrderDate] ) , '
        'query_groups ( ) + { [T::Region] } , query_filters ( ) )'
    )


def test_emit_passthrough_without_a_partition_column_is_unwrapped():
    log = IssueLog()
    out = emit_passthrough(STDDEV_POP, ["[x]"], log, object_ref="metric:Revenue")
    assert not out.startswith("group_aggregate")


def test_emit_passthrough_requires_partition_column_when_template_carries_partition_by():
    # E8, enforced rather than left to convention: ROW_NUMBER's template carries a
    # literal PARTITION BY, so omitting partition_column must fail loudly rather
    # than silently emit an unwrapped, only-sometimes-correct pass-through.
    log = IssueLog()
    with pytest.raises(ValueError, match="PARTITION BY"):
        emit_passthrough(
            ROW_NUMBER, ["[T::Region]", "[T::OrderDate]"], log,
            object_ref="metric:RowNum",
        )


def test_emit_passthrough_refuses_a_partition_column_for_a_template_with_no_partition_by():
    # Symmetric check: STDDEV_POP's template has no PARTITION BY, so supplying
    # partition_column anyway is equally a mistake (a mis-transcribed catalog row)
    # and must also fail loudly.
    log = IssueLog()
    with pytest.raises(ValueError, match="PARTITION BY"):
        emit_passthrough(
            STDDEV_POP, ["[x]"], log,
            object_ref="metric:Revenue", partition_column="[T::Region]",
        )


def test_emit_passthrough_detects_partition_by_with_irregular_whitespace():
    # A plain substring match on "partition by" misses "PARTITION  BY" (two
    # spaces) or a newline between the words, which would silently leave the
    # E8 guard defeated in both directions. Regex with \s+ must still catch it.
    irregular = Construct(
        "IRREGULAR_WHITESPACE(expr)", Classification.PASSTHROUGH,
        template="SOME_FUNC({0}) OVER (PARTITION  BY {0} ORDER BY {1})",
        variant=Variant.NUMBER_AGGREGATE,
    )
    log = IssueLog()
    with pytest.raises(ValueError, match="PARTITION BY"):
        emit_passthrough(irregular, ["[dim]", "[ord]"], log, object_ref="metric:X")


# --------------------------------------------------------------------------
# Catalog-wide sweep: every DIRECT row must actually render, not merely read
# correctly. This is the check that caught the IN/NOT IN brace-escaping bug:
# both templates embedded ThoughtSpot's literal `{ ... }` set syntax unescaped
# in a Python format string, so the call with the CORRECT, natural-arity
# argument count (the one a real caller makes) crashed with `ValueError:
# unexpected '{' in field name` — a non-obvious failure, not a clean domain
# error, and invisible to any test that only inspects `construct.template` as
# a string (e.g. `"{" in row.template`) rather than executing it. A catalog
# author can transcribe a document cell containing a literal brace,
# parenthesis, or any other str.format metacharacter for any future family
# and reintroduce exactly this shape of bug; this sweep is general over every
# DIRECT row in CATALOG, not scoped to the rows that caught it originally,
# precisely so that it does.
# --------------------------------------------------------------------------

def test_every_direct_catalog_row_renders_with_its_own_natural_arity():
    failures = []
    for name, construct in CATALOG.items():
        if construct.classification is not Classification.DIRECT:
            continue
        arity = _placeholder_count(construct.template)
        args = [f"arg{i}" for i in range(arity)]
        try:
            emit_direct(construct, args)
        except Exception as exc:  # noqa: BLE001 - want to report every failure, not stop at the first
            failures.append(f"{name!r} ({arity} args): {exc!r}")
    assert not failures, "DIRECT rows that fail to render with their own natural arity:\n" + "\n".join(
        failures
    )


# --------------------------------------------------------------------------
# The passthrough counterpart to the sweep above: every PASSTHROUGH row must
# actually render with its own natural arity, not merely read correctly. A
# catalog edit that desyncs a template's `{n}` placeholders from its intended
# arity — on any passthrough row, not just the one pinned regression case
# above — would otherwise go uncaught until something later tried to emit
# that specific row. Whether a row needs `partition_column` is derived from
# its own template (the same `PARTITION BY` check emit_passthrough itself
# makes, E8), not hardcoded, so a row that gains or loses a PARTITION BY
# stays in sync with this sweep automatically.
# --------------------------------------------------------------------------

def test_every_passthrough_catalog_row_renders_with_its_own_natural_arity():
    failures = []
    for name, construct in CATALOG.items():
        if construct.classification is not Classification.PASSTHROUGH:
            continue
        arity = _placeholder_count(construct.template)
        args = [f"arg{i}" for i in range(arity)]
        carries_partition_by = bool(re.search(r"partition\s+by", construct.template, re.IGNORECASE))
        partition_column = "[partition_col]" if carries_partition_by else None
        log = IssueLog()
        try:
            emit_passthrough(
                construct, args, log, object_ref="metric:sweep", partition_column=partition_column
            )
        except Exception as exc:  # noqa: BLE001 - want to report every failure, not stop at the first
            failures.append(f"{name!r} ({arity} args): {exc!r}")
    assert not failures, "PASSTHROUGH rows that fail to render with their own natural arity:\n" + "\n".join(
        failures
    )
