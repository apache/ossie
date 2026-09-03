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

import pytest
from ossie_thoughtspot import stash
from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.tml_to_ossie import convert_metric


def _resolve(table, column):
    """Every reference lands in the dataset named after its table, lower-cased,
    unless the table is named "MISSING" — then it resolves to nothing at all."""
    return None if table == "MISSING" else f"{table.lower()}.{column.lower()}"


class TestConvertMetric:
    def _table(self, name):
        return {"ORDERS": {"name": "ORDERS", "columns": [
            {"name": "AMOUNT", "db_column_name": "AMOUNT",
             "db_column_properties": {"data_type": "DOUBLE"}},
        ]}}.get(name)

    # -- Row 1 of the truth table: column_id + aggregation. --
    def test_physical_column_with_aggregation_becomes_an_aggregate_metric(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Total Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            {}, self._table, _resolve, log,
        )
        assert metric is not None
        dialects = metric["expression"]["dialects"]
        assert {"dialect": "THOUGHTSPOT", "expression": "sum ( [ORDERS::AMOUNT] )"} in dialects
        assert {"dialect": "ANSI_SQL", "expression": "SUM(orders.amount)"} in dialects
        assert log.as_dicts() == []

    # -- Row 2: formula_id -> a *scalar* expr + aggregation. The two compose. --
    def test_scalar_formula_composes_with_the_column_aggregation(self):
        log = IssueLog()
        formulas = {"formula_Net": {"id": "formula_Net", "expr": "[A::x] - [A::y]"}}
        metric = convert_metric(
            {"name": "Average Net", "formula_id": "formula_Net",
             "properties": {"column_type": "MEASURE", "aggregation": "AVERAGE"}},
            formulas, self._table, _resolve, log,
        )
        assert metric is not None
        dialects = metric["expression"]["dialects"]
        assert {"dialect": "THOUGHTSPOT", "expression": "average ( [A::x] - [A::y] )"} in dialects
        # [A::x] - [A::y] is compound, not a bare reference, so it is not portable
        # on its own — no ANSI_SQL sibling can be composed around it either, and
        # an issue records why (from expression_entries's own non-portability check).
        assert "ANSI_SQL" not in [d["dialect"] for d in dialects]
        issues = log.as_dicts()
        assert len(issues) == 1

    def test_scalar_formula_composes_and_the_ansi_sql_sibling_appears_when_portable(self):
        # The other half of the same rule: when the scalar formula IS a bare,
        # resolvable reference, composing produces a real ANSI_SQL sibling too, not
        # just a THOUGHTSPOT-only rendering.
        log = IssueLog()
        formulas = {"formula_Bare": {"id": "formula_Bare", "expr": "[ORDERS::AMOUNT]"}}
        metric = convert_metric(
            {"name": "Average Amount", "formula_id": "formula_Bare",
             "properties": {"column_type": "MEASURE", "aggregation": "AVERAGE"}},
            formulas, self._table, _resolve, log,
        )
        dialects = metric["expression"]["dialects"]
        assert {"dialect": "THOUGHTSPOT", "expression": "average ( [ORDERS::AMOUNT] )"} in dialects
        assert {"dialect": "ANSI_SQL", "expression": "AVG(orders.amount)"} in dialects
        assert log.as_dicts() == []

    # -- Row 3: formula_id -> an *aggregate* expr. The column aggregation is a no-op. --
    def test_aggregate_formula_ignores_the_column_aggregation(self):
        log = IssueLog()
        formulas = {"formula_Sum": {"id": "formula_Sum", "expr": "sum ( [A::x] )"}}
        metric = convert_metric(
            {"name": "Odd Max Of Sum", "formula_id": "formula_Sum",
             "properties": {"column_type": "MEASURE", "aggregation": "MAX"}},
            formulas, self._table, _resolve, log,
        )
        dialects = metric["expression"]["dialects"]
        assert {"dialect": "THOUGHTSPOT", "expression": "sum ( [A::x] )"} in dialects
        assert not any("MAX" in d["expression"] for d in dialects)

    def test_count_distinct_maps_to_count_distinct(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Unique Customers", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "COUNT_DISTINCT"}},
            {}, self._table, _resolve, log,
        )
        dialects = metric["expression"]["dialects"]
        assert {"dialect": "THOUGHTSPOT", "expression": "unique count ( [ORDERS::AMOUNT] )"} \
            in dialects
        assert {"dialect": "ANSI_SQL", "expression": "COUNT(DISTINCT orders.amount)"} in dialects

    def test_none_aggregation_means_no_aggregate(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Raw Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "NONE"}},
            {}, self._table, _resolve, log,
        )
        assert metric["expression"]["dialects"] == [
            {"dialect": "THOUGHTSPOT", "expression": "[ORDERS::AMOUNT]"},
            {"dialect": "ANSI_SQL", "expression": "orders.amount"},
        ]
        assert log.as_dicts() == []

    def test_std_deviation_and_variance_map(self):
        log = IssueLog()
        stddev_metric = convert_metric(
            {"name": "Amount Stddev", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "STD_DEVIATION"}},
            {}, self._table, _resolve, log,
        )
        variance_metric = convert_metric(
            {"name": "Amount Variance", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "VARIANCE"}},
            {}, self._table, _resolve, log,
        )
        assert {"dialect": "THOUGHTSPOT", "expression": "stddev ( [ORDERS::AMOUNT] )"} \
            in stddev_metric["expression"]["dialects"]
        assert {"dialect": "ANSI_SQL", "expression": "STDDEV(orders.amount)"} \
            in stddev_metric["expression"]["dialects"]
        assert {"dialect": "THOUGHTSPOT", "expression": "variance ( [ORDERS::AMOUNT] )"} \
            in variance_metric["expression"]["dialects"]
        assert {"dialect": "ANSI_SQL", "expression": "VARIANCE(orders.amount)"} \
            in variance_metric["expression"]["dialects"]

    @pytest.mark.parametrize("expr", [
        "sum(   [A::x]  )",
        "sum(\n  [A::x]\n)",
        "count ( [A::x] )",
    ])
    def test_the_thoughtspot_entry_is_the_verbatim_formula_expr(self, expr):
        # The no-op (aggregate-formula) shape must carry the exact source text,
        # untouched — never a reconstruction — even under adversarial whitespace,
        # and even though a *different* column-level aggregation is present and
        # must be discarded rather than applied.
        log = IssueLog()
        formulas = {"formula_Weird": {"id": "formula_Weird", "expr": expr}}
        metric = convert_metric(
            {"name": "Weird", "formula_id": "formula_Weird",
             "properties": {"column_type": "MEASURE", "aggregation": "MAX"}},
            formulas, self._table, _resolve, log,
        )
        thoughtspot_entries = [
            d for d in metric["expression"]["dialects"] if d["dialect"] == "THOUGHTSPOT"
        ]
        assert thoughtspot_entries == [{"dialect": "THOUGHTSPOT", "expression": expr}]

    def test_a_metric_name_that_normalises_differently_stashes_the_exact_name(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Gross Margin %!!", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            {}, self._table, _resolve, log,
        )
        assert metric["name"] == "gross_margin"
        assert "label" not in metric  # metrics have no label field
        assert stash.read_stash(metric)["tml_name"] == "Gross Margin %!!"

    def test_a_metric_that_needs_neither_tml_name_nor_shape_stashes_nothing(self):
        # X6: a converted document stays clean where ThoughtSpot added nothing.
        # Both conditions have to hold at once here: the name must normalise to
        # itself, AND the shape must be the "formula" default — the one shape
        # that needs no stash entry, because it is also what a document with no
        # stash defaults to on the way back.
        log = IssueLog()
        formulas = {"formula_Revenue": {"id": "formula_Revenue", "expr": "sum ( [A::x] )"}}
        metric = convert_metric(
            {"name": "revenue", "formula_id": "formula_Revenue",
             "properties": {"column_type": "MEASURE", "aggregation": "NONE"}},
            formulas, self._table, _resolve, log,
        )
        assert metric["name"] == "revenue"
        assert "custom_extensions" not in metric

    def test_shape_is_stashed_even_when_the_name_is_unchanged(self):
        # A column_id metric is shape `column_aggregation`, not the default
        # `formula` — it needs the stash entry regardless of whether the name
        # also needed one, so an unchanged name must not suppress it.
        log = IssueLog()
        metric = convert_metric(
            {"name": "amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            {}, self._table, _resolve, log,
        )
        assert metric["name"] == "amount"
        payload = stash.read_stash(metric)
        assert payload["shape"] == "column_aggregation"
        assert "tml_name" not in payload

    def test_each_shape_is_stashed_with_its_own_enum_value(self):
        # Pins all three enum spellings the stash schema defines, and confirms
        # each survives a read_stash round trip. The "formula" shape is the one
        # value that is never written (see the empty-payload test above), so its
        # absence here is itself the assertion for that row.
        log = IssueLog()
        column_aggregation_metric = convert_metric(
            {"name": "Total Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            {}, self._table, _resolve, log,
        )
        formulas = {"formula_Net": {"id": "formula_Net", "expr": "[A::x] - [A::y]"}}
        scalar_plus_aggregation_metric = convert_metric(
            {"name": "Average Net", "formula_id": "formula_Net",
             "properties": {"column_type": "MEASURE", "aggregation": "AVERAGE"}},
            formulas, self._table, _resolve, log,
        )
        formulas = {"formula_Sum": {"id": "formula_Sum", "expr": "sum ( [A::x] )"}}
        formula_metric = convert_metric(
            {"name": "Odd Max Of Sum", "formula_id": "formula_Sum",
             "properties": {"column_type": "MEASURE", "aggregation": "MAX"}},
            formulas, self._table, _resolve, log,
        )

        assert stash.read_stash(column_aggregation_metric)["shape"] == "column_aggregation"
        assert (
            stash.read_stash(scalar_plus_aggregation_metric)["shape"]
            == "scalar_formula_plus_aggregation"
        )
        assert "shape" not in stash.read_stash(formula_metric)

    def test_datatype_is_emitted_only_for_a_bare_aggregate_over_a_typed_column(self):
        log = IssueLog()

        count_metric = convert_metric(
            {"name": "Order Count", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "COUNT"}},
            {}, self._table, _resolve, log,
        )
        count_distinct_metric = convert_metric(
            {"name": "Distinct Count", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "COUNT_DISTINCT"}},
            {}, self._table, _resolve, log,
        )
        sum_metric = convert_metric(
            {"name": "Total", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            {}, self._table, _resolve, log,
        )
        formulas = {"formula_Sum": {"id": "formula_Sum", "expr": "sum ( [A::x] )"}}
        formula_metric = convert_metric(
            {"name": "Formula Total", "formula_id": "formula_Sum",
             "properties": {"column_type": "MEASURE", "aggregation": "NONE"}},
            formulas, self._table, _resolve, log,
        )

        assert count_metric["datatype"] == "Integer"
        assert count_distinct_metric["datatype"] == "Integer"
        assert sum_metric["datatype"] == "Decimal"  # the physical column's own mapped type
        assert "datatype" not in formula_metric  # a formula has no declared type anywhere

    def test_a_formula_id_with_no_matching_formulas_entry_raises_an_issue(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Orphan", "formula_id": "formula_Nonexistent",
             "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            {}, self._table, _resolve, log,
        )
        assert metric is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "Orphan" in issues[0]["message"]
        assert "formula_Nonexistent" in issues[0]["message"]

    # -- Tests of my own, beyond everything specified above. --
    #
    # 1. An unrecognised `aggregation` value must not raise KeyError. A missing
    #    formula_id is already guarded against a bare KeyError above; the
    #    _AGGREGATION lookup is exactly the same shape of hazard on a different
    #    dict: a malformed or newer-than-this-converter TML value hitting an
    #    unguarded lookup would crash the whole conversion instead of degrading
    #    one metric. Chosen because it is the most direct sibling of a failure
    #    mode already treated as important elsewhere in this suite, just applied
    #    to a different lookup.
    def test_an_unrecognised_aggregation_value_logs_and_falls_back_to_none(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Mystery", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE", "aggregation": "BOGUS"}},
            {}, self._table, _resolve, log,
        )
        assert metric is not None
        assert metric["expression"]["dialects"] == [
            {"dialect": "THOUGHTSPOT", "expression": "[ORDERS::AMOUNT]"},
            {"dialect": "ANSI_SQL", "expression": "orders.amount"},
        ]
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "BOGUS" in issues[0]["message"]

    # 2. An ATTRIBUTE column must not become a metric. Building one for it here
    #    would surface the same TML column as two competing Ossie objects (a field
    #    from convert_field and a metric from here) once a future caller runs both
    #    functions over every columns[] entry, so this boundary needs to be
    #    enforced on this function's own side, not only on convert_field's
    #    ATTRIBUTE-only check.
    def test_an_attribute_column_is_not_a_metric(self):
        log = IssueLog()
        assert convert_metric(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        ) is None
        assert log.as_dicts() == []

    # -- One more, mirroring convert_field's own coverage of the same shape. --
    def test_neither_column_id_nor_formula_id_logs_and_returns_none(self):
        log = IssueLog()
        metric = convert_metric(
            {"name": "Nothing", "properties": {"column_type": "MEASURE"}},
            {}, self._table, _resolve, log,
        )
        assert metric is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "column_id" in issues[0]["message"]
        assert "formula_id" in issues[0]["message"]
