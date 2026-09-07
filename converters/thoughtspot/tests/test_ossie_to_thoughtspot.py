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

"""Tests for the public `convert` entry point (Ossie -> TML), inline-join
placement, and the stash-restoration witness.

Three things are new here relative to the other `ossie_to_thoughtspot`
test modules: `convert()` itself (build_table/build_model already have
their own dedicated files), the two places this converter now applies its
stash-if-present-**and-still-current**-else-derive rule rather than
plain stash-if-present, and a round trip that drives the two public entry
points back to back (`tml_to_ossie.convert` then `ossie_to_thoughtspot.
convert`) rather than a hand-built Ossie fixture.
"""
import json

import pytest

from ossie_thoughtspot.constants import (
    FIELD_STASH_DATA_TYPE,
    FIELD_STASH_DATA_TYPE_WITNESS,
    RELATIONSHIP_STASH_CARDINALITY,
    RELATIONSHIP_STASH_ON_EXPRESSION,
    RELATIONSHIP_STASH_ON_EXPRESSION_WITNESS,
    RELATIONSHIP_STASH_TYPE,
)
from ossie_thoughtspot.errors import ConversionError
from ossie_thoughtspot.issues import IssueLog
from ossie_thoughtspot.ossie_to_thoughtspot import TmlConversion, build_model, build_table, convert
from ossie_thoughtspot.tml import (
    DocumentSet,
    TmlDocument,
    dump_document,
    dump_document_set,
    load_document,
    load_document_set,
)
from ossie_thoughtspot.tml_to_ossie import convert as tml_to_ossie_convert

# ---------------------------------------------------------------------------
# Fixture builders -- the same conventions test_ossie_to_thoughtspot_model.py
# and test_ossie_to_thoughtspot_tables.py use, kept local rather than shared
# so each test module's fixtures stay self-contained.
# ---------------------------------------------------------------------------


def _stash_ext(**payload):
    return [{"vendor_name": "THOUGHTSPOT", "data": json.dumps({"_v": 1, **payload})}]


def _dialects(*pairs):
    return [{"dialect": d, "expression": e} for d, e in pairs]


def _field(name, dialects, *, label=None, datatype=None, description=None, field_stash=None):
    field: dict = {"name": name}
    if label is not None:
        field["label"] = label
    field["expression"] = {"dialects": dialects}
    if datatype is not None:
        field["datatype"] = datatype
    if description is not None:
        field["description"] = description
    if field_stash is not None:
        field["custom_extensions"] = _stash_ext(**field_stash)
    return field


def _round_tripped(name, table, column, **kwargs):
    """A field whose expression is the verbatim THOUGHTSPOT bracket a prior
    TML -> Ossie trip would have produced -- the shape build_table/build_model
    treat as authoritative over any ANSI_SQL sibling."""
    return _field(name, _dialects(("THOUGHTSPOT", f"[{table}::{column}]")), **kwargs)


def _hand_authored_physical(name, identifier=None, **kwargs):
    """A field whose expression is a single bare SQL identifier and no
    THOUGHTSPOT dialect entry at all -- the shape a hand-authored Ossie
    document (never round-tripped through TML) uses for a physical column."""
    return _field(name, _dialects(("ANSI_SQL", identifier or name)), **kwargs)


def _metric(name, dialects, *, description=None, metric_stash=None):
    metric: dict = {"name": name, "expression": {"dialects": dialects}}
    if description is not None:
        metric["description"] = description
    if metric_stash is not None:
        metric["custom_extensions"] = _stash_ext(**metric_stash)
    return metric


def _dataset(name, source, fields=None, *, dataset_stash=None):
    dataset: dict = {"name": name, "source": source}
    if fields is not None:
        dataset["fields"] = fields
    if dataset_stash is not None:
        dataset["custom_extensions"] = _stash_ext(**dataset_stash)
    return dataset


def _semantic_model(name="test_model", datasets=None, metrics=None, relationships=None, model_stash=None):
    model: dict = {"name": name, "datasets": datasets or []}
    if metrics is not None:
        model["metrics"] = metrics
    if relationships is not None:
        model["relationships"] = relationships
    if model_stash is not None:
        model["custom_extensions"] = _stash_ext(**model_stash)
    return model


def _relationship(name, from_, to, from_columns, to_columns, *, rel_stash=None):
    relationship: dict = {
        "name": name, "from": from_, "to": to,
        "from_columns": from_columns, "to_columns": to_columns,
    }
    if rel_stash is not None:
        relationship["custom_extensions"] = _stash_ext(**rel_stash)
    return relationship


def _ossie_document(*semantic_models):
    return {"version": "0.2.0.dev0", "semantic_model": list(semantic_models)}


def _table_doc(name, columns, connection="My Snowflake"):
    return TmlDocument(
        kind="table",
        body={
            "name": name, "db": "SALES", "schema": "PUBLIC", "db_table": name,
            "connection": {"name": connection}, "columns": columns,
        },
        guid=None,
    )


def _sql_view_doc(name, sql_query, columns, connection="My Snowflake"):
    return TmlDocument(
        kind="sql_view",
        body={
            "name": name, "sql_query": sql_query,
            "connection": {"name": connection}, "sql_view_columns": columns,
        },
        guid=None,
    )


def _column(name, db_column_name=None, data_type="VARCHAR"):
    return {"name": name, "db_column_name": db_column_name or name,
            "db_column_properties": {"data_type": data_type}}


def _sql_view_column(name, sql_output_column=None, data_type="VARCHAR"):
    return {"name": name, "sql_output_column": sql_output_column or name,
            "db_column_properties": {"data_type": data_type}}


def _model_tml(name, model_tables, columns, formulas=None):
    body: dict = {"name": name, "model_tables": model_tables, "columns": columns}
    if formulas is not None:
        body["formulas"] = formulas
    return TmlDocument(kind="model", body=body, guid=None)


def _find_key(value, key):
    """Whether `key` appears anywhere in `value`, at any depth -- the "no
    guid anywhere" rule needs to look past the document root, since a nested
    guid is exactly as import-breaking as a root one (tml.py strips guids
    unconditionally at dump time, but build_model/build_table must also
    never *emit* one in the first place)."""
    if isinstance(value, dict):
        return key in value or any(_find_key(v, key) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_find_key(v, key) for v in value)
    return False


# ---------------------------------------------------------------------------
# convert(): the public entry point itself.
# ---------------------------------------------------------------------------


class TestConvertEntryPoint:
    def test_convert_returns_a_document_set_with_a_model_and_its_tables(self):
        orders = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _round_tripped("amount", "orders", "Amount", label="Amount"),
        ])
        model = _semantic_model(datasets=[orders])
        result = convert(_ossie_document(model))

        assert isinstance(result, TmlConversion)
        assert isinstance(result.documents, DocumentSet)
        assert result.documents.model.kind == "model"
        assert [t.body["name"] for t in result.documents.tables] == ["orders"]
        assert isinstance(result.issues, IssueLog)

    def test_no_semantic_model_at_all_is_a_hard_failure(self):
        with pytest.raises(ConversionError):
            convert({"version": "0.2.0.dev0", "semantic_model": []})

    def test_missing_semantic_model_key_is_a_hard_failure(self):
        with pytest.raises(ConversionError):
            convert({"version": "0.2.0.dev0"})

    def test_more_than_one_semantic_model_is_a_hard_failure_naming_both(self):
        first = _semantic_model(name="first")
        second = _semantic_model(name="second")
        with pytest.raises(ConversionError, match="first"):
            convert(_ossie_document(first, second))

    def test_no_guid_appears_anywhere_in_the_emitted_document_set(self):
        # The no-guid-anywhere rule, proven at the deepest fixture this file builds: a join, a
        # formula cross-reference, a metric and a stashed foreign extension
        # all present at once.
        orders_ds = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _round_tripped("amount", "orders", "Amount", label="Amount"),
            _round_tripped("cost", "orders", "Cost", label="Cost"),
        ])
        customers_ds = _dataset("customers", "SALES.PUBLIC.CUSTOMERS", fields=[
            _round_tripped("id", "customers", "Id", label="Id"),
        ])
        relationship = _relationship("orders_to_customers", "orders", "customers", ["Amount"], ["Id"])
        metric = _metric("total", _dialects(("THOUGHTSPOT", "sum ( [orders::Amount] )")))
        model = _semantic_model(
            datasets=[orders_ds, customers_ds], metrics=[metric], relationships=[relationship],
        )
        result = convert(_ossie_document(model))

        assert not _find_key(result.documents.model.body, "guid")
        for table in result.documents.tables:
            assert not _find_key(table.body, "guid")

    def test_a_hand_authored_document_with_no_stash_at_all_converts(self):
        # A genuinely hand-authored Ossie file: no custom_extensions
        # anywhere, physical fields as bare identifiers, no THOUGHTSPOT
        # dialect entries. This must still produce an importable document
        # set -- the "else-derive" half of the witness rule: every stashed key needs a
        # derivation or a documented default, since a hand-authored
        # document has no stash to fall back on at all.
        orders = _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[
            _hand_authored_physical("order_date", datatype="Date"),
            _hand_authored_physical("amount", datatype="Decimal"),
        ])
        customers = _dataset("customers", "SALES.PUBLIC.CUSTOMERS", fields=[
            _hand_authored_physical("id", datatype="Integer"),
        ])
        relationship = _relationship("orders_to_customers", "orders", "customers", ["amount"], ["id"])
        model = _semantic_model(
            name="hand_authored", datasets=[orders, customers], relationships=[relationship],
        )
        result = convert(_ossie_document(model))

        assert not result.issues.has_errors()
        texts = dump_document_set(result.documents)
        reloaded = load_document_set(texts)
        assert reloaded.model.kind == "model"
        assert {t.kind for t in reloaded.tables} == {"table"}
        # A connection name was never supplied -- build_table names the gap
        # rather than inventing one, per its own documented contract.
        assert any(i["code"] == "TS-DATASET-CONNECTION-MISSING" for i in result.issues.as_dicts())


# ---------------------------------------------------------------------------
# Inline join placement and type normalisation, through convert()'s own
# document -- build_model's join mechanics have their own dedicated tests in
# test_ossie_to_thoughtspot_model.py; these confirm the same invariants hold
# end to end through the public entry point.
# ---------------------------------------------------------------------------


class TestJoinPlacementThroughConvert:
    def _model_with_join(self, **rel_kwargs):
        orders = _dataset("orders", "SALES.PUBLIC.ORDERS")
        customers = _dataset("customers", "SALES.PUBLIC.CUSTOMERS")
        relationship = _relationship(
            "orders_to_customers", "orders", "customers", ["Customer Id"], ["Id"], **rel_kwargs
        )
        return _semantic_model(datasets=[orders, customers], relationships=[relationship])

    def test_the_join_lives_on_the_source_entry_never_at_model_top_level(self):
        result = convert(_ossie_document(self._model_with_join()))
        body = result.documents.model.body
        assert "joins" not in body
        [orders_entry] = [t for t in body["model_tables"] if t["name"] == "orders"]
        assert len(orders_entry["joins"]) == 1
        assert orders_entry["joins"][0]["with"] == "customers"
        [customers_entry] = [t for t in body["model_tables"] if t["name"] == "customers"]
        assert "joins" not in customers_entry

    def test_the_on_key_survives_dump_and_reload_as_a_plain_string(self):
        # 'on' is a YAML 1.1 reserved word -- tml.py's codec has to quote it
        # or a reload coerces the key itself, not just a value.
        result = convert(_ossie_document(self._model_with_join()))
        text = dump_document(result.documents.model)
        assert "'on':" in text
        reloaded = load_document(text)
        [orders_entry] = [t for t in reloaded.body["model_tables"] if t["name"] == "orders"]
        assert orders_entry["joins"][0]["on"] == "[orders::Customer Id] = [customers::Id]"

    @pytest.mark.parametrize("spelling", ["FULL_OUTER", "FULL OUTER", "full_outer"])
    def test_full_outer_becomes_outer_on_a_relationship_join(self, spelling):
        model = self._model_with_join(
            rel_stash={RELATIONSHIP_STASH_TYPE: spelling, RELATIONSHIP_STASH_CARDINALITY: "MANY_TO_ONE"},
        )
        result = convert(_ossie_document(model))
        [orders_entry] = [t for t in result.documents.model.body["model_tables"] if t["name"] == "orders"]
        assert orders_entry["joins"][0]["type"] == "OUTER"

    def test_full_outer_becomes_outer_on_an_unrepresentable_join_too(self):
        # The rename applies "in every context TML accepts a join type at
        # all" -- unrepresentable_joins[] is the other one this module emits.
        orders = _dataset("orders", "SALES.PUBLIC.ORDERS")
        fx_rates = _dataset("fx_rates", "SALES.PUBLIC.FX_RATES")
        model = _semantic_model(
            datasets=[orders, fx_rates],
            model_stash={
                "unrepresentable_joins": [{
                    "from": "orders", "to": "fx_rates",
                    RELATIONSHIP_STASH_ON_EXPRESSION: "[orders::Order Date] >= [fx_rates::Effective Date]",
                    RELATIONSHIP_STASH_TYPE: "FULL_OUTER",
                    RELATIONSHIP_STASH_CARDINALITY: "MANY_TO_ONE",
                }],
            },
        )
        result = convert(_ossie_document(model))
        [orders_entry] = [t for t in result.documents.model.body["model_tables"] if t["name"] == "orders"]
        assert orders_entry["joins"][0]["type"] == "OUTER"

    def test_missing_type_and_cardinality_default_rather_than_being_omitted(self):
        # TML requires both keys on every join -- a document with
        # neither stashed must still emit both, never leave one out.
        result = convert(_ossie_document(self._model_with_join()))
        [orders_entry] = [t for t in result.documents.model.body["model_tables"] if t["name"] == "orders"]
        [join] = orders_entry["joins"]
        assert join["type"] == "INNER"
        assert join["cardinality"] == "MANY_TO_ONE"
        assert set(join) == {"with", "on", "type", "cardinality"}


# ---------------------------------------------------------------------------
# Stash-if-present-and-still-current-else-derive, for a relationship's
# on_expression. The obvious reading ("use the stash if it is there") is
# wrong: it silently discards a retargeted relationship's edit.
# ---------------------------------------------------------------------------


class TestOnExpressionWitness:
    _NARROWED_CONDITION = (
        "[orders::Currency] = [fx_rates::Currency] and "
        "[orders::Order Date] >= [fx_rates::Effective Date]"
    )

    def _tables(self):
        orders = _table_doc("orders", [
            _column("Order Date", "ORDER_DATE", "DATE"),
            _column("Currency", "CURRENCY", "VARCHAR"),
        ])
        fx_rates = _table_doc("fx_rates", [
            _column("Effective Date", "EFFECTIVE_DATE", "DATE"),
            _column("Currency", "CURRENCY", "VARCHAR"),
        ])
        return orders, fx_rates

    def _model(self, relationship):
        return _semantic_model(
            datasets=[
                _dataset("orders", "SALES.PUBLIC.ORDERS"),
                _dataset("fx_rates", "SALES.PUBLIC.FX_RATES"),
            ],
            relationships=[relationship],
        )

    def test_a_witness_that_still_matches_restores_the_verbatim_condition(self):
        relationship = _relationship(
            "orders_to_fx", "orders", "fx_rates", ["Currency"], ["Currency"],
            rel_stash={
                RELATIONSHIP_STASH_ON_EXPRESSION: self._NARROWED_CONDITION,
                RELATIONSHIP_STASH_ON_EXPRESSION_WITNESS: [["Currency"], ["Currency"]],
                RELATIONSHIP_STASH_TYPE: "INNER",
                RELATIONSHIP_STASH_CARDINALITY: "MANY_TO_ONE",
            },
        )
        orders, fx_rates = self._tables()
        log = IssueLog()
        doc = build_model(self._model(relationship), [orders, fx_rates], log)

        [orders_entry] = [t for t in doc.body["model_tables"] if t["name"] == "orders"]
        assert orders_entry["joins"][0]["on"] == self._NARROWED_CONDITION
        assert not [i for i in log.as_dicts() if i["code"] == "TS-JOIN-ON-EXPRESSION-STALE"]

    def test_a_witness_that_no_longer_matches_is_dropped_and_re_derived(self):
        # from_columns/to_columns were retargeted after the stash was
        # written -- the witness still names the OLD pairing (Currency).
        relationship = _relationship(
            "orders_to_fx", "orders", "fx_rates", ["Order Date"], ["Effective Date"],
            rel_stash={
                RELATIONSHIP_STASH_ON_EXPRESSION: self._NARROWED_CONDITION,
                RELATIONSHIP_STASH_ON_EXPRESSION_WITNESS: [["Currency"], ["Currency"]],
                RELATIONSHIP_STASH_TYPE: "INNER",
                RELATIONSHIP_STASH_CARDINALITY: "MANY_TO_ONE",
            },
        )
        orders, fx_rates = self._tables()
        log = IssueLog()
        doc = build_model(self._model(relationship), [orders, fx_rates], log)

        [orders_entry] = [t for t in doc.body["model_tables"] if t["name"] == "orders"]
        # Re-derived from the CURRENT from_columns/to_columns -- the stale
        # verbatim text (and the residual narrowing it carried) is dropped,
        # not silently kept.
        assert orders_entry["joins"][0]["on"] == "[orders::Order Date] = [fx_rates::Effective Date]"
        assert any(i["code"] == "TS-JOIN-ON-EXPRESSION-STALE" for i in log.as_dicts())

    def test_no_stash_at_all_converts_using_the_plain_equality_condition(self):
        # A hand-authored relationship with no custom_extensions at all must
        # still convert, with no staleness issue raised -- there is nothing
        # stale about a value that was never there in the first place.
        relationship = _relationship("orders_to_fx", "orders", "fx_rates", ["Currency"], ["Currency"])
        orders, fx_rates = self._tables()
        log = IssueLog()
        doc = build_model(self._model(relationship), [orders, fx_rates], log)

        [orders_entry] = [t for t in doc.body["model_tables"] if t["name"] == "orders"]
        assert orders_entry["joins"][0]["on"] == "[orders::Currency] = [fx_rates::Currency]"
        assert not [i for i in log.as_dicts() if i["code"] == "TS-JOIN-ON-EXPRESSION-STALE"]


# ---------------------------------------------------------------------------
# The same witness rule again, on a second construct: FIELD_STASH_DATA_TYPE. Reading
# _field_datatype revealed the exact same stash-if-present pattern to
# warn against for on_expression, just on a different key: a field whose
# `datatype` is edited after the stash was written (Boolean -> String,
# say) would silently keep emitting the OLD warehouse spelling (BOOL) for
# a column that is no longer Boolean at all. Worth its own test because it
# proves the fix is systemic -- every stash that shadows a live, editable
# Ossie value needs a witness -- not a one-off patch scoped to relationships.
# ---------------------------------------------------------------------------


class TestFieldDataTypeWitness:
    def _dataset_with(self, field):
        return _dataset("orders", "SALES.PUBLIC.ORDERS", fields=[field])

    def test_a_witness_that_still_matches_restores_the_stashed_spelling(self):
        field = _hand_authored_physical(
            "is_active", datatype="Boolean",
            field_stash={FIELD_STASH_DATA_TYPE: "BOOL", FIELD_STASH_DATA_TYPE_WITNESS: "Boolean"},
        )
        table = build_table(self._dataset_with(field), IssueLog())
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "BOOL"

    def test_a_witness_that_no_longer_matches_is_dropped_and_re_derived(self):
        # The field's datatype was edited (Boolean -> String) since the
        # spelling was stashed -- BOOL now names a warehouse type this
        # field no longer has.
        field = _hand_authored_physical(
            "is_active", datatype="String",
            field_stash={FIELD_STASH_DATA_TYPE: "BOOL", FIELD_STASH_DATA_TYPE_WITNESS: "Boolean"},
        )
        log = IssueLog()
        table = build_table(self._dataset_with(field), log)
        assert table.body["columns"][0]["db_column_properties"]["data_type"] == "VARCHAR"
        assert any(i["code"] == "TS-FIELD-DATA-TYPE-STASH-STALE" for i in log.as_dicts())


# ---------------------------------------------------------------------------
# A full round trip through both public entry points.
# ---------------------------------------------------------------------------


class TestFullRoundTripBothEntryPoints:
    """Build a rich TML document set by hand, run it forward
    (tml_to_ossie.convert), touch the intermediate Ossie document the way
    another tool legitimately might (append a foreign vendor's
    custom_extensions entry), run it back through this module's convert,
    and diff the result against the original.

    Covers: a Table and a SQL View, physical and computed fields, two
    metric shapes (`formula` and `column_aggregation`), an equality join
    and a non-equality join (with a residual predicate -- the exact
    on_expression-witness path TestOnExpressionWitness exercises directly,
    here exercised through a real round trip instead of a synthetic
    fixture), a pre-existing foreign vendor extension, and a YAML 1.1
    boolean column name ("On").
    """

    _FX_JOIN_CONDITION = (
        "[ORDERS::Currency] = [FX_RATES::Currency] and "
        "[ORDERS::Order Date] >= [FX_RATES::Effective Date]"
    )

    def _original(self):
        orders = _table_doc("ORDERS", [
            _column("Order Date", "O_ORDER_DATE", "DATE"),
            _column("Amount", "O_AMOUNT", "DOUBLE"),
            _column("Cost", "O_COST", "DOUBLE"),
            _column("Currency", "O_CURRENCY", "VARCHAR"),
            _column("Customer Id", "O_CUSTOMER_ID", "INT64"),
            _column("On", "O_ON_FLAG", "VARCHAR"),
        ])
        customers = _table_doc("CUSTOMERS", [
            _column("Id", "ID", "INT64"),
            _column("Status", "C_STATUS", "VARCHAR"),
        ])
        fx_rates = _sql_view_doc(
            "FX_RATES", "SELECT CURRENCY, EFFECTIVE_DATE FROM RAW.FX",
            [
                _sql_view_column("Currency", "CURRENCY", "VARCHAR"),
                _sql_view_column("Effective Date", "EFFECTIVE_DATE", "DATE"),
            ],
        )
        model = _model_tml(
            "Sales Analytics",
            model_tables=[
                {"name": "ORDERS", "joins": [
                    {"with": "CUSTOMERS", "on": "[ORDERS::Customer Id] = [CUSTOMERS::Id]",
                     "type": "LEFT_OUTER", "cardinality": "MANY_TO_ONE"},
                    {"with": "FX_RATES", "on": self._FX_JOIN_CONDITION,
                     "type": "INNER", "cardinality": "MANY_TO_ONE"},
                ]},
                {"name": "CUSTOMERS"},
                {"name": "FX_RATES"},
            ],
            columns=[
                {"name": "Order Date", "column_id": "ORDERS::Order Date",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Amount", "column_id": "ORDERS::Amount",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Cost", "column_id": "ORDERS::Cost",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Currency", "column_id": "ORDERS::Currency",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Customer Id", "column_id": "ORDERS::Customer Id",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "On", "column_id": "ORDERS::On",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Status", "column_id": "CUSTOMERS::Status",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "Net Amount", "formula_id": "formula_net_amount",
                 "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "total_revenue", "formula_id": "formula_total_revenue",
                 "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
                {"name": "customer_count", "column_id": "CUSTOMERS::Id",
                 "properties": {"column_type": "MEASURE", "aggregation": "COUNT_DISTINCT"}},
            ],
            formulas=[
                {"id": "formula_net_amount", "name": "Net Amount",
                 "expr": "[ORDERS::Amount] - [ORDERS::Cost]"},
                {"id": "formula_total_revenue", "name": "total_revenue",
                 "expr": "sum ( [ORDERS::Amount] )"},
            ],
        )
        return DocumentSet(model=model, tables=(orders, customers, fx_rates))

    def _round_trip(self):
        original = self._original()
        forward = tml_to_ossie_convert(original)
        assert not forward.issues.has_errors()

        ossie_document = forward.model
        orders_dataset = next(
            d for d in ossie_document["semantic_model"][0]["datasets"] if d["name"] == "ORDERS"
        )
        # Simulate another tool having already touched the intermediate
        # Ossie document -- the scenario write_stash's foreign-vendor
        # preservation guards against, and the only place a
        # "foreign vendor extension" can meaningfully appear in a
        # TML -> Ossie -> TML round trip, since TML itself has no
        # extension mechanism at all for one to originate from.
        foreign_entry = {"vendor_name": "DATABRICKS", "data": json.dumps({"note": "unrelated"})}
        orders_dataset.setdefault("custom_extensions", []).append(foreign_entry)

        result = convert(ossie_document)
        assert not result.issues.has_errors()
        return original, ossie_document, foreign_entry, result

    def test_the_foreign_vendor_entry_is_never_touched(self):
        _original, ossie_document, foreign_entry, _result = self._round_trip()
        orders_dataset = next(
            d for d in ossie_document["semantic_model"][0]["datasets"] if d["name"] == "ORDERS"
        )
        assert foreign_entry in orders_dataset["custom_extensions"]

    def test_both_joins_restore_their_exact_original_condition_type_and_cardinality(self):
        original, _ossie_document, _foreign, result = self._round_trip()
        rebuilt_orders = next(
            t for t in result.documents.model.body["model_tables"] if t["name"] == "ORDERS"
        )
        original_orders = next(
            t for t in original.model.body["model_tables"] if t["name"] == "ORDERS"
        )
        rebuilt_joins = {j["with"]: j for j in rebuilt_orders["joins"]}
        original_joins = {j["with"]: j for j in original_orders["joins"]}

        assert set(rebuilt_joins) == set(original_joins)
        for target, original_join in original_joins.items():
            rebuilt_join = rebuilt_joins[target]
            assert rebuilt_join["on"] == original_join["on"]
            assert rebuilt_join["type"] == original_join["type"]
            assert rebuilt_join["cardinality"] == original_join["cardinality"]

    def test_the_non_equality_joins_residual_narrowing_survived_the_full_trip(self):
        # The concrete proof that TestOnExpressionWitness's synthetic case
        # is not synthetic-only: an unedited FX_RATES relationship comes
        # back with its ">=" narrowing intact, not collapsed to the bare
        # equality pair a stale or absent stash would produce.
        _original, _ossie_document, _foreign, result = self._round_trip()
        rebuilt_orders = next(
            t for t in result.documents.model.body["model_tables"] if t["name"] == "ORDERS"
        )
        [fx_join] = [j for j in rebuilt_orders["joins"] if j["with"] == "FX_RATES"]
        assert fx_join["on"] == self._FX_JOIN_CONDITION
        assert ">=" in fx_join["on"]

    def test_formula_backed_fields_and_metrics_round_trip_their_expr_byte_identical(self):
        original, _ossie_document, _foreign, result = self._round_trip()
        original_formulas = {f["id"]: f["expr"] for f in original.model.body["formulas"]}
        rebuilt_formulas = {f["id"]: f["expr"] for f in result.documents.model.body["formulas"]}
        assert rebuilt_formulas["formula_net_amount"] == original_formulas["formula_net_amount"]
        assert rebuilt_formulas["formula_total_revenue"] == original_formulas["formula_total_revenue"]

    def test_the_column_aggregation_metric_becomes_a_formula_a_declared_non_lossy_difference(self):
        # A metric is always emitted as a formula, never column_id +
        # aggregation, on the way back -- Ossie's Metric schema has no
        # column_id field at all. This is the one deliberate structural
        # difference the round trip produces; asserted explicitly here so
        # it reads as "expected", not as an unnoticed regression.
        _original, _ossie_document, _foreign, result = self._round_trip()
        columns = result.documents.model.body["columns"]
        customer_count = next(c for c in columns if c["name"] == "customer_count")
        assert "column_id" not in customer_count
        assert "formula_id" in customer_count
        assert customer_count["properties"]["column_type"] == "MEASURE"

    def test_table_and_sql_view_documents_round_trip_their_connection_and_source(self):
        original, _ossie_document, _foreign, result = self._round_trip()
        rebuilt_by_name = {t.body["name"]: t for t in result.documents.tables}

        original_orders = next(t for t in original.tables if t.body["name"] == "ORDERS")
        rebuilt_orders = rebuilt_by_name["ORDERS"]
        assert rebuilt_orders.kind == "table"
        assert rebuilt_orders.body["connection"] == original_orders.body["connection"]
        assert (rebuilt_orders.body["db"], rebuilt_orders.body["schema"], rebuilt_orders.body["db_table"]) == (
            original_orders.body["db"], original_orders.body["schema"], original_orders.body["db_table"],
        )

        original_fx = next(t for t in original.tables if t.body["name"] == "FX_RATES")
        rebuilt_fx = rebuilt_by_name["FX_RATES"]
        assert rebuilt_fx.kind == "sql_view"
        assert rebuilt_fx.body["sql_query"] == original_fx.body["sql_query"]
        rebuilt_fx_columns = {c["name"]: c["sql_output_column"] for c in rebuilt_fx.body["sql_view_columns"]}
        original_fx_columns = {c["name"]: c["sql_output_column"] for c in original_fx.body["sql_view_columns"]}
        assert rebuilt_fx_columns == original_fx_columns

    def test_the_yaml_1_1_boolean_token_column_name_survives_dump_and_reload(self):
        _original, _ossie_document, _foreign, result = self._round_trip()
        text = dump_document(result.documents.model)
        reloaded = load_document(text)
        on_column = next(c for c in reloaded.body["columns"] if c["column_id"] == "ORDERS::On")
        assert on_column["name"] == "On"

    def test_the_full_document_set_reloads_and_carries_no_guid(self):
        _original, _ossie_document, _foreign, result = self._round_trip()
        texts = dump_document_set(result.documents)
        reloaded = load_document_set(texts)
        assert reloaded.model.kind == "model"
        assert len(reloaded.tables) == 3
        assert not _find_key(result.documents.model.body, "guid")
        for table in result.documents.tables:
            assert not _find_key(table.body, "guid")
