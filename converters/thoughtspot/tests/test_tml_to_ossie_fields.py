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
from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.tml_to_ossie import attribute_dataset, convert_field, expression_entries


def _resolve(table, column):
    """Every reference lands in the dataset named after its table, lower-cased,
    unless the table is named "MISSING" — then it resolves to nothing at all."""
    return None if table == "MISSING" else f"{table.lower()}.{column.lower()}"


class TestExpressionEntries:
    def test_a_bare_reference_gets_both_dialects(self):
        log = IssueLog()
        out = expression_entries("[ORDERS::Amount]", _resolve, log, object_ref="f")
        assert out == [
            {"dialect": "THOUGHTSPOT", "expression": "[ORDERS::Amount]"},
            {"dialect": "ANSI_SQL", "expression": "orders.amount"},
        ]
        assert log.as_dicts() == []

    def test_the_thoughtspot_entry_is_byte_for_byte_the_input(self):
        # A reconstruction from a parsed (name, args) shape would normalise this to
        # `sum ( [ORDERS::Amount] )` and break exact-string equality on the way back.
        log = IssueLog()
        out = expression_entries("sum([ORDERS::Amount])", _resolve, log, object_ref="f")
        assert out[0] == {"dialect": "THOUGHTSPOT", "expression": "sum([ORDERS::Amount])"}
        assert [e["dialect"] for e in out] == ["THOUGHTSPOT"]

    def test_a_computed_expression_gets_a_thoughtspot_entry_and_an_issue(self):
        log = IssueLog()
        out = expression_entries(
            "sum ( [ORDERS::Amount] ) / count ( [ORDERS::Id] )", _resolve, log, object_ref="f"
        )
        assert [e["dialect"] for e in out] == ["THOUGHTSPOT"]
        assert len(log.as_dicts()) == 1

    def test_a_parameter_reference_blocks_the_portable_sibling(self):
        # A runtime parameter has no Ossie equivalent, so the expression is not
        # portable however simple it looks.
        log = IssueLog()
        out = expression_entries("[ORDERS::Amount] * [Growth Rate]", _resolve, log, object_ref="f")
        assert [e["dialect"] for e in out] == ["THOUGHTSPOT"]
        assert any("parameter" in i["message"].lower() for i in log.as_dicts())
        assert not any(i["code"] == "TS-EXPR-FORMULA-REFERENCE" for i in log.as_dicts())

    def test_a_formula_cross_reference_is_not_reported_as_a_parameter(self):
        # `[formula_Margin]` has no `::`, the same bracketed shape a runtime
        # parameter has -- but it is a formula composing another formula, a
        # first-class ThoughtSpot construct, not something with no Ossie
        # equivalent. The old bug: this fired TS-EXPR-PARAM and told a reader
        # to go hunting for a parameter that does not exist.
        log = IssueLog()
        out = expression_entries("sum ( [formula_Margin] )", _resolve, log, object_ref="f")
        assert [e["dialect"] for e in out] == ["THOUGHTSPOT"]
        assert not any(i["code"] == "TS-EXPR-PARAM" for i in log.as_dicts())
        assert not any("parameter" in i["message"].lower() for i in log.as_dicts())

    def test_the_cross_reference_issue_names_the_right_cause(self):
        log = IssueLog()
        expression_entries("sum ( [formula_Margin] )", _resolve, log, object_ref="f")
        [issue] = [i for i in log.as_dicts() if i["code"] == "TS-EXPR-FORMULA-REFERENCE"]
        assert "formula_Margin" in issue["message"]
        assert "inlin" in issue["message"].lower()

    def test_an_expression_with_both_a_cross_reference_and_a_parameter_reports_both(self):
        log = IssueLog()
        out = expression_entries(
            "[formula_Margin] * [Growth Rate]", _resolve, log, object_ref="f"
        )
        assert [e["dialect"] for e in out] == ["THOUGHTSPOT"]
        codes = {i["code"] for i in log.as_dicts()}
        assert codes == {"TS-EXPR-FORMULA-REFERENCE", "TS-EXPR-PARAM"}
        [param_issue] = [i for i in log.as_dicts() if i["code"] == "TS-EXPR-PARAM"]
        assert "Growth Rate" in param_issue["message"]
        assert "formula_Margin" not in param_issue["message"]

    def test_a_parameter_used_twice_is_named_once_not_twice(self):
        # `[Growth Rate]` on both sides of the ratio is one fact worth
        # reporting once -- listing it twice reads as two distinct
        # unresolved parameters, not a single repeated reference.
        log = IssueLog()
        expression_entries(
            "[Growth Rate] / [Growth Rate]", _resolve, log, object_ref="f"
        )
        [issue] = [i for i in log.as_dicts() if i["code"] == "TS-EXPR-PARAM"]
        assert issue["message"].count("Growth Rate") == 1

    def test_a_repeated_cross_reference_is_also_named_once(self):
        log = IssueLog()
        expression_entries(
            "[formula_Margin] + [formula_Margin]", _resolve, log, object_ref="f"
        )
        [issue] = [i for i in log.as_dicts() if i["code"] == "TS-EXPR-FORMULA-REFERENCE"]
        assert issue["message"].count("formula_Margin") == 1

    def test_an_unresolvable_reference_blocks_the_portable_sibling(self):
        log = IssueLog()
        out = expression_entries("[MISSING::Col]", _resolve, log, object_ref="f")
        assert [e["dialect"] for e in out] == ["THOUGHTSPOT"]
        assert log.as_dicts()

    def test_the_thoughtspot_entry_is_always_present(self):
        # The invariant the whole round trip rests on: whatever else happens, the
        # original expression string survives, unmodified, as the first entry.
        log = IssueLog()
        for expr in ["[A::x]", "sum ( [A::x] )", "gibberish ( (", "[Param]"]:
            out = expression_entries(expr, _resolve, log, object_ref="f")
            assert out[0]["dialect"] == "THOUGHTSPOT"
            assert out[0]["expression"] == expr


class TestExpressionEntriesVerbatimUnderAdversarialInput:
    """The exact-string property is the one thing the whole round trip depends on.
    Each case below distorts the input in one way real ThoughtSpot formulas can be
    distorted — irregular internal spacing, edge whitespace, embedded structure, a
    quoting convention — and checks the THOUGHTSPOT entry is untouched regardless."""

    @pytest.mark.parametrize(
        "expr",
        [
            "sum(   [ORDERS::Amount]  ,2   )",  # irregular internal whitespace
            "[ORDERS::Amount] ",  # trailing space
            "[ORDERS::Amount]\t",  # trailing tab
            "sum(\n  [ORDERS::Amount]\n)",  # embedded newline
            "concat ( [ORDERS::Name] , 'it''s a test' )",  # doubled quote
            "\t[ORDERS::Amount]\n",  # leading tab, trailing newline
        ],
    )
    def test_verbatim_property_holds(self, expr):
        log = IssueLog()
        out = expression_entries(expr, _resolve, log, object_ref="f")
        assert out[0] == {"dialect": "THOUGHTSPOT", "expression": expr}
        # A dialect entry is a plain dict of Python strs; nothing en route (e.g. an
        # implicit int/float coercion or a str subclass with different __eq__) could
        # make the equality above pass while the object stored is not the original.
        assert out[0]["expression"] is expr or out[0]["expression"] == expr
        assert isinstance(out[0]["expression"], str)


class TestPortableSiblingTruthTable:
    """When exactly does a portable ANSI_SQL sibling appear? One row per input shape,
    plus a sweep confirming none of the non-portable shapes ever produces one anyway
    (a wrong portable expression is worse than none)."""

    CASES = {
        "bare reference": ("[ORDERS::Amount]", True),
        "bare reference with surrounding whitespace": ("  [ORDERS::Amount]  ", True),
        "reference the resolver cannot resolve": ("[MISSING::Col]", False),
        "expression with a runtime parameter": ("[ORDERS::Amount] * [Growth Rate]", False),
        "expression with a formula cross-reference": ("sum ( [formula_Margin] )", False),
        "single function call": ("sum([ORDERS::Amount])", False),
        "compound expression": ("[ORDERS::Amount] + [ORDERS::Tax]", False),
    }

    @pytest.mark.parametrize("expr,expect_portable", CASES.values(), ids=CASES.keys())
    def test_portability_per_case(self, expr, expect_portable):
        log = IssueLog()
        out = expression_entries(expr, _resolve, log, object_ref="f")
        dialects = [e["dialect"] for e in out]
        if expect_portable:
            assert dialects == ["THOUGHTSPOT", "ANSI_SQL"]
            assert log.as_dicts() == []
        else:
            assert dialects == ["THOUGHTSPOT"]
            assert log.as_dicts()

    def test_no_non_portable_case_produces_a_portable_sibling(self):
        # The inverse check: sweep every case this table declares non-portable and
        # confirm none of them slipped an ANSI_SQL entry in anyway.
        for expr, expect_portable in self.CASES.values():
            if expect_portable:
                continue
            log = IssueLog()
            out = expression_entries(expr, _resolve, log, object_ref="f")
            assert "ANSI_SQL" not in [e["dialect"] for e in out], expr


class TestAttributeDataset:
    """Attacking the attribution rule: a computed field spanning two datasets must
    not be attributed, and neither must the other shapes that offer no single,
    confident answer."""

    def test_references_spanning_two_datasets_are_not_attributed(self):
        log = IssueLog()

        def resolve(table, column):
            return f"other.{column.lower()}" if table == "OTHER" else f"orders.{column.lower()}"

        result = attribute_dataset(
            "[ORDERS::Amount] + [OTHER::Fee]", resolve, log, object_ref="f"
        )
        assert result is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "different datasets" in issues[0]["message"]

    def test_no_references_at_all_is_not_attributed(self):
        log = IssueLog()
        result = attribute_dataset("1 + 1", _resolve, log, object_ref="f")
        assert result is None
        assert log.as_dicts()
        assert "no column references" in log.as_dicts()[0]["message"]

    def test_references_to_the_same_dataset_via_different_tables_are_attributed(self):
        # Two different ThoughtSpot tables can legitimately resolve into the *same*
        # Ossie dataset (an alias, or two tables mapped onto one source) — that is
        # real agreement, not a coincidence to be suspicious of.
        log = IssueLog()

        def resolve(table, column):
            return f"orders.{column.lower()}"  # ORDERS and ORDERS_ALIAS both land here

        result = attribute_dataset(
            "[ORDERS::Amount] + [ORDERS_ALIAS::Tax]", resolve, log, object_ref="f"
        )
        assert result == "orders"
        assert log.as_dicts() == []

    def test_an_unresolvable_reference_is_not_attributed(self):
        log = IssueLog()
        result = attribute_dataset(
            "[ORDERS::Amount] + [MISSING::Fee]", _resolve, log, object_ref="f"
        )
        assert result is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "[MISSING::Fee]" in issues[0]["message"]

    def test_a_single_resolvable_reference_is_attributed(self):
        log = IssueLog()
        result = attribute_dataset("[ORDERS::Amount]", _resolve, log, object_ref="f")
        assert result == "orders"
        assert log.as_dicts() == []

    def test_parameters_take_no_part_in_attribution(self):
        # A parameter reference carries no dataset. Attribution should succeed from
        # the column references alone; portability is a separate question that
        # expression_entries answers, not this function.
        log = IssueLog()
        result = attribute_dataset(
            "[ORDERS::Amount] * [Growth Rate]", _resolve, log, object_ref="f"
        )
        assert result == "orders"
        assert log.as_dicts() == []


class TestConvertField:
    def _table(self, name):
        return {"ORDERS": {"name": "ORDERS", "columns": [
            {"name": "AMOUNT", "db_column_name": "AMOUNT",
             "db_column_properties": {"data_type": "DOUBLE"}},
        ]}}.get(name)

    def test_a_physical_column_becomes_a_field(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Order Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert field["name"] == "order_amount"      # the normalised identifier
        assert field["label"] == "Order Amount"      # the exact display name
        assert field["datatype"] == "Decimal"        # DOUBLE -> Decimal

    def test_description_round_trips_without_a_stash(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT", "description": "How much",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert field["description"] == "How much"

    def test_an_absent_description_is_omitted_not_blank(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert "description" not in field

    def test_synonyms_become_ai_context(self):
        # The full shape, not just presence: a synonyms-only ai_context must be the
        # bare {"synonyms": [...]} object, with no "instructions" key sitting empty
        # beside it.
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE", "synonyms": ["total", "value"],
                            "synonym_type": "USER_DEFINED"}},
            {}, self._table, _resolve, log,
        )
        assert field["ai_context"] == {"synonyms": ["total", "value"]}

    def test_an_empty_synonyms_list_produces_no_ai_context(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE", "synonyms": []}},
            {}, self._table, _resolve, log,
        )
        assert "ai_context" not in field

    def test_free_text_ai_context_alone_stays_a_bare_string(self):
        # The other shape Ossie's ai_context oneOf accepts: free-text instructions
        # with no synonyms must come through as the plain string itself, not
        # wrapped in an object — a regression that wrapped it would still pass a
        # presence-only check.
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE",
                            "ai_context": "Prefer this over the raw column."}},
            {}, self._table, _resolve, log,
        )
        assert field["ai_context"] == "Prefer this over the raw column."
        assert isinstance(field["ai_context"], str)

    def test_synonyms_and_free_text_together_combine_into_one_object(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE", "synonyms": ["total"],
                            "ai_context": "Prefer this over the raw column."}},
            {}, self._table, _resolve, log,
        )
        assert field["ai_context"] == {
            "synonyms": ["total"],
            "instructions": "Prefer this over the raw column.",
        }

    def test_neither_synonyms_nor_free_text_omits_ai_context_entirely(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert "ai_context" not in field

    def test_a_measure_column_is_not_a_field(self):
        # MEASURE columns become metrics, handled elsewhere.
        log = IssueLog()
        assert convert_field(
            {"name": "Amount", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "MEASURE"}},
            {}, self._table, _resolve, log,
        ) is None

    def test_is_time_is_omitted_when_the_type_already_implies_it(self):
        # ThoughtSpot has no temporal-role flag at all in this direction, so is_time
        # is never written — writing it for a Date column would just be noise on
        # top of the type-derived default.
        log = IssueLog()
        table = lambda n: {"name": "ORDERS", "columns": [
            {"name": "DT", "db_column_name": "DT",
             "db_column_properties": {"data_type": "DATE"}}]}
        field = convert_field(
            {"name": "Order Date", "column_id": "ORDERS::DT",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, table, _resolve, log,
        )
        assert "dimension" not in field or "is_time" not in field.get("dimension", {})

    def test_a_missing_physical_column_raises_an_issue_and_omits_the_datatype(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Ghost", "column_id": "ORDERS::NOPE",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert "datatype" not in field
        assert log.as_dicts()

    def test_a_missing_table_also_omits_the_datatype_and_raises_an_issue(self):
        # Distinct from the case above: here the whole table is absent, not just one
        # column inside a table that was found.
        log = IssueLog()
        field = convert_field(
            {"name": "Ghost", "column_id": "MISSING::Col",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert field is not None
        assert "datatype" not in field
        assert log.as_dicts()

    def test_the_identifier_and_the_label_are_never_swapped(self):
        # A stronger anti-regression case than the basic one above: punctuation in
        # the display name makes name/label divergence unmistakable if the two were
        # ever accidentally swapped.
        log = IssueLog()
        field = convert_field(
            {"name": "Gross Margin %!!", "column_id": "ORDERS::AMOUNT",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert field["label"] == "Gross Margin %!!"
        assert field["name"] != field["label"]
        assert field["name"] == "gross_margin"

    def test_a_computed_field_spanning_two_datasets_is_not_built(self):
        log = IssueLog()

        def resolve(table, column):
            return f"other.{column.lower()}" if table == "OTHER" else f"orders.{column.lower()}"

        formulas = {"formula_Combined": {"id": "formula_Combined",
                                          "expr": "[ORDERS::Amount] + [OTHER::Fee]"}}
        field = convert_field(
            {"name": "Combined", "formula_id": "formula_Combined",
             "properties": {"column_type": "ATTRIBUTE"}},
            formulas, self._table, resolve, log,
        )
        assert field is None
        assert log.as_dicts()

    # -- Tests of my own, beyond everything specified above. --
    #
    # 1. convert_field's handling of a *computed* (formula-backed) ATTRIBUTE column —
    #    attribution plus field construction end to end — has no coverage at all in
    #    the cases above; every one of them is a physical, column_id-backed field.
    #    That whole code path is new and untested, and is exactly the kind of thing
    #    that "reasoning about behaviour instead of running it" would get wrong.
    def test_a_computed_field_with_references_in_one_dataset_is_built(self):
        log = IssueLog()
        formulas = {"formula_Net_Amount": {"id": "formula_Net_Amount",
                                            "expr": "[ORDERS::Amount] - [ORDERS::Discount]"}}
        field = convert_field(
            {"name": "Net Amount", "formula_id": "formula_Net_Amount",
             "properties": {"column_type": "ATTRIBUTE"}},
            formulas, self._table, _resolve, log,
        )
        assert field is not None
        assert field["name"] == "net_amount"
        assert field["label"] == "Net Amount"
        assert field["expression"]["dialects"] == [
            {"dialect": "THOUGHTSPOT", "expression": "[ORDERS::Amount] - [ORDERS::Discount]"},
        ]
        # Not portable (a compound expression), but still attributed and built —
        # attribution and portability are independent questions.
        assert "datatype" not in field
        assert log.as_dicts()  # the non-portability issue from expression_entries

    # 2. A computed field that mixes an attributable column reference with a runtime
    #    parameter is the sharpest test of whether attribution and portability were
    #    kept genuinely independent, rather than one implementation accidentally
    #    leaning on the other (e.g. attribution silently failing because of the
    #    parameter, or the parameter warning silently being swallowed because
    #    attribution succeeded).
    def test_a_computed_field_with_a_parameter_is_attributed_but_not_portable(self):
        log = IssueLog()
        formulas = {"formula_Grown_Amount": {"id": "formula_Grown_Amount",
                                              "expr": "[ORDERS::Amount] * [Growth Rate]"}}
        field = convert_field(
            {"name": "Grown Amount", "formula_id": "formula_Grown_Amount",
             "properties": {"column_type": "ATTRIBUTE"}},
            formulas, self._table, _resolve, log,
        )
        assert field is not None  # attribution succeeded from the one column reference
        dialects = [e["dialect"] for e in field["expression"]["dialects"]]
        assert dialects == ["THOUGHTSPOT"]  # but it is not portable
        assert any("parameter" in i["message"].lower() for i in log.as_dicts())

    # 3. The `formulas` map lookup itself, per the task's three required cases.
    #
    # 3a. formula_id present in the map: the expr must survive verbatim, byte for
    #     byte, into the THOUGHTSPOT dialect entry — re-run through the new
    #     lookup-based path rather than assumed to still hold from the expr-stash
    #     tests above.
    def test_a_formula_id_present_in_the_map_converts_with_the_verbatim_expr(self):
        log = IssueLog()
        weird_expr = "concat(  [ORDERS::Amount] , 'it''s a test'\t)\n"
        formulas = {"formula_Weird": {"id": "formula_Weird", "expr": weird_expr}}
        field = convert_field(
            {"name": "Weird", "formula_id": "formula_Weird",
             "properties": {"column_type": "ATTRIBUTE"}},
            formulas, self._table, _resolve, log,
        )
        assert field is not None
        thoughtspot_entries = [
            e for e in field["expression"]["dialects"] if e["dialect"] == "THOUGHTSPOT"
        ]
        assert thoughtspot_entries == [{"dialect": "THOUGHTSPOT", "expression": weird_expr}]

    # 3b. formula_id with no matching entry in the map: must not raise (no
    #     KeyError), must log an issue naming the column and the missing id, and
    #     must return None rather than a field silently missing its expression.
    def test_a_formula_id_missing_from_the_map_logs_and_returns_none(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Orphan", "formula_id": "formula_Nonexistent",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert field is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "Orphan" in issues[0]["message"]
        assert "formula_Nonexistent" in issues[0]["message"]

    # 3c. Neither column_id nor formula_id: decided to treat this the same as the
    #     pre-existing "no source" contract (a column with neither key was already
    #     handled before formula_id existed) — log an issue and return None, rather
    #     than inventing a new, silent no-op path for what is really the same
    #     "nothing to build this field from" situation.
    def test_neither_column_id_nor_formula_id_logs_and_returns_none(self):
        log = IssueLog()
        field = convert_field(
            {"name": "Nothing", "properties": {"column_type": "ATTRIBUTE"}},
            {}, self._table, _resolve, log,
        )
        assert field is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "column_id" in issues[0]["message"]
        assert "formula_id" in issues[0]["message"]

    # 4. A formulas[] entry can be present under the id but still be malformed —
    #    missing its own `expr` key. Same class of problem as an absent id (there
    #    is still no expression text to read), so it gets the same treatment: an
    #    issue naming the column and the formula id, and None — not a KeyError.
    def test_a_formula_entry_present_but_missing_expr_logs_and_returns_none(self):
        log = IssueLog()
        formulas = {"formula_Bad": {"id": "formula_Bad"}}  # no "expr" key
        field = convert_field(
            {"name": "Malformed", "formula_id": "formula_Bad",
             "properties": {"column_type": "ATTRIBUTE"}},
            formulas, self._table, _resolve, log,
        )
        assert field is None
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "Malformed" in issues[0]["message"]
        assert "formula_Bad" in issues[0]["message"]


class TestPhysicalDatatypeLoss:
    """`_physical_datatype`'s two `None` outcomes are not the same kind of
    outcome, and only one of them is a loss worth logging — exercised through
    `convert_field`'s column_id path, the only way this private helper runs."""

    def test_an_absent_data_type_produces_no_issue(self):
        # Nothing was ever declared, so there is nothing being dropped. datatype
        # is optional in Ossie; silence here is the correct, unremarkable answer.
        log = IssueLog()
        table = lambda n: {"name": "ORDERS", "columns": [
            {"name": "NOTE", "db_column_name": "NOTE"}]}  # no db_column_properties
        field = convert_field(
            {"name": "Note", "column_id": "ORDERS::NOTE",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, table, _resolve, log,
        )
        assert field is not None
        assert "datatype" not in field
        assert log.as_dicts() == []

    def test_a_present_but_unmapped_data_type_logs_exactly_one_issue_naming_it(self):
        # The warehouse told us the type (GEOGRAPHY, outside the Ossie enum) and
        # to_ossie has no mapping for it — that is a genuine silent loss unless
        # this logs it.
        log = IssueLog()
        table = lambda n: {"name": "ORDERS", "columns": [
            {"name": "LOC", "db_column_name": "LOC",
             "db_column_properties": {"data_type": "GEOGRAPHY"}}]}
        field = convert_field(
            {"name": "Location", "column_id": "ORDERS::LOC",
             "properties": {"column_type": "ATTRIBUTE"}},
            {}, table, _resolve, log,
        )
        assert field is not None
        assert "datatype" not in field
        issues = log.as_dicts()
        assert len(issues) == 1
        assert "GEOGRAPHY" in issues[0]["message"]
