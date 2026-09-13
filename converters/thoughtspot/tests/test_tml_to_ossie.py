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

"""Tests for the assembler: datasets, the cross-model resolver, relationships,
key derivation, the model-scope stash, and `convert()`'s public entry point.

Fixtures build `TmlDocument`/`DocumentSet` objects directly (as
test_tml_to_ossie_fields.py and test_tml_to_ossie_metrics.py build raw column
dicts), rather than round-tripping through YAML text -- `convert()`'s input
contract is the dataclass, not the text format `tml.py` parses separately and
already tests on its own.
"""
import json
from pathlib import Path

import pytest

from ossie_thoughtspot import stash
from ossie_thoughtspot.constants import (
    DATASET_STASH_ALIAS,
    DATASET_STASH_SQL_OUTPUT_COLUMNS,
    DATASET_STASH_TABLE_NAME,
    DATASET_STASH_TML_OBJECT,
    DATASET_STASH_UNSURFACED_COLUMNS,
    FIELD_STASH_COLUMN_PROPERTIES,
    FIELD_STASH_DATA_TYPE,
    FIELD_STASH_DB_COLUMN_NAME,
    MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS,
    MODEL_STASH_COLUMN_GROUPS,
    MODEL_STASH_CONSTRAINTS,
    MODEL_STASH_FILTERS,
    MODEL_STASH_LESSON_PLANS,
    MODEL_STASH_MODEL_JOINS_WITH,
    MODEL_STASH_PARAMETERS,
    MODEL_STASH_UNATTRIBUTED_FORMULAS,
    MODEL_STASH_UNREPRESENTABLE_JOINS,
    RELATIONSHIP_STASH_CARDINALITY,
    RELATIONSHIP_STASH_ENDPOINTS_SWAPPED,
    RELATIONSHIP_STASH_ENDPOINTS_SWAPPED_WITNESS,
    RELATIONSHIP_STASH_JOIN_SHAPE,
    RELATIONSHIP_STASH_ON_EXPRESSION,
    RELATIONSHIP_STASH_REFERENCING_JOIN,
    RELATIONSHIP_STASH_TYPE,
)
from ossie_thoughtspot.errors import ConversionError
from ossie_thoughtspot.tml import DocumentSet, TmlDocument
from ossie_thoughtspot.tml_to_ossie import OssieConversion, convert


def _table(name, db="SALES", schema="PUBLIC", db_table=None, columns=None,
           connection="My Snowflake", **extra):
    body = {
        "name": name,
        "db": db,
        "schema": schema,
        "db_table": db_table or name,
        "connection": {"name": connection},
        "columns": columns or [],
    }
    body.update(extra)
    return TmlDocument(kind="table", body=body, guid=None)


def _column(name, db_column_name=None, data_type="VARCHAR"):
    return {
        "name": name,
        "db_column_name": db_column_name or name,
        "db_column_properties": {"data_type": data_type},
    }


def _sql_view(name, sql_query="SELECT 1", columns=None, connection="My Snowflake", **extra):
    body = {
        "name": name,
        "sql_query": sql_query,
        "connection": {"name": connection},
        "sql_view_columns": columns or [],
    }
    body.update(extra)
    return TmlDocument(kind="sql_view", body=body, guid=None)


def _sql_view_column(name, sql_output_column=None, data_type="VARCHAR"):
    return {
        "name": name,
        "sql_output_column": sql_output_column or name,
        "db_column_properties": {"data_type": data_type},
    }


def _attribute(name, column_id):
    return {"name": name, "column_id": column_id, "properties": {"column_type": "ATTRIBUTE"}}


def _model(name="Sales Analytics", model_tables=None, columns=None, formulas=None, **extra):
    body: dict = {"name": name, "model_tables": model_tables or [], "columns": columns or []}
    if formulas is not None:
        body["formulas"] = formulas
    body.update(extra)
    return TmlDocument(kind="model", body=body, guid=None)


def _document_set(model_doc, *table_docs):
    return DocumentSet(model=model_doc, tables=tuple(table_docs))


def _own_stash(obj):
    """The parsed THOUGHTSPOT custom_extensions payload on `obj`, or None."""
    for entry in obj.get("custom_extensions") or []:
        if entry["vendor_name"] == "THOUGHTSPOT":
            return json.loads(entry["data"])
    return None


class TestMinimalConversion:
    def test_a_minimal_document_set_converts(self):
        # The mapping document's *Worked shape*: one dataset, one attribute,
        # one metric.
        orders = _table(
            "ORDERS",
            columns=[
                _column("Order Date", "O_ORDERDATE", "DATE"),
                _column("Amount", "O_TOTALPRICE", "DOUBLE"),
            ],
        )
        model = _model(
            name="Sales Analytics",
            model_tables=[{"name": "ORDERS"}],
            columns=[
                _attribute("Order Date", "ORDERS::Order Date"),
                _attribute("Amount", "ORDERS::Amount"),
                {"name": "total_revenue", "formula_id": "formula_total_revenue",
                 "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            ],
            formulas=[{"id": "formula_total_revenue", "name": "total_revenue",
                       "expr": "sum ( [ORDERS::Amount] )"}],
        )

        result = convert(_document_set(model, orders))

        assert isinstance(result, OssieConversion)
        document = result.model
        assert document["version"] == "0.2.0.dev0"
        semantic_model = document["semantic_model"][0]
        assert semantic_model["name"] == "sales_analytics"

        assert len(semantic_model["datasets"]) == 1
        dataset = semantic_model["datasets"][0]
        assert dataset["name"] == "ORDERS"
        assert dataset["source"] == "SALES.PUBLIC.ORDERS"
        assert {f["name"] for f in dataset["fields"]} == {"order_date", "amount"}
        assert "primary_key" not in dataset
        assert "relationships" not in semantic_model

        assert len(semantic_model["metrics"]) == 1
        metric = semantic_model["metrics"][0]
        assert metric["name"] == "total_revenue"
        dialects = {d["dialect"]: d["expression"] for d in metric["expression"]["dialects"]}
        assert dialects["THOUGHTSPOT"] == "sum ( [ORDERS::Amount] )"

    def test_dataset_source_is_db_schema_table(self):
        orders = _table("ORDERS", db="SALES", schema="PUBLIC", db_table="ORDERS_FACT")
        model = _model(model_tables=[{"name": "ORDERS"}])
        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        assert dataset["source"] == "SALES.PUBLIC.ORDERS_FACT"


class TestAliasPrefix:
    def test_an_alias_is_used_for_the_reference_prefix_when_present(self):
        # model_tables[].alias overrides name in column_id prefixes; getting
        # this wrong breaks every reference in an aliased model. A plain,
        # unaliased table sits alongside it to prove that case still works.
        ship_to = _table("ADDRESSES", columns=[_column("City", "CITY", "VARCHAR")])
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[
                {"name": "ADDRESSES", "alias": "ShippingAddress"},
                {"name": "ORDERS"},
            ],
            columns=[
                _attribute("Ship City", "ShippingAddress::City"),
                _attribute("Amount", "ORDERS::Amount"),
                {"name": "city_count", "formula_id": "formula_city_count",
                 "properties": {"column_type": "MEASURE", "aggregation": "COUNT_DISTINCT"}},
            ],
            formulas=[{"id": "formula_city_count", "name": "city_count",
                       "expr": "[ShippingAddress::City]"}],
        )

        result = convert(_document_set(model, ship_to, orders))
        semantic_model = result.model["semantic_model"][0]
        datasets = {d["name"]: d for d in semantic_model["datasets"]}

        assert set(datasets) == {"ShippingAddress", "ORDERS"}

        aliased = datasets["ShippingAddress"]
        assert aliased["fields"][0]["name"] == "ship_city"
        assert aliased["fields"][0]["datatype"] == "String"  # table_lookup used the alias too
        aliased_stash = _own_stash(aliased)
        assert aliased_stash[DATASET_STASH_ALIAS] == "ShippingAddress"
        assert aliased_stash[DATASET_STASH_TABLE_NAME] == "ADDRESSES"

        # The unaliased case is unaffected: dataset name is the plain table
        # name, and there is no alias/table_name in its stash.
        plain = datasets["ORDERS"]
        assert plain["fields"][0]["name"] == "amount"
        plain_stash = _own_stash(plain) or {}
        assert DATASET_STASH_ALIAS not in plain_stash

        # The metric's expression resolves through the ALIAS, not "ADDRESSES" --
        # proof `resolve()` keys off the alias end to end, not just the
        # column_id -> field mapping. The dataset qualifier preserves the
        # alias's exact case (Dataset-level mapping's "name...exactly,
        # case-sensitive" rule); the column itself is the WAREHOUSE name
        # ("CITY", from _column("City", "CITY", ...)), not the Ossie
        # field's own display-derived identifier ("ship_city") -- a bare
        # reference's portable sibling names the physical column, per the
        # mapping document.
        metric = semantic_model["metrics"][0]
        dialects = {d["dialect"]: d["expression"] for d in metric["expression"]["dialects"]}
        assert dialects["ANSI_SQL"] == "COUNT(DISTINCT ShippingAddress.CITY)"

        # No unresolved-reference issue should have fired for the aliased column.
        assert not any(i["code"] == "TS-EXPR-UNRESOLVED" for i in result.issues.as_dicts())


class TestKeyDerivation:
    def test_an_equality_join_derives_a_primary_key(self):
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "CUSTOMERS",
                    "on": "[ORDERS::Customer Id] = [CUSTOMERS::Id]",
                    "type": "INNER",
                    "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "CUSTOMERS"},
            ],
        )

        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]
        customers_ds = next(d for d in semantic_model["datasets"] if d["name"] == "CUSTOMERS")

        assert customers_ds["primary_key"] == ["Id"]
        assert customers_ds["unique_keys"] == [["Id"]]

        rel = semantic_model["relationships"][0]
        assert rel["from"] == "ORDERS"
        assert rel["to"] == "CUSTOMERS"
        assert rel["from_columns"] == ["Customer Id"]
        assert rel["to_columns"] == ["Id"]
        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_TYPE] == "INNER"
        assert rel_stash[RELATIONSHIP_STASH_CARDINALITY] == "MANY_TO_ONE"
        assert rel_stash[RELATIONSHIP_STASH_JOIN_SHAPE] == "inline"
        assert RELATIONSHIP_STASH_ON_EXPRESSION not in rel_stash

    def test_a_non_equality_join_derives_no_key_and_stashes_the_condition(self):
        # A residual-predicate (as-of) join is to-one only
        # because of the narrowing -- its equality columns alone are not
        # unique, so no key is derived and no relationship is emitted at all
        # (the condition has zero equality pairs).
        rates = _table("FX_RATES", columns=[_column("Ccy", "CCY"), _column("Effective Date", "EFFECTIVE_DATE", "DATE")])
        orders = _table("ORDERS", columns=[_column("Order Date", "ORDER_DATE", "DATE")])
        on_expr = "[ORDERS::Order Date] >= [FX_RATES::Effective Date]"
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "FX_RATES", "on": on_expr,
                    "type": "INNER", "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "FX_RATES"},
            ],
        )

        result = convert(_document_set(model, orders, rates))
        semantic_model = result.model["semantic_model"][0]
        fx_ds = next(d for d in semantic_model["datasets"] if d["name"] == "FX_RATES")

        assert "primary_key" not in fx_ds
        assert "unique_keys" not in fx_ds
        assert "relationships" not in semantic_model

        model_stash = _own_stash(semantic_model)
        unrep = model_stash[MODEL_STASH_UNREPRESENTABLE_JOINS][0]
        assert unrep["from"] == "ORDERS"
        assert unrep["to"] == "FX_RATES"
        assert unrep[RELATIONSHIP_STASH_ON_EXPRESSION] == on_expr
        assert any(
            i["code"] == "TS-JOIN-UNREPRESENTABLE" and "FX_RATES" in i["message"]
            for i in result.issues.as_dicts()
        )

    def test_a_composite_equality_join_derives_a_composite_key(self):
        customers = _table("CUSTOMERS", columns=[_column("Region"), _column("Id", "ID", "INT64")])
        orders = _table("ORDERS", columns=[_column("Region"), _column("Customer Id", "CUSTOMER_ID", "INT64")])
        on_expr = "[ORDERS::Region] = [CUSTOMERS::Region] and [ORDERS::Customer Id] = [CUSTOMERS::Id]"
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "CUSTOMERS", "on": on_expr,
                    "type": "INNER", "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "CUSTOMERS"},
            ],
        )

        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]
        customers_ds = next(d for d in semantic_model["datasets"] if d["name"] == "CUSTOMERS")

        assert customers_ds["primary_key"] == ["Region", "Id"]
        rel = semantic_model["relationships"][0]
        assert rel["from_columns"] == ["Region", "Customer Id"]
        assert rel["to_columns"] == ["Region", "Id"]


class TestOneToManyEndpointSwap:
    """core-spec/spec.yaml requires a Relationship's `from` to name the many
    side and `to` the one side, but TML's own `from`/`to` -- the
    model_tables[] entry a join is declared under, and its `with` target --
    do not encode which side is which; `cardinality` does. A `ONE_TO_MANY`
    join is the one case where TML's `from` names the one side and `to`
    names the many side: the wrong way around for Ossie's spec, so its
    emitted endpoints are swapped to compensate. `MANY_TO_ONE`/`ONE_TO_ONE`
    are already oriented correctly and must be left alone.
    """

    def test_one_to_many_swaps_the_relationships_endpoints(self):
        # A customer has many orders: TML declares this from the "one" side
        # (CUSTOMERS), naming ORDERS as `with` and ONE_TO_MANY as the
        # cardinality -- so from=CUSTOMERS is the one side and to=ORDERS is
        # the many side, backwards for Ossie's spec.
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "CUSTOMERS", "joins": [{
                    "with": "ORDERS",
                    "on": "[CUSTOMERS::Id] = [ORDERS::Customer Id]",
                    "type": "INNER",
                    "cardinality": "ONE_TO_MANY",
                }]},
                {"name": "ORDERS"},
            ],
        )

        result = convert(_document_set(model, customers, orders))
        semantic_model = result.model["semantic_model"][0]

        rel = semantic_model["relationships"][0]
        # Swapped: the many side (ORDERS) is `from`, the one side
        # (CUSTOMERS) is `to` -- the reverse of how the join is declared.
        assert rel["from"] == "ORDERS"
        assert rel["to"] == "CUSTOMERS"
        assert rel["from_columns"] == ["Customer Id"]
        assert rel["to_columns"] == ["Id"]
        # The inline join's synthesized name reflects the emitted (swapped)
        # from/to, not the TML declaration order.
        assert rel["name"] == "ORDERS_to_CUSTOMERS"

        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_CARDINALITY] == "ONE_TO_MANY"
        assert rel_stash[RELATIONSHIP_STASH_ENDPOINTS_SWAPPED] is True
        assert rel_stash[RELATIONSHIP_STASH_ENDPOINTS_SWAPPED_WITNESS] == [
            "ORDERS", "CUSTOMERS", ["Customer Id"], ["Id"],
        ]

        # Key derivation follows the swap: the key belongs to the one side
        # (CUSTOMERS), which is now `to`.
        customers_ds = next(d for d in semantic_model["datasets"] if d["name"] == "CUSTOMERS")
        assert customers_ds["primary_key"] == ["Id"]
        assert customers_ds["unique_keys"] == [["Id"]]
        orders_ds = next(d for d in semantic_model["datasets"] if d["name"] == "ORDERS")
        assert "primary_key" not in orders_ds

    def test_many_to_one_is_not_swapped(self):
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "CUSTOMERS",
                    "on": "[ORDERS::Customer Id] = [CUSTOMERS::Id]",
                    "type": "INNER",
                    "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "CUSTOMERS"},
            ],
        )

        result = convert(_document_set(model, orders, customers))
        rel = result.model["semantic_model"][0]["relationships"][0]

        assert rel["from"] == "ORDERS"
        assert rel["to"] == "CUSTOMERS"
        assert rel["from_columns"] == ["Customer Id"]
        assert rel["to_columns"] == ["Id"]
        assert rel["name"] == "ORDERS_to_CUSTOMERS"

        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_CARDINALITY] == "MANY_TO_ONE"
        assert RELATIONSHIP_STASH_ENDPOINTS_SWAPPED not in rel_stash
        assert RELATIONSHIP_STASH_ENDPOINTS_SWAPPED_WITNESS not in rel_stash

    def test_one_to_one_is_not_swapped(self):
        people = _table("PEOPLE", columns=[_column("Id", "ID", "INT64")])
        profiles = _table("PROFILES", columns=[_column("Person Id", "PERSON_ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "PROFILES", "joins": [{
                    "with": "PEOPLE",
                    "on": "[PROFILES::Person Id] = [PEOPLE::Id]",
                    "type": "INNER",
                    "cardinality": "ONE_TO_ONE",
                }]},
                {"name": "PEOPLE"},
            ],
        )

        result = convert(_document_set(model, profiles, people))
        rel = result.model["semantic_model"][0]["relationships"][0]

        assert rel["from"] == "PROFILES"
        assert rel["to"] == "PEOPLE"
        assert rel["from_columns"] == ["Person Id"]
        assert rel["to_columns"] == ["Id"]

        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_CARDINALITY] == "ONE_TO_ONE"
        assert RELATIONSHIP_STASH_ENDPOINTS_SWAPPED not in rel_stash

    def test_a_referencing_shaped_one_to_many_join_keeps_its_own_name(self):
        # The hybrid shape (referencing_join plus an inline cardinality
        # override): the name comes from the Table's own joins_with[] entry,
        # not from/to dataset names, and is untouched by the endpoint swap.
        customers = _table(
            "CUSTOMERS",
            columns=[_column("Id", "ID", "INT64")],
            joins_with=[{
                "name": "customers_to_orders",
                "destination": {"name": "ORDERS"},
                "on": "[CUSTOMERS::Id] = [ORDERS::Customer Id]",
                "type": "INNER",
                "cardinality": "MANY_TO_ONE",
            }],
        )
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "CUSTOMERS", "joins": [{
                    "referencing_join": "customers_to_orders", "cardinality": "ONE_TO_MANY",
                }]},
                {"name": "ORDERS"},
            ],
        )

        result = convert(_document_set(model, customers, orders))
        rel = result.model["semantic_model"][0]["relationships"][0]

        assert rel["name"] == "customers_to_orders"
        assert rel["from"] == "ORDERS"
        assert rel["to"] == "CUSTOMERS"
        assert rel["from_columns"] == ["Customer Id"]
        assert rel["to_columns"] == ["Id"]

        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_CARDINALITY] == "ONE_TO_MANY"
        assert rel_stash[RELATIONSHIP_STASH_ENDPOINTS_SWAPPED] is True
        assert rel_stash[RELATIONSHIP_STASH_JOIN_SHAPE] == "referencing_with_inline_attrs"
        assert rel_stash[RELATIONSHIP_STASH_REFERENCING_JOIN] == "customers_to_orders"


class TestUnattributedFormulas:
    def test_a_multi_dataset_formula_is_not_attributed_and_raises_an_issue(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        customers = _table("CUSTOMERS", columns=[_column("Discount", "DISCOUNT", "DOUBLE")])
        expr = "[ORDERS::Amount] - [CUSTOMERS::Discount]"
        model = _model(
            model_tables=[{"name": "ORDERS"}, {"name": "CUSTOMERS"}],
            columns=[
                _attribute("Amount", "ORDERS::Amount"),
                _attribute("Discount", "CUSTOMERS::Discount"),
                {"name": "Net Amount", "formula_id": "formula_net",
                 "properties": {"column_type": "ATTRIBUTE"}},
            ],
            formulas=[{"id": "formula_net", "name": "Net Amount", "expr": expr}],
        )

        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]

        field_names = {f["name"] for d in semantic_model["datasets"] for f in d.get("fields", [])}
        assert "net_amount" not in field_names

        model_stash = _own_stash(semantic_model)
        unattributed = model_stash[MODEL_STASH_UNATTRIBUTED_FORMULAS]
        assert len(unattributed) == 1
        assert unattributed[0]["name"] == "Net Amount"
        assert unattributed[0]["expr"] == expr

        assert any(i["code"] == "TS-FIELD-UNATTRIBUTED" for i in result.issues.as_dicts())


class TestStashProtocol:
    def test_other_vendors_custom_extensions_pass_through_untouched(self):
        # `convert()`'s own objects must stay compatible with a further
        # write_stash call from another vendor's tooling -- exercised on a
        # dataset dict `convert()` actually produced.
        orders = _table("ORDERS")
        model = _model(model_tables=[{"name": "ORDERS"}])
        result = convert(_document_set(model, orders))

        dataset = result.model["semantic_model"][0]["datasets"][0]
        dataset.setdefault("custom_extensions", []).append(
            {"vendor_name": "SNOWFLAKE", "data": '{"x": 1}'}
        )
        merged = stash.write_stash(dataset, {"extra": "value"})
        vendor_names = {e["vendor_name"] for e in merged["custom_extensions"]}
        assert vendor_names == {"THOUGHTSPOT", "SNOWFLAKE"}
        foreign = next(e for e in merged["custom_extensions"] if e["vendor_name"] == "SNOWFLAKE")
        assert foreign == {"vendor_name": "SNOWFLAKE", "data": '{"x": 1}'}

    def test_no_guid_obj_id_or_fqn_appears_anywhere_in_the_output(self):
        # Nested guids on the model_tables[] entry (fqn) and the model
        # document root (guid) are present in the source and must never leak
        # into the output -- not only into the stash, but anywhere at all.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        model_body = {
            "name": "Sales",
            "model_tables": [
                {"name": "ORDERS", "fqn": "abc-123-fqn", "obj_id": "obj-1",
                 "joins": [{"with": "CUSTOMERS",
                            "on": "[ORDERS::Amount] = [CUSTOMERS::Id]",
                            "cardinality": "MANY_TO_ONE"}]},
                {"name": "CUSTOMERS", "fqn": "def-456-fqn"},
            ],
            "columns": [_attribute("Amount", "ORDERS::Amount")],
        }
        model = TmlDocument(kind="model", body=model_body, guid="model-guid-1")

        result = convert(_document_set(model, orders, customers))
        serialised = json.dumps(result.model)
        for forbidden in ("guid", "obj_id", "fqn"):
            assert forbidden not in serialised, forbidden

    def test_an_empty_payload_writes_no_stash_entry(self):
        # A model with an already-normalised name and no ThoughtSpot-only
        # model-scope properties stays clean at model scope.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")], connection="Snowflake")
        model = _model(
            name="sales",  # already a valid identifier -- normalise() is a no-op
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("Amount", "ORDERS::Amount")],
        )
        result = convert(_document_set(model, orders))
        semantic_model = result.model["semantic_model"][0]
        assert "custom_extensions" not in semantic_model


class TestSchemaValidation:
    def test_the_output_validates_against_the_upstream_schema(self):
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = Path(__file__).resolve().parents[3] / "core-spec" / "ossie-schema.json"
        with open(schema_path) as fh:
            schema = json.load(fh)

        orders = _table(
            "ORDERS",
            columns=[_column("Amount", "AMOUNT", "DOUBLE"), _column("Order Date", "ORDER_DATE", "DATE")],
        )
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "CUSTOMERS",
                    "on": "[ORDERS::Amount] = [CUSTOMERS::Id]",
                    "type": "INNER",
                    "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "CUSTOMERS"},
            ],
            columns=[
                _attribute("Amount", "ORDERS::Amount"),
                _attribute("Order Date", "ORDERS::Order Date"),
                {"name": "total_revenue", "formula_id": "formula_rev",
                 "properties": {"column_type": "MEASURE", "aggregation": "SUM"}},
            ],
            formulas=[{"id": "formula_rev", "name": "total_revenue",
                       "expr": "sum ( [ORDERS::Amount] )"}],
        )

        result = convert(_document_set(model, orders, customers))
        jsonschema.Draft202012Validator(schema).validate(result.model)


class TestOwnChoice:
    """Two cases the required list doesn't name, chosen because they attack
    code this task adds that nothing else exercises."""

    def test_a_referencing_join_resolves_via_the_tables_joins_with(self):
        # The OTHER TML join shape (Table joins_with[] + Model referencing_join)
        # is real and documented but untouched by every other required test,
        # which all use inline joins. If _convert_join's referencing-join
        # branch has a bug, nothing else here would catch it.
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        orders = _table(
            "ORDERS",
            columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")],
            joins_with=[{
                "name": "orders_to_customers",
                "destination": {"name": "CUSTOMERS"},
                "on": "[ORDERS::Customer Id] = [CUSTOMERS::Id]",
                "type": "INNER",
                "cardinality": "MANY_TO_ONE",
            }],
        )
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{"referencing_join": "orders_to_customers"}]},
                {"name": "CUSTOMERS"},
            ],
        )

        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]

        rel = semantic_model["relationships"][0]
        assert rel["name"] == "orders_to_customers"
        assert rel["from"] == "ORDERS"
        assert rel["to"] == "CUSTOMERS"
        assert rel["from_columns"] == ["Customer Id"]
        assert rel["to_columns"] == ["Id"]
        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_JOIN_SHAPE] == "referencing"
        assert rel_stash[RELATIONSHIP_STASH_REFERENCING_JOIN] == "orders_to_customers"
        assert rel_stash[RELATIONSHIP_STASH_TYPE] == "INNER"
        assert rel_stash[RELATIONSHIP_STASH_CARDINALITY] == "MANY_TO_ONE"

        customers_ds = next(d for d in semantic_model["datasets"] if d["name"] == "CUSTOMERS")
        assert customers_ds["primary_key"] == ["Id"]

    def test_a_malformed_join_condition_is_caught_and_the_conversion_continues(self):
        # Lesson carried into this task: a malformed reference must not abort
        # the whole model. This is the join-condition version of that rule --
        # untested anywhere else, since every other join test uses a clean
        # condition. A triple-colon reference is ambiguous per
        # identifiers.split_column_ref and raises inside _parse_join_condition.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        bad_condition = "[ORDERS:::Bad Ref] = [CUSTOMERS::Id]"
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{
                    "with": "CUSTOMERS", "on": bad_condition, "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "CUSTOMERS"},
            ],
            columns=[_attribute("Amount", "ORDERS::Amount")],
        )

        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]

        # The rest of the model still converts.
        assert len(semantic_model["datasets"]) == 2
        orders_ds = next(d for d in semantic_model["datasets"] if d["name"] == "ORDERS")
        assert orders_ds["fields"][0]["name"] == "amount"
        assert "relationships" not in semantic_model

        model_stash = _own_stash(semantic_model)
        unrep = model_stash[MODEL_STASH_UNREPRESENTABLE_JOINS][0]
        assert unrep[RELATIONSHIP_STASH_ON_EXPRESSION] == bad_condition
        assert any(i["code"] == "TS-JOIN-MALFORMED" for i in result.issues.as_dicts())


class TestUnconsumedColumnProperties:
    """Neither convert_field nor convert_metric preserves a ThoughtSpot-only
    column property (`index_type`, `value_casing`, ...): they never returned
    a fragment for the assembler to merge, so it vanished with no issue. The
    assembler now stashes the complement of what the converter actually
    reads, rather than an enumeration of known ThoughtSpot-only names."""

    # db_column_name matches the display name exactly, so these tests'
    # "no extra properties" premises are not disturbed by the separate
    # db_column_name-preservation stash (TestPhysicalColumnStash covers
    # that dimension on its own fixtures).
    ORDERS = _table("ORDERS", columns=[_column("Amount", "Amount", "DOUBLE")])

    def _convert_one(self, properties):
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[{"name": "Amount", "column_id": "ORDERS::Amount", "properties": properties}],
        )
        return convert(_document_set(model, self.ORDERS))

    def test_thoughtspot_only_properties_round_trip_into_column_properties(self):
        result = self._convert_one(
            {"column_type": "ATTRIBUTE", "index_type": "DONT_INDEX", "value_casing": "UPPER"}
        )
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert _own_stash(field)[FIELD_STASH_COLUMN_PROPERTIES] == {
            "index_type": "DONT_INDEX", "value_casing": "UPPER",
        }

    def test_a_metric_with_only_consumed_properties_gets_no_column_properties_key(self):
        result = self._convert_one({"column_type": "MEASURE", "aggregation": "SUM"})
        metric = result.model["semantic_model"][0]["metrics"][0]
        stashed = _own_stash(metric) or {}
        assert FIELD_STASH_COLUMN_PROPERTIES not in stashed

    def test_a_column_with_no_extra_properties_gets_no_extension_entry(self):
        result = self._convert_one({"column_type": "ATTRIBUTE"})
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert "custom_extensions" not in field

    def test_an_unknown_invented_property_name_is_preserved(self):
        # The fail-closed property itself: a name this converter has never
        # heard of must still survive, because the rule is "everything not
        # consumed", not "everything on a known list".
        result = self._convert_one(
            {"column_type": "ATTRIBUTE", "a_property_ossie_thoughtspot_has_never_seen": 42}
        )
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert _own_stash(field)[FIELD_STASH_COLUMN_PROPERTIES] == {
            "a_property_ossie_thoughtspot_has_never_seen": 42
        }

    def test_the_metric_side_behaves_the_same_as_the_field_side(self):
        result = self._convert_one(
            {"column_type": "MEASURE", "aggregation": "SUM", "index_type": "DONT_INDEX"}
        )
        metric = result.model["semantic_model"][0]["metrics"][0]
        assert _own_stash(metric)[FIELD_STASH_COLUMN_PROPERTIES] == {"index_type": "DONT_INDEX"}

    def test_identity_shaped_content_nested_in_a_property_value_is_dropped_not_stashed(self):
        # Found while re-verifying the identity guard for this fix: the complement copies an
        # unconsumed property's *value* wholesale, and a real, documented
        # ThoughtSpot shape (geo_config naming a custom map) carries a GUID
        # nested inside that value -- not as a top-level payload key, which
        # is all stash.write_stash's own guard checks. Dropped, not stashed,
        # with an issue -- silently widening what "column_properties" leaks
        # would be worse than the original gap.
        result = self._convert_one({
            "column_type": "ATTRIBUTE",
            "index_type": "DONT_INDEX",
            "geo_config": {"custom_file_guid": "map-guid-123", "geometryType": "polygon"},
        })
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert _own_stash(field)[FIELD_STASH_COLUMN_PROPERTIES] == {"index_type": "DONT_INDEX"}
        serialised = json.dumps(result.model)
        assert "guid" not in serialised
        assert any(i["code"] == "TS-PROPERTY-IDENTITY-DROPPED" for i in result.issues.as_dicts())


class TestUnknownRelationshipTarget:
    def test_a_relationship_targeting_an_unknown_dataset_is_dropped_and_logged(self):
        # Critical: only the FROM side of a join used to be checked against
        # the datasets this model actually built. CUSTOMERS is referenced by
        # the join but has no Table document, so its dataset never builds --
        # emitting a relationship pointing at it would produce a document
        # upstream's own validator rejects outright.
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        model = _model(
            model_tables=[{"name": "ORDERS", "joins": [{
                "with": "CUSTOMERS",
                "on": "[ORDERS::Customer Id] = [CUSTOMERS::Id]",
                "cardinality": "MANY_TO_ONE",
            }]}],
            columns=[_attribute("Customer Id", "ORDERS::Customer Id")],
        )

        result = convert(_document_set(model, orders))
        semantic_model = result.model["semantic_model"][0]

        assert "relationships" not in semantic_model
        # The rest of the model -- the one dataset that DID build -- is
        # still useful rather than being discarded along with the bad join.
        assert semantic_model["datasets"][0]["fields"][0]["name"] == "customer_id"
        assert any(
            i["code"] == "TS-JOIN-UNKNOWN-TARGET" and "CUSTOMERS" in i["message"]
            for i in result.issues.as_dicts()
        )


class TestUnsurfacedColumns:
    def test_a_physical_column_the_model_does_not_surface_is_stashed_verbatim(self):
        orders = _table("ORDERS", columns=[
            _column("Amount", "AMOUNT", "DOUBLE"),
            _column("Internal Flag", "INTERNAL_FLAG", "BOOLEAN"),
        ])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("Amount", "ORDERS::Amount")],
        )

        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]

        unsurfaced = _own_stash(dataset)[DATASET_STASH_UNSURFACED_COLUMNS]
        assert len(unsurfaced) == 1
        assert unsurfaced[0]["name"] == "Internal Flag"
        assert unsurfaced[0]["db_column_name"] == "INTERNAL_FLAG"

    def test_a_column_surfaced_only_as_a_measure_is_stashed_too(self):
        # column_aggregation-shape metrics surface their physical column via
        # column_id, but a Metric has no column_id field on the Ossie side
        # at all -- it carries only the composed THOUGHTSPOT-dialect
        # expression, bracket reference and all. An earlier revision treated
        # this column as "surfaced enough" to skip unsurfaced_columns, on
        # the reasoning that it is still part of the semantic model. True,
        # but nothing else preserves its definition, so the reverse
        # direction regenerated a Table missing it while the metric's own
        # formula still referenced it -- a dangling column reference,
        # caught only by round-tripping a real document through both public
        # entry points. This column is now stashed exactly like one
        # referenced by nothing at all, redundant with the metric's own
        # expression but making the Table document regenerable on its own.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[{"name": "Total", "column_id": "ORDERS::Amount",
                      "properties": {"column_type": "MEASURE", "aggregation": "SUM"}}],
        )

        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        stashed = _own_stash(dataset) or {}
        unsurfaced = stashed.get(DATASET_STASH_UNSURFACED_COLUMNS) or []
        assert [c["name"] for c in unsurfaced] == ["Amount"]

    def test_a_dataset_with_no_unsurfaced_columns_gets_no_such_key(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("Amount", "ORDERS::Amount")],
        )
        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        stashed = _own_stash(dataset) or {}
        assert DATASET_STASH_UNSURFACED_COLUMNS not in stashed

    def test_unsurfaced_columns_populates_the_dataset_stash_on_its_own(self):
        # A dataset's stash always carries at least tml_object, so the
        # empty-payload guarantee is exercised at the model scope
        # (test_an_empty_payload_writes_no_stash_entry), not here -- this
        # confirms unsurfaced_columns itself lands correctly when nothing
        # else about the column is surfaced at all.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[])
        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        stashed = _own_stash(dataset)
        assert stashed is not None
        assert stashed[DATASET_STASH_UNSURFACED_COLUMNS][0]["name"] == "Amount"


class TestModelScopeIdentityIsCaughtNotFatal:
    """The boundary guard (stash.write_stash) still raises -- that is what
    makes it impossible to bypass -- but the assembly catches it, drops the
    one contaminated stash field, logs why, and keeps converting everything
    else. A single stray identity value in an otherwise-fine model must not
    turn the whole conversion into a traceback."""

    def _model_with(self, **model_scope_fields):
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("Amount", "ORDERS::Amount")],
            **model_scope_fields,
        )
        return orders, model

    def test_a_guid_nested_in_parameters_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            parameters=[{"name": "P", "default_value": {"obj_id": "p-1"}}]
        )
        result = convert(_document_set(model, orders))
        semantic_model = result.model["semantic_model"][0]
        assert semantic_model["datasets"][0]["fields"][0]["name"] == "amount"
        stashed = _own_stash(semantic_model) or {}
        assert MODEL_STASH_PARAMETERS not in stashed
        assert "obj_id" not in json.dumps(result.model)
        assert any(i["code"] == "TS-STASH-IDENTITY-DROPPED" for i in result.issues.as_dicts())

    def test_a_guid_nested_in_filters_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            filters=[{"column": "Region", "values": [{"nested": {"fqn": "f-1"}}]}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_FILTERS not in stashed
        assert "fqn" not in json.dumps(result.model)

    def test_a_guid_nested_in_column_groups_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            column_groups=[{"name": "Sales", "meta": {"guid": "g-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_COLUMN_GROUPS not in stashed
        assert "guid" not in json.dumps(result.model)

    def test_a_guid_nested_in_lesson_plans_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            lesson_plans=[{"lesson_id": 0, "extra": {"obj_id": "l-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_LESSON_PLANS not in stashed
        assert "obj_id" not in json.dumps(result.model)

    def test_a_guid_nested_in_action_object_associations_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            action_object_associations=[{"action_name": "A", "context": {"fqn": "a-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_ACTION_OBJECT_ASSOCIATIONS not in stashed
        assert "fqn" not in json.dumps(result.model)

    def test_a_guid_nested_in_constraints_is_dropped_not_fatal(self):
        orders, model = self._model_with(constraints={"rolling": {"window": {"guid": "c-1"}}})
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_CONSTRAINTS not in stashed
        assert "guid" not in json.dumps(result.model)

    def test_a_guid_nested_in_model_joins_with_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            joins_with=[{"name": "j", "destination": {"fqn": "j-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_MODEL_JOINS_WITH not in stashed
        assert "fqn" not in json.dumps(result.model)

    def test_other_model_scope_fields_survive_when_only_one_is_contaminated(self):
        # Dropping the one bad key must not take the rest of the model
        # stash down with it.
        orders, model = self._model_with(
            parameters=[{"name": "P", "default_value": {"obj_id": "p-1"}}],
            filters=[{"column": "Region", "values": ["US"]}],
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert MODEL_STASH_PARAMETERS not in stashed
        assert stashed[MODEL_STASH_FILTERS] == [{"column": "Region", "values": ["US"]}]


class TestUnnormalisableNamesAreCaughtNotFatal:
    """`identifiers.normalise` raises when a display name has no ASCII
    alphanumerics for it to fold onto (a CJK-only name, a punctuation-only
    one). Three scopes can hit it: the model's own top-level name, a field,
    and a metric. All three must degrade -- fall back to a usable
    identifier, report it, and continue -- rather than take the whole
    conversion down, or the column, over one unfoldable name. (Before
    `_field_or_metric_identifier` existed, a field or metric hitting this
    was dropped entirely and misreported as a malformed column *reference*
    -- see `test_a_column_ref_and_an_unnormalisable_name_report_different_codes`
    for the two now being told apart.)
    """

    def test_a_model_name_with_no_ascii_alphanumerics_falls_back_and_is_reported(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            name="北京市",  # CJK-only; NFKD folds none of it to ASCII
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("Amount", "ORDERS::Amount")],
        )
        result = convert(_document_set(model, orders))
        semantic_model = result.model["semantic_model"][0]
        assert semantic_model["name"] == "model"
        assert any(i["code"] == "TS-MODEL-NAME-UNNORMALISABLE" for i in result.issues.as_dicts())
        # The rest of the model still converts -- one unfoldable name does
        # not take the whole document down.
        assert semantic_model["datasets"][0]["fields"][0]["name"] == "amount"

    def test_an_attribute_columns_unnormalisable_name_falls_back_to_the_warehouse_column_name(self):
        # Also exercises `_index_attribute_columns` (Phase 2, which runs
        # over every ATTRIBUTE column before Phase 3 starts): before this
        # fix, Phase 2 silently excluded a column like this one from the
        # cross-reference gate on the theory that convert_field would drop
        # the field too -- true then, a correctness bug once convert_field
        # grew this fallback. There is no cross-reference in this fixture,
        # but the field surviving Phase 3 at all already proves Phase 2 did
        # not exclude it.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("!!!", "ORDERS::Amount")],
        )
        result = convert(_document_set(model, orders))
        fields = result.model["semantic_model"][0]["datasets"][0]["fields"]
        assert len(fields) == 1
        field = fields[0]
        # The physical column's own warehouse name is the fallback basis --
        # not a bare placeholder -- see _field_or_metric_identifier.
        assert field["name"] == "amount"
        assert field["label"] == "!!!"  # the exact display name, recoverable via `label` alone
        assert any(i["code"] == "TS-FIELD-NAME-UNNORMALISABLE" for i in result.issues.as_dicts())
        assert not any(i["code"] == "TS-COLUMN-REF-MALFORMED" for i in result.issues.as_dicts())

    def test_two_unnormalisable_fields_with_no_physical_hint_get_distinct_fallback_names(self):
        # Neither formula-backed field has a column_id, so neither has a
        # warehouse column name to fall back on -- both reach the
        # allocator-suffixed placeholder, and must not collide into the same
        # "field" identifier. The physical "Amount" field is only here so
        # `resolve()` has something to attribute the two formulas' shared
        # `[ORDERS::Amount]` reference to.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[
                _attribute("Amount", "ORDERS::Amount"),
                {"name": "顧客", "formula_id": "f1", "properties": {"column_type": "ATTRIBUTE"}},
                {"name": "名前", "formula_id": "f2", "properties": {"column_type": "ATTRIBUTE"}},
            ],
            formulas=[
                {"id": "f1", "name": "顧客", "expr": "[ORDERS::Amount]"},
                {"id": "f2", "name": "名前", "expr": "[ORDERS::Amount]"},
            ],
        )
        result = convert(_document_set(model, orders))
        fields = result.model["semantic_model"][0]["datasets"][0]["fields"]
        assert len(fields) == 3  # "amount" plus the two non-Latin formula fields
        fallback_names = {f["name"] for f in fields if f["label"] in ("顧客", "名前")}
        assert len(fallback_names) == 2  # distinct, not collapsed into one "field"
        assert fallback_names == {"field", "field_2"}

    def test_a_column_ref_and_an_unnormalisable_name_report_different_codes(self):
        # Fix 2: split_column_ref('[顧客::名前]') parses fine -- the reference
        # itself is not malformed -- so a field with a genuinely ambiguous
        # column_id must still report TS-COLUMN-REF-MALFORMED, distinct from
        # TS-FIELD-NAME-UNNORMALISABLE (a valid reference, unfoldable name).
        # Before this fix both funnelled through one shared except-ValueError
        # handler in convert() and were indistinguishable.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[
                _attribute("名前", "ORDERS::Amount"),  # unfoldable name, valid reference
                # A column_id containing a run of "::" is ambiguous --
                # identifiers.split_column_ref refuses to guess.
                _attribute("Ambiguous", "ORDERS:::Nested"),
            ],
        )
        result = convert(_document_set(model, orders))
        codes = {i["code"] for i in result.issues.as_dicts()}
        assert "TS-FIELD-NAME-UNNORMALISABLE" in codes
        assert "TS-COLUMN-REF-MALFORMED" in codes
        fields = result.model["semantic_model"][0]["datasets"][0]["fields"]
        assert len(fields) == 1  # the unfoldable-but-valid field survives
        assert fields[0]["label"] == "名前"


class TestKeyDerivationEdgeCasesCommitted:
    """Edge cases attacked and confirmed by hand during development, now
    committed so the check runs on every future change instead of living
    only in a one-off transcript."""

    def test_a_mixed_equality_and_residual_join_emits_a_weaker_relationship_and_no_key(self):
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64"), _column("Effective Date", "EFFECTIVE_DATE", "DATE")])
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64"), _column("Order Date", "ORDER_DATE", "DATE")])
        on_expr = (
            "[ORDERS::Customer Id] = [CUSTOMERS::Id] and "
            "[ORDERS::Order Date] >= [CUSTOMERS::Effective Date]"
        )
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{"with": "CUSTOMERS", "on": on_expr,
                                               "type": "INNER", "cardinality": "MANY_TO_ONE"}]},
                {"name": "CUSTOMERS"},
            ],
        )
        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]
        customers_ds = next(d for d in semantic_model["datasets"] if d["name"] == "CUSTOMERS")

        assert "primary_key" not in customers_ds
        assert "unique_keys" not in customers_ds

        rel = semantic_model["relationships"][0]
        assert rel["from_columns"] == ["Customer Id"]
        assert rel["to_columns"] == ["Id"]
        rel_stash = _own_stash(rel)
        assert rel_stash[RELATIONSHIP_STASH_ON_EXPRESSION] == on_expr
        assert any(i["code"] == "TS-JOIN-RESIDUAL-PREDICATES" for i in result.issues.as_dicts())
        assert any(i["code"] == "TS_KEY_COVERAGE" for i in result.issues.as_dicts())

    def test_a_self_join_gives_each_alias_its_own_dataset_and_key(self):
        employees = _table("EMPLOYEES", columns=[
            _column("Id", "ID", "INT64"), _column("Manager Id", "MANAGER_ID", "INT64"),
            _column("Name", "NAME", "VARCHAR"),
        ])
        model = _model(
            name="OrgChart",
            model_tables=[
                {"name": "EMPLOYEES", "alias": "Emp", "joins": [{
                    "with": "Mgr", "on": "[Emp::Manager Id] = [Mgr::Id]",
                    "type": "INNER", "cardinality": "MANY_TO_ONE",
                }]},
                {"name": "EMPLOYEES", "alias": "Mgr"},
            ],
            columns=[
                _attribute("Emp Name", "Emp::Name"),
                _attribute("Mgr Name", "Mgr::Name"),
            ],
        )
        result = convert(_document_set(model, employees))
        semantic_model = result.model["semantic_model"][0]
        datasets = {d["name"]: d for d in semantic_model["datasets"]}

        assert set(datasets) == {"Emp", "Mgr"}
        assert datasets["Emp"]["source"] == datasets["Mgr"]["source"] == "SALES.PUBLIC.EMPLOYEES"
        assert datasets["Mgr"]["primary_key"] == ["Id"]
        rel = semantic_model["relationships"][0]
        assert rel["from"] == "Emp"
        assert rel["to"] == "Mgr"
        assert result.issues.as_dicts() == []

    def test_a_top_level_or_in_a_join_condition_is_not_fabricated_into_an_equality_pair(self):
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64"), _column("Legacy Id", "LEGACY_ID", "INT64")])
        orders = _table("ORDERS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        on_expr = "[ORDERS::Customer Id] = [CUSTOMERS::Id] or [ORDERS::Customer Id] = [CUSTOMERS::Legacy Id]"
        model = _model(
            model_tables=[
                {"name": "ORDERS", "joins": [{"with": "CUSTOMERS", "on": on_expr, "cardinality": "MANY_TO_ONE"}]},
                {"name": "CUSTOMERS"},
            ],
        )
        result = convert(_document_set(model, orders, customers))
        semantic_model = result.model["semantic_model"][0]

        # No equality pair could be safely attributed -- the whole "or"
        # expression is one residual, never split into a fabricated pair.
        assert "relationships" not in semantic_model
        model_stash = _own_stash(semantic_model)
        assert model_stash[MODEL_STASH_UNREPRESENTABLE_JOINS][0][RELATIONSHIP_STASH_ON_EXPRESSION] == on_expr

    def test_many_to_many_is_not_key_evidence_through_the_full_pipeline(self):
        customers = _table("CUSTOMERS", columns=[_column("Id", "ID", "INT64")])
        products = _table("PRODUCTS", columns=[_column("Customer Id", "CUSTOMER_ID", "INT64")])
        model = _model(
            model_tables=[
                {"name": "PRODUCTS", "joins": [{
                    "with": "CUSTOMERS", "on": "[PRODUCTS::Customer Id] = [CUSTOMERS::Id]",
                    "type": "INNER", "cardinality": "MANY_TO_MANY",
                }]},
                {"name": "CUSTOMERS"},
            ],
        )
        result = convert(_document_set(model, products, customers))
        semantic_model = result.model["semantic_model"][0]
        customers_ds = next(d for d in semantic_model["datasets"] if d["name"] == "CUSTOMERS")

        assert "primary_key" not in customers_ds
        assert "unique_keys" not in customers_ds
        rel = semantic_model["relationships"][0]
        assert _own_stash(rel)[RELATIONSHIP_STASH_CARDINALITY] == "MANY_TO_MANY"


class TestSqlViewColumns:
    """A SQL View document's columns live under sql_view_columns[], not
    columns[] -- a different key entirely, not a differently-shaped entry
    under the same one. Reading the wrong key silently finds nothing for
    every column of every SQL View: no datatype resolves, and every column
    reads as unsurfaced regardless of whether the Model actually surfaces
    it."""

    def test_a_surfaced_sql_view_column_resolves_its_datatype(self):
        vw = _sql_view("VW", columns=[
            _sql_view_column("CID", "c_id", "INT64"),
        ])
        model = _model(
            model_tables=[{"name": "VW"}],
            columns=[_attribute("Cid", "VW::CID")],
        )
        result = convert(_document_set(model, vw))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert field["datatype"] == "Integer"
        assert result.issues.as_dicts() == []

    def test_an_unsurfaced_sql_view_column_is_stashed_verbatim(self):
        vw = _sql_view("VW", columns=[
            _sql_view_column("CID", "c_id", "INT64"),
            _sql_view_column("Never Surfaced", "x", "VARCHAR"),
        ])
        model = _model(
            model_tables=[{"name": "VW"}],
            columns=[_attribute("Cid", "VW::CID")],
        )
        result = convert(_document_set(model, vw))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        unsurfaced = _own_stash(dataset)[DATASET_STASH_UNSURFACED_COLUMNS]
        assert len(unsurfaced) == 1
        assert unsurfaced[0]["name"] == "Never Surfaced"
        # Verbatim -- the SQL View's own key name, not the datatype-lookup
        # translation this converter builds internally for its own use.
        assert unsurfaced[0]["sql_output_column"] == "x"
        assert "db_column_name" not in unsurfaced[0]

    def test_sql_output_column_differing_from_name_is_stashed(self):
        vw = _sql_view("VW", columns=[
            _sql_view_column("Customer Id", sql_output_column="cust_id_out", data_type="INT64"),
        ])
        model = _model(
            model_tables=[{"name": "VW"}],
            columns=[_attribute("Customer Id", "VW::Customer Id")],
        )
        result = convert(_document_set(model, vw))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        field_name = dataset["fields"][0]["name"]
        assert field_name == "customer_id"
        stashed = _own_stash(dataset)
        assert stashed[DATASET_STASH_SQL_OUTPUT_COLUMNS] == {"customer_id": "cust_id_out"}

    def test_a_mixed_document_set_with_a_table_and_a_sql_view_both_convert(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        vw = _sql_view("VW", columns=[_sql_view_column("CID", "c_id", "INT64")])
        model = _model(
            model_tables=[{"name": "ORDERS"}, {"name": "VW"}],
            columns=[
                _attribute("Amount", "ORDERS::Amount"),
                _attribute("Cid", "VW::CID"),
            ],
        )
        result = convert(_document_set(model, orders, vw))
        datasets = {d["name"]: d for d in result.model["semantic_model"][0]["datasets"]}

        assert datasets["ORDERS"]["source"] == "SALES.PUBLIC.ORDERS"
        assert datasets["ORDERS"]["fields"][0]["datatype"] == "Decimal"

        assert datasets["VW"]["source"] == "SELECT 1"
        assert datasets["VW"]["fields"][0]["datatype"] == "Integer"
        assert _own_stash(datasets["VW"])[DATASET_STASH_TML_OBJECT] == "sql_view"
        assert result.issues.as_dicts() == []


class TestPhysicalColumnReferences:
    """The mapping document is explicit for a bare-identifier field: "the
    identifier is the *physical* column; the display name comes from
    label/name." resolve() used to build the ANSI_SQL sibling from the
    Ossie field's own display-derived identifier instead -- confident,
    well-formed SQL that names a column the warehouse does not have,
    wrong in every model where a display name differs from its physical
    column, which is the normal case in any curated model."""

    def test_a_differing_db_column_name_is_used_in_the_portable_expression(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMT_RAW", "DOUBLE")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Amount", "ORDERS::Amount")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        dialects = {d["dialect"]: d["expression"] for d in field["expression"]["dialects"]}
        assert dialects["ANSI_SQL"] == "ORDERS.AMT_RAW"

    def test_an_equal_db_column_name_still_resolves(self):
        orders = _table("ORDERS", columns=[_column("Amount", "Amount", "DOUBLE")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Amount", "ORDERS::Amount")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        dialects = {d["dialect"]: d["expression"] for d in field["expression"]["dialects"]}
        assert dialects["ANSI_SQL"] == "ORDERS.Amount"

    def test_a_sql_view_reference_uses_sql_output_column_not_db_column_name(self):
        vw = _sql_view("VW", columns=[_sql_view_column("CID", "c_id", "INT64")])
        model = _model(model_tables=[{"name": "VW"}], columns=[_attribute("Cid", "VW::CID")])
        result = convert(_document_set(model, vw))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        dialects = {d["dialect"]: d["expression"] for d in field["expression"]["dialects"]}
        assert dialects["ANSI_SQL"] == "VW.c_id"

    def test_a_computed_field_referencing_a_renamed_column_still_resolves(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMT_RAW", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[
                _attribute("Amount", "ORDERS::Amount"),
                {"name": "Doubled", "formula_id": "formula_doubled",
                 "properties": {"column_type": "ATTRIBUTE"}},
            ],
            formulas=[{"id": "formula_doubled", "name": "Doubled", "expr": "[ORDERS::Amount]"}],
        )
        result = convert(_document_set(model, orders))
        fields = {f["name"]: f for f in result.model["semantic_model"][0]["datasets"][0]["fields"]}
        doubled_dialects = {d["dialect"]: d["expression"] for d in fields["doubled"]["expression"]["dialects"]}
        assert doubled_dialects["ANSI_SQL"] == "ORDERS.AMT_RAW"
        assert result.issues.as_dicts() == []


class TestPhysicalColumnStash:
    """`data_type`'s connection-dependent spelling (BOOL/BOOLEAN,
    DOUBLE/FLOAT) and a Table column's `db_column_name` are both
    unrecoverable by the reverse direction unless the forward direction
    records them -- the datatype map says so explicitly for the former; the
    latter has no documented stash slot at all yet but is just as lost
    without one, since the display name is all a round-tripped bracket
    reference carries."""

    def test_a_non_canonical_boolean_spelling_is_stashed(self):
        orders = _table("ORDERS", columns=[_column("Is Active", "Is Active", "BOOL")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Is Active", "ORDERS::Is Active")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert field["datatype"] == "Boolean"
        assert _own_stash(field)[FIELD_STASH_DATA_TYPE] == "BOOL"

    def test_the_canonical_boolean_spelling_is_not_stashed(self):
        orders = _table("ORDERS", columns=[_column("Is Active", "Is Active", "BOOLEAN")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Is Active", "ORDERS::Is Active")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert field["datatype"] == "Boolean"
        assert "custom_extensions" not in field

    def test_a_float_column_stashes_its_float_spelling(self):
        orders = _table("ORDERS", columns=[_column("Rate", "Rate", "FLOAT")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Rate", "ORDERS::Rate")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert field["datatype"] == "Float"
        assert _own_stash(field)[FIELD_STASH_DATA_TYPE] == "FLOAT"

    def test_a_differing_db_column_name_is_stashed_on_a_table_column(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMT_RAW", "DOUBLE")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Amount", "ORDERS::Amount")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert _own_stash(field)[FIELD_STASH_DB_COLUMN_NAME] == "AMT_RAW"

    def test_an_equal_db_column_name_is_not_stashed(self):
        orders = _table("ORDERS", columns=[_column("Amount", "Amount", "DOUBLE")])
        model = _model(model_tables=[{"name": "ORDERS"}], columns=[_attribute("Amount", "ORDERS::Amount")])
        result = convert(_document_set(model, orders))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert "custom_extensions" not in field

    def test_a_sql_view_column_never_gets_a_db_column_name_stash(self):
        # sql_output_columns (dataset-level) already carries this fact for
        # a SQL View -- a field-level db_column_name would be a redundant
        # second copy of the same information under a different name.
        vw = _sql_view("VW", columns=[_sql_view_column("CID", "c_id", "INT64")])
        model = _model(model_tables=[{"name": "VW"}], columns=[_attribute("Cid", "VW::CID")])
        result = convert(_document_set(model, vw))
        field = result.model["semantic_model"][0]["datasets"][0]["fields"][0]
        assert "custom_extensions" not in field
        dataset_stash = _own_stash(result.model["semantic_model"][0]["datasets"][0])
        assert dataset_stash[DATASET_STASH_SQL_OUTPUT_COLUMNS] == {"cid": "c_id"}

    def test_a_metric_bound_to_a_physical_column_gets_the_same_stash(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMT_RAW", "BOOL")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[{"name": "Amount", "column_id": "ORDERS::Amount",
                      "properties": {"column_type": "MEASURE", "aggregation": "COUNT"}}],
        )
        result = convert(_document_set(model, orders))
        metric = result.model["semantic_model"][0]["metrics"][0]
        stashed = _own_stash(metric)
        assert stashed[FIELD_STASH_DB_COLUMN_NAME] == "AMT_RAW"
