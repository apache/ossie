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

"""Tests for `build_table`: one Ossie dataset -> one Table or SQL View document.

Fixtures build raw Ossie dataset/field dicts directly, the same way
test_tml_to_ossie_fields.py builds raw TML column dicts -- `build_table`'s
input contract is the dataset dict, not any particular document it came from.
"""
import json

import pytest

from ossie_thoughtspot.constants import (
    DATASET_STASH_CONNECTION_NAME,
    DATASET_STASH_SOURCE_PARTS,
    DATASET_STASH_SOURCE_PARTS_DB,
    DATASET_STASH_SOURCE_PARTS_DB_TABLE,
    DATASET_STASH_SOURCE_PARTS_SCHEMA,
    DATASET_STASH_SQL_OUTPUT_COLUMNS,
    DATASET_STASH_TML_OBJECT,
    DATASET_STASH_TML_OBJECT_WITNESS,
    DATASET_STASH_UNSURFACED_COLUMNS,
    FIELD_STASH_DATA_TYPE,
    FIELD_STASH_DATA_TYPE_WITNESS,
    FIELD_STASH_DB_COLUMN_NAME,
    FIELD_STASH_DB_COLUMN_NAME_WITNESS,
)
from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.ossie_to_thoughtspot import build_table
from ossie_thoughtspot.tml import DocumentSet, TmlDocument, dump_document, load_document
from ossie_thoughtspot.tml_to_ossie import convert as tml_to_ossie_convert


def _dump(document):
    return dump_document(document)


def _stash(**payload):
    return [{"vendor_name": "THOUGHTSPOT", "data": json.dumps({"_v": 1, **payload})}]


def _field(name, expression, *, label=None, datatype=None, description=None, field_stash=None):
    field: dict = {"name": name}
    if label is not None:
        field["label"] = label
    field["expression"] = {"dialects": expression}
    if datatype is not None:
        field["datatype"] = datatype
    if description is not None:
        field["description"] = description
    if field_stash is not None:
        field["custom_extensions"] = _stash(**field_stash)
    return field


def _physical(name, identifier=None, **kwargs):
    """A field whose expression is a single bare SQL identifier -- the
    hand-authored shape of a physical column."""
    return _field(name, [{"dialect": "ANSI_SQL", "expression": identifier or name}], **kwargs)


def _round_tripped_physical(name, table, column, **kwargs):
    """A field whose expression is the THOUGHTSPOT-dialect verbatim bracket
    reference a prior TML -> Ossie trip would have produced."""
    return _field(name, [{"dialect": "THOUGHTSPOT", "expression": f"[{table}::{column}]"}], **kwargs)


def _computed(name, expr, **kwargs):
    return _field(name, [{"dialect": "THOUGHTSPOT", "expression": expr}], **kwargs)


def _dataset(name, source, fields=None, *, description=None, dataset_stash=None):
    dataset: dict = {"name": name, "source": source}
    if fields is not None:
        dataset["fields"] = fields
    if description is not None:
        dataset["description"] = description
    if dataset_stash is not None:
        dataset["custom_extensions"] = _stash(**dataset_stash)
    return dataset


class TestDbColumnName:
    def test_every_column_carries_db_column_name(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS",
            fields=[_physical("order_date"), _physical("amount", "o_amount")],
            dataset_stash={DATASET_STASH_CONNECTION_NAME: "My Snowflake"},
        )
        table = build_table(dataset, IssueLog())
        assert table.kind == "table"
        columns = table.body["columns"]
        assert len(columns) == 2
        for column in columns:
            assert "db_column_name" in column
        assert columns[0] == {
            "name": "order_date",
            "db_column_name": "order_date",
            "db_column_properties": {"data_type": "INT64"},
        }
        assert columns[1]["db_column_name"] == "o_amount"

    def test_db_column_name_is_present_even_when_equal_to_name(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[_physical("status")])
        table = build_table(dataset, IssueLog())
        column = table.body["columns"][0]
        assert column["name"] == column["db_column_name"] == "status"

    def test_a_round_tripped_bracket_reference_supplies_db_column_name(self):
        # A prior TML -> Ossie trip leaves the table's own physical column
        # *display* name inside the verbatim THOUGHTSPOT bracket, not in
        # label/name -- label/name are the Model's own display name, which
        # a computed field's surfacing column can set independently.
        field = _round_tripped_physical("order_date", "ORDERS", "O_ORDERDATE", label="Order Date")
        dataset = _dataset("ORDERS", "SALES.PUBLIC.ORDERS", fields=[field])
        log = IssueLog()
        table = build_table(dataset, log)
        column = table.body["columns"][0]
        assert column["name"] == "O_ORDERDATE"
        assert column["db_column_name"] == "O_ORDERDATE"
        # The default is reported, not silent -- see
        # TestRoundTripAgainstTheForwardDirection for the case where it is
        # wrong (the display name and the true db_column_name differed).
        assert any(i["code"] == "TS-FIELD-DB-COLUMN-NAME-ASSUMED" for i in log.as_dicts())

    def test_a_stashed_db_column_name_is_preferred_over_the_bracket_display_name(self):
        # The bracket names the table's display name ("Order Date"); the
        # field's own stash carries the true warehouse name separately when
        # the forward direction saw the two differ, and that value wins --
        # no assumption, no issue -- as long as its witness (the display
        # name it was recorded for) still matches.
        field = _round_tripped_physical(
            "order_date", "ORDERS", "Order Date",
            field_stash={
                FIELD_STASH_DB_COLUMN_NAME: "O_ORDERDATE",
                FIELD_STASH_DB_COLUMN_NAME_WITNESS: "Order Date",
            },
        )
        dataset = _dataset("ORDERS", "SALES.PUBLIC.ORDERS", fields=[field])
        log = IssueLog()
        table = build_table(dataset, log)
        column = table.body["columns"][0]
        assert column["name"] == "Order Date"
        assert column["db_column_name"] == "O_ORDERDATE"
        assert not [i for i in log.as_dicts() if i["code"] == "TS-FIELD-DB-COLUMN-NAME-ASSUMED"]
        assert not [i for i in log.as_dicts() if i["code"] == "TS-FIELD-DB-COLUMN-NAME-STALE"]

    def test_a_stashed_db_column_name_whose_witness_no_longer_matches_is_dropped(self):
        # The field was retargeted to a different physical column since the
        # stash was written (Amount -> Total Amount, the exact scenario a
        # retargeted reference produces) -- the stashed warehouse name
        # describes the OLD column and must not be applied to the new one.
        field = _round_tripped_physical(
            "amount", "ORDERS", "Total Amount",
            field_stash={
                FIELD_STASH_DB_COLUMN_NAME: "O_AMOUNT",
                FIELD_STASH_DB_COLUMN_NAME_WITNESS: "Amount",
            },
        )
        dataset = _dataset("ORDERS", "SALES.PUBLIC.ORDERS", fields=[field])
        log = IssueLog()
        table = build_table(dataset, log)
        column = table.body["columns"][0]
        assert column["name"] == "Total Amount"
        assert column["db_column_name"] == "Total Amount"
        assert any(i["code"] == "TS-FIELD-DB-COLUMN-NAME-STALE" for i in log.as_dicts())


class TestDataTypeCompulsory:
    def test_a_datatype_less_field_still_gets_a_data_type(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[_physical("note")])
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.body["columns"][0]["db_column_properties"] == {"data_type": "INT64"}

    def test_a_declared_datatype_maps_through(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS", fields=[_physical("amount", datatype="Integer")]
        )
        table = build_table(dataset, IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "INT64"


class TestDeclaredLoss:
    @pytest.mark.parametrize("datatype", ["Float", "Time", "DateTimeTz", "Opaque"])
    def test_each_declared_loss_datatype_raises_an_issue_naming_the_loss(self, datatype):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS", fields=[_physical("field_a", datatype=datatype)]
        )
        log = IssueLog()
        build_table(dataset, log)
        issues = [i for i in log.as_dicts() if i["code"] == "TS-FIELD-DATATYPE-DECLARED-LOSS"]
        assert len(issues) == 1
        assert datatype in issues[0]["message"]

    def test_a_lossless_datatype_raises_no_declared_loss_issue(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS", fields=[_physical("amount", datatype="Integer")]
        )
        log = IssueLog()
        build_table(dataset, log)
        assert not [i for i in log.as_dicts() if i["code"] == "TS-FIELD-DATATYPE-DECLARED-LOSS"]


class TestSourceSplitting:
    def test_a_three_part_source_splits_correctly(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.kind == "table"
        assert table.body["db"] == "SALES"
        assert table.body["schema"] == "PUBLIC"
        assert table.body["db_table"] == "ORDERS"
        assert not [i for i in log.as_dicts() if "SOURCE" in i["code"]]

    @pytest.mark.parametrize("source", ["SALES.ORDERS", "ORDERS"])
    def test_a_two_or_one_part_source_raises_an_issue_rather_than_a_malformed_table(self, source):
        dataset = _dataset("orders", source)
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.kind == "table"
        assert any(i["code"] == "TS-DATASET-SOURCE-MALFORMED" for i in log.as_dicts())

    def test_a_query_source_produces_a_sql_view_document(self):
        dataset = _dataset("recent_orders", "SELECT * FROM orders WHERE recent = true")
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.kind == "sql_view"
        assert table.body["sql_query"] == "SELECT * FROM orders WHERE recent = true"
        assert not [i for i in log.as_dicts() if "SOURCE" in i["code"]]

    def test_a_stashed_tml_object_overrides_a_looks_like_a_query_source(self):
        # A query that happens to be stored under a stashed sql_view kind
        # must not be re-classified by the whitespace heuristic. The witness
        # (the source it was stashed against) still matches, so the stash
        # wins even though this particular source's derived kind agrees
        # anyway -- see the next two tests for cases where it does not.
        source = "SELECT * FROM orders"
        dataset = _dataset(
            "recent_orders", source,
            dataset_stash={
                DATASET_STASH_TML_OBJECT: "sql_view",
                DATASET_STASH_TML_OBJECT_WITNESS: source,
            },
        )
        table = build_table(dataset, IssueLog())
        assert table.kind == "sql_view"

    def test_a_matching_witness_prefers_the_stash_over_a_disagreeing_derivation(self):
        # The source LOOKS like a plain table reference (_derive_kind would
        # call it "table"), but the stash says this dataset came from a
        # sql_view -- and its witness still matches the live source, so the
        # stash wins despite disagreeing with the heuristic.
        source = "SALES.PUBLIC.ORDERS"
        dataset = _dataset(
            "orders", source,
            dataset_stash={
                DATASET_STASH_TML_OBJECT: "sql_view",
                DATASET_STASH_TML_OBJECT_WITNESS: source,
            },
        )
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.kind == "sql_view"
        assert not any(i["code"] == "TS-DATASET-TML-OBJECT-STALE" for i in log.as_dicts())

    def test_a_stale_tml_object_witness_is_dropped_and_the_kind_re_derived(self):
        # The dataset's source has moved on since the stash was written (a
        # query rewritten into a table reference) -- reusing the stale kind
        # would silently misread the new source under the old rules.
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS",
            dataset_stash={
                DATASET_STASH_TML_OBJECT: "sql_view",
                DATASET_STASH_TML_OBJECT_WITNESS: "SELECT * FROM orders",
            },
        )
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.kind == "table"
        assert any(i["code"] == "TS-DATASET-TML-OBJECT-STALE" for i in log.as_dicts())

    def test_a_stashed_source_parts_entry_is_used_when_it_still_agrees(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS",
            dataset_stash={
                DATASET_STASH_SOURCE_PARTS: {
                    DATASET_STASH_SOURCE_PARTS_DB: "SALES",
                    DATASET_STASH_SOURCE_PARTS_SCHEMA: "PUBLIC",
                    DATASET_STASH_SOURCE_PARTS_DB_TABLE: "ORDERS",
                }
            },
        )
        table = build_table(dataset, IssueLog())
        assert (table.body["db"], table.body["schema"], table.body["db_table"]) == (
            "SALES", "PUBLIC", "ORDERS",
        )

    def test_a_stale_stashed_source_parts_entry_is_dropped_and_re_derived(self):
        # The dataset's source has moved on since the stash was written --
        # reusing the stale parts would silently discard the edit.
        dataset = _dataset(
            "orders", "SALES.PUBLIC.RENAMED_ORDERS",
            dataset_stash={
                DATASET_STASH_SOURCE_PARTS: {
                    DATASET_STASH_SOURCE_PARTS_DB: "SALES",
                    DATASET_STASH_SOURCE_PARTS_SCHEMA: "PUBLIC",
                    DATASET_STASH_SOURCE_PARTS_DB_TABLE: "ORDERS",
                }
            },
        )
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.body["db_table"] == "RENAMED_ORDERS"
        assert any(i["code"] == "TS-DATASET-SOURCE-PARTS-STALE" for i in log.as_dicts())


class TestQuotedIdentifierIsNotMisreadAsAQuery:
    """A quoted identifier segment may legitimately contain whitespace
    (`"ORDER TABLE"`) -- classifying a source by "contains whitespace"
    alone would misread it as a query and emit an unimportable sql_view
    document with the whole dotted string as its query, with no issue to
    say so."""

    def test_a_quoted_identifier_with_a_space_is_still_a_table(self):
        dataset = _dataset("orders", 'SALES.PUBLIC."ORDER TABLE"')
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.kind == "table"
        assert (table.body["db"], table.body["schema"], table.body["db_table"]) == (
            "SALES", "PUBLIC", "ORDER TABLE",
        )
        assert not [i for i in log.as_dicts() if "SOURCE" in i["code"]]

    def test_an_ordinary_three_part_name_is_unaffected(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        table = build_table(dataset, IssueLog())
        assert table.kind == "table"
        assert table.body["db_table"] == "ORDERS"

    def test_a_genuine_query_is_still_a_sql_view(self):
        dataset = _dataset("recent_orders", "SELECT * FROM orders WHERE recent = true")
        table = build_table(dataset, IssueLog())
        assert table.kind == "sql_view"


class TestConnectionDependentSpelling:
    def test_boolean_spelling_is_taken_from_the_stash_when_present(self):
        field = _physical(
            "is_active", datatype="Boolean",
            field_stash={FIELD_STASH_DATA_TYPE: "BOOL", FIELD_STASH_DATA_TYPE_WITNESS: "Boolean"},
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        table = build_table(dataset, IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "BOOL"

    def test_boolean_spelling_defaults_when_no_stash_is_present(self):
        field = _physical("is_active", datatype="Boolean")
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        table = build_table(dataset, IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "BOOLEAN"

    def test_float_spelling_is_taken_from_the_stash_when_present(self):
        field = _physical(
            "weight", datatype="Float",
            field_stash={FIELD_STASH_DATA_TYPE: "FLOAT", FIELD_STASH_DATA_TYPE_WITNESS: "Float"},
        )
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "FLOAT"
        # Still a declared loss -- the stash only fixes the spelling, not the
        # Float/Decimal collapse itself.
        assert any(i["code"] == "TS-FIELD-DATATYPE-DECLARED-LOSS" for i in log.as_dicts())

    def test_float_spelling_defaults_when_no_stash_is_present(self):
        field = _physical("weight", datatype="Float")
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        table = build_table(dataset, IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "DOUBLE"


class TestReload:
    def test_the_emitted_table_document_reloads(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS",
            fields=[_physical("order_date"), _physical("amount")],
            dataset_stash={DATASET_STASH_CONNECTION_NAME: "My Snowflake"},
        )
        table = build_table(dataset, IssueLog())
        reloaded = load_document(_dump(table))
        assert reloaded.kind == "table"
        assert reloaded.body["name"] == dataset["name"]

    def test_the_emitted_sql_view_document_reloads(self):
        dataset = _dataset(
            "recent_orders", "SELECT * FROM orders",
            fields=[_physical("order_id")],
            dataset_stash={DATASET_STASH_CONNECTION_NAME: "My Snowflake"},
        )
        table = build_table(dataset, IssueLog())
        reloaded = load_document(_dump(table))
        assert reloaded.kind == "sql_view"
        assert reloaded.body["sql_query"] == "SELECT * FROM orders"


class TestConnectionFallback:
    def test_a_connection_name_argument_is_used_when_nothing_is_stashed(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        table = build_table(dataset, IssueLog(), connection_name="Fallback Connection")
        assert table.body["connection"] == {"name": "Fallback Connection"}

    def test_a_stashed_connection_name_wins_over_the_argument(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS", dataset_stash={DATASET_STASH_CONNECTION_NAME: "Stashed Connection"}
        )
        table = build_table(dataset, IssueLog(), connection_name="Fallback Connection")
        assert table.body["connection"] == {"name": "Stashed Connection"}

    def test_no_connection_at_all_omits_the_block_and_raises_an_issue(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS")
        log = IssueLog()
        table = build_table(dataset, log)
        assert "connection" not in table.body
        assert any(i["code"] == "TS-DATASET-CONNECTION-MISSING" for i in log.as_dicts())


# ---------------------------------------------------------------------------
# Own tests: the two shapes judged most likely to hide a real bug.
# ---------------------------------------------------------------------------

class TestComputedFieldsAreNotMisreadAsColumns:
    """A dataset with a genuine computed field mixed in among physical ones is
    exactly the input a Model-building step will hand this module in practice
    (a dataset's Ossie fields are not pre-sorted into physical vs. computed).
    Getting this wrong either drops a physical column or invents a column
    for a formula -- both produce a Table document a Model can silently
    reference incorrectly, one document kind an isolated single-field test
    would never exercise."""

    def test_a_computed_field_produces_no_table_column(self):
        physical = _physical("amount")
        computed = _computed("net_amount", "[ORDERS::amount] - [ORDERS::cost]")
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[physical, computed])
        table = build_table(dataset, IssueLog())
        names = {c["name"] for c in table.body["columns"]}
        assert names == {"amount"}

    def test_a_thoughtspot_only_aggregate_expression_is_also_skipped(self):
        # The THOUGHTSPOT dialect is present but is a function call, not a
        # bare reference -- must not be mistaken for a bracketed physical
        # column just because a bracket appears somewhere inside it.
        computed = _computed("total", "sum ( [ORDERS::amount] )")
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[computed])
        table = build_table(dataset, IssueLog())
        assert table.body["columns"] == []


class TestSqlViewColumnsUseOutputAliasNotDbColumnName:
    """SQL View columns bind to a query output alias (`sql_output_column`),
    never `db_column_name` -- a prior task shipped with SQL views completely
    broken while every existing test used a table document, because nothing
    exercised the SQL View column shape at all. These tests exist so that
    failure mode cannot repeat silently here."""

    def test_sql_view_columns_carry_sql_output_column_not_db_column_name(self):
        field = _physical("customer_id", "cust_id_out")
        dataset = _dataset("recent_orders", "SELECT cust_id_out FROM orders", fields=[field])
        table = build_table(dataset, IssueLog())
        column = table.body["sql_view_columns"][0]
        assert column["sql_output_column"] == "cust_id_out"
        assert "db_column_name" not in column

    def test_a_stashed_output_alias_wins_over_the_expressions_own_identifier(self):
        field = _round_tripped_physical("customer_id", "recent_orders", "customer_id")
        dataset = _dataset(
            "recent_orders", "SELECT cust_id_out AS customer_id FROM orders",
            fields=[field],
            dataset_stash={
                DATASET_STASH_TML_OBJECT: "sql_view",
                DATASET_STASH_SQL_OUTPUT_COLUMNS: {"customer_id": "cust_id_out"},
            },
        )
        table = build_table(dataset, IssueLog())
        column = table.body["sql_view_columns"][0]
        assert column["sql_output_column"] == "cust_id_out"

    def test_unsurfaced_columns_land_in_sql_view_columns_not_columns(self):
        dataset = _dataset(
            "recent_orders", "SELECT a, b FROM orders",
            dataset_stash={
                DATASET_STASH_TML_OBJECT: "sql_view",
                DATASET_STASH_UNSURFACED_COLUMNS: [
                    {"name": "b", "sql_output_column": "b",
                     "db_column_properties": {"data_type": "VARCHAR"}}
                ],
            },
        )
        table = build_table(dataset, IssueLog())
        assert "columns" not in table.body
        assert table.body["sql_view_columns"] == [
            {"name": "b", "sql_output_column": "b", "db_column_properties": {"data_type": "VARCHAR"}}
        ]


class TestUnsurfacedColumns:
    def test_unsurfaced_table_columns_are_restored_verbatim(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS",
            fields=[_physical("amount")],
            dataset_stash={
                DATASET_STASH_UNSURFACED_COLUMNS: [
                    {"name": "internal_flag", "db_column_name": "INTERNAL_FLAG",
                     "db_column_properties": {"data_type": "BOOLEAN"}}
                ]
            },
        )
        table = build_table(dataset, IssueLog())
        names = [c["name"] for c in table.body["columns"]]
        assert names == ["amount", "internal_flag"]


class TestDatasetAiContextHasNoHomeInTml:
    """Table TML (both kinds) has no synonym or instruction field at all --
    this loss is unconditional, so it must always be reported, not only when
    some other recovery path happens to fail."""

    def test_a_string_ai_context_raises_an_issue(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[_physical("amount")])
        dataset["ai_context"] = "Use this table for revenue questions."
        log = IssueLog()
        build_table(dataset, log)
        assert any(i["code"] == "TS-DATASET-AI-CONTEXT-UNSUPPORTED" for i in log.as_dicts())

    def test_an_object_ai_context_also_raises_an_issue(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[_physical("amount")])
        dataset["ai_context"] = {"synonyms": ["sales"]}
        log = IssueLog()
        build_table(dataset, log)
        assert any(i["code"] == "TS-DATASET-AI-CONTEXT-UNSUPPORTED" for i in log.as_dicts())

    def test_no_ai_context_raises_nothing(self):
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[_physical("amount")])
        log = IssueLog()
        build_table(dataset, log)
        assert not [i for i in log.as_dicts() if i["code"] == "TS-DATASET-AI-CONTEXT-UNSUPPORTED"]


class TestRoundTripAgainstTheForwardDirection:
    """Feed a real TML Table document through the forward direction and back
    through `build_table`, and compare against the original -- the strongest
    check available, because a unit test built from a hand-written Ossie
    fixture can be unknowingly wrong about what the forward direction
    actually produces."""

    def _model_and_table(self):
        table_doc = TmlDocument(
            kind="table",
            body={
                "name": "ORDERS",
                "db": "SALES", "schema": "PUBLIC", "db_table": "ORDERS",
                "connection": {"name": "My Snowflake"},
                "columns": [
                    {"name": "Order Date", "db_column_name": "O_ORDERDATE",
                     "db_column_properties": {"data_type": "DATE"}},
                    {"name": "Amount", "db_column_name": "O_AMOUNT",
                     "db_column_properties": {"data_type": "DOUBLE"}},
                    {"name": "Is Priority", "db_column_name": "O_IS_PRIORITY",
                     "db_column_properties": {"data_type": "BOOL"}},
                    {"name": "Internal Note", "db_column_name": "O_NOTE",
                     "db_column_properties": {"data_type": "VARCHAR"}},
                ],
            },
            guid=None,
        )
        model_doc = TmlDocument(
            kind="model",
            body={
                "name": "Sales Analytics",
                "model_tables": [{"name": "ORDERS"}],
                "columns": [
                    {"name": "Order Date", "column_id": "ORDERS::Order Date",
                     "properties": {"column_type": "ATTRIBUTE"}},
                    {"name": "Amount", "column_id": "ORDERS::Amount",
                     "properties": {"column_type": "ATTRIBUTE"}},
                    {"name": "Is Priority", "column_id": "ORDERS::Is Priority",
                     "properties": {"column_type": "ATTRIBUTE"}},
                    # "Internal Note" is deliberately not surfaced -- it must
                    # come back as an unsurfaced column, not a field.
                ],
            },
            guid=None,
        )
        return DocumentSet(model=model_doc, tables=(table_doc,)), table_doc

    def test_round_trip_reproduces_the_original_table_structurally(self):
        document_set, original = self._model_and_table()
        ossie = tml_to_ossie_convert(document_set)
        [dataset] = ossie.model["semantic_model"][0]["datasets"]

        log = IssueLog()
        rebuilt = build_table(dataset, log)

        assert rebuilt.kind == "table"
        assert rebuilt.body["name"] == original.body["name"]
        assert rebuilt.body["db"] == original.body["db"]
        assert rebuilt.body["schema"] == original.body["schema"]
        assert rebuilt.body["db_table"] == original.body["db_table"]
        assert rebuilt.body["connection"] == original.body["connection"]

        by_name = {c["name"]: c for c in rebuilt.body["columns"]}
        assert set(by_name) == {c["name"] for c in original.body["columns"]}
        for original_column in original.body["columns"]:
            rebuilt_column = by_name[original_column["name"]]
            assert rebuilt_column["db_column_properties"]["data_type"] == (
                original_column["db_column_properties"]["data_type"]
            )

        # "Internal Note" was never surfaced by the Model, so it round-trips
        # verbatim through unsurfaced_columns, db_column_name included.
        assert by_name["Internal Note"]["db_column_name"] == "O_NOTE"

        # "Order Date", "Amount" and "Is Priority" WERE surfaced, and each
        # has a display name that differs from its true db_column_name. The
        # forward direction stashes that true name separately for exactly
        # this case, and it round-trips exactly rather than falling back to
        # the display-name assumption. A hand-written fixture that happens
        # to set bracket-name equal to db_column_name would never expose a
        # regression here; only a real forward-then-reverse round trip does.
        assert by_name["Order Date"]["db_column_name"] == "O_ORDERDATE"
        assert by_name["Amount"]["db_column_name"] == "O_AMOUNT"
        assert by_name["Is Priority"]["db_column_name"] == "O_IS_PRIORITY"
        assert not [i for i in log.as_dicts() if i["code"] == "TS-FIELD-DB-COLUMN-NAME-ASSUMED"]
