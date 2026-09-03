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
            dataset_stash={"connection_name": "My Snowflake"},
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
        # must not be re-classified by the whitespace heuristic.
        dataset = _dataset(
            "recent_orders", "SELECT * FROM orders",
            dataset_stash={"tml_object": "sql_view"},
        )
        table = build_table(dataset, IssueLog())
        assert table.kind == "sql_view"

    def test_a_stashed_source_parts_entry_is_used_when_it_still_agrees(self):
        dataset = _dataset(
            "orders", "SALES.PUBLIC.ORDERS",
            dataset_stash={"source_parts": {"db": "SALES", "schema": "PUBLIC", "db_table": "ORDERS"}},
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
            dataset_stash={"source_parts": {"db": "SALES", "schema": "PUBLIC", "db_table": "ORDERS"}},
        )
        log = IssueLog()
        table = build_table(dataset, log)
        assert table.body["db_table"] == "RENAMED_ORDERS"
        assert any(i["code"] == "TS-DATASET-SOURCE-PARTS-STALE" for i in log.as_dicts())


class TestConnectionDependentSpelling:
    def test_boolean_spelling_is_taken_from_the_stash_when_present(self):
        field = _physical("is_active", datatype="Boolean", field_stash={"data_type": "BOOL"})
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        table = build_table(dataset, IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "BOOL"

    def test_boolean_spelling_defaults_when_no_stash_is_present(self):
        field = _physical("is_active", datatype="Boolean")
        dataset = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])
        table = build_table(dataset, IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "BOOLEAN"

    def test_float_spelling_is_taken_from_the_stash_when_present(self):
        field = _physical("weight", datatype="Float", field_stash={"data_type": "FLOAT"})
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
            dataset_stash={"connection_name": "My Snowflake"},
        )
        table = build_table(dataset, IssueLog())
        reloaded = load_document(_dump(table))
        assert reloaded.kind == "table"
        assert reloaded.body["name"] == dataset["name"]

    def test_the_emitted_sql_view_document_reloads(self):
        dataset = _dataset(
            "recent_orders", "SELECT * FROM orders",
            fields=[_physical("order_id")],
            dataset_stash={"connection_name": "My Snowflake"},
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
            "orders", "SALES.PUBLIC.ORDERS", dataset_stash={"connection_name": "Stashed Connection"}
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
            dataset_stash={"tml_object": "sql_view", "sql_output_columns": {"customer_id": "cust_id_out"}},
        )
        table = build_table(dataset, IssueLog())
        column = table.body["sql_view_columns"][0]
        assert column["sql_output_column"] == "cust_id_out"

    def test_unsurfaced_columns_land_in_sql_view_columns_not_columns(self):
        dataset = _dataset(
            "recent_orders", "SELECT a, b FROM orders",
            dataset_stash={
                "tml_object": "sql_view",
                "unsurfaced_columns": [
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
                "unsurfaced_columns": [
                    {"name": "internal_flag", "db_column_name": "INTERNAL_FLAG",
                     "db_column_properties": {"data_type": "BOOLEAN"}}
                ]
            },
        )
        table = build_table(dataset, IssueLog())
        names = [c["name"] for c in table.body["columns"]]
        assert names == ["amount", "internal_flag"]


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

        # "Order Date" and "Amount" WERE surfaced. Their real db_column_name
        # ("O_ORDERDATE", "O_AMOUNT") is genuinely not recoverable: the
        # forward direction matches a physical column by display name only,
        # so nothing about the original db_column_name reaches the Ossie
        # document at all when it differs from the display name. A
        # hand-written fixture can accidentally set bracket-name equal to
        # db_column_name and never expose this; only a real forward-then-
        # reverse round trip does. The documented default (db_column_name ==
        # display name) is what comes back, and it is reported rather than
        # silent for exactly this reason.
        assert by_name["Order Date"]["db_column_name"] == "Order Date"
        assert by_name["Amount"]["db_column_name"] == "Amount"
        assumed = [i for i in log.as_dicts() if i["code"] == "TS-FIELD-DB-COLUMN-NAME-ASSUMED"]
        assert len(assumed) == 2
