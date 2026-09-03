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
        assert aliased_stash["alias"] == "ShippingAddress"
        assert aliased_stash["table_name"] == "ADDRESSES"

        # The unaliased case is unaffected: dataset name is the plain table
        # name, and there is no alias/table_name in its stash.
        plain = datasets["ORDERS"]
        assert plain["fields"][0]["name"] == "amount"
        plain_stash = _own_stash(plain) or {}
        assert "alias" not in plain_stash

        # The metric's expression resolves through the ALIAS, not "ADDRESSES" --
        # proof `resolve()` keys off the alias end to end, not just the
        # column_id -> field mapping. The dataset-qualified name preserves the
        # alias's exact case, per the Dataset-level mapping's "name...exactly,
        # case-sensitive" rule.
        metric = semantic_model["metrics"][0]
        dialects = {d["dialect"]: d["expression"] for d in metric["expression"]["dialects"]}
        assert dialects["ANSI_SQL"] == "COUNT(DISTINCT ShippingAddress.ship_city)"

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
        assert rel_stash["type"] == "INNER"
        assert rel_stash["cardinality"] == "MANY_TO_ONE"
        assert rel_stash["join_shape"] == "inline"
        assert "residual_predicates" not in rel_stash

    def test_a_non_equality_join_derives_no_key_and_stashes_the_condition(self):
        # KD1 negative: a residual-predicate (as-of) join is to-one only
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
        unrep = model_stash["unrepresentable_joins"][0]
        assert unrep["from"] == "ORDERS"
        assert unrep["to"] == "FX_RATES"
        assert unrep["on_expression"] == on_expr
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
        unattributed = model_stash["unattributed_formulas"]
        assert len(unattributed) == 1
        assert unattributed[0]["name"] == "Net Amount"
        assert unattributed[0]["expr"] == expr

        assert any(i["code"] == "TS-FIELD-UNATTRIBUTED" for i in result.issues.as_dicts())


class TestStashProtocol:
    def test_other_vendors_custom_extensions_pass_through_untouched(self):
        # X7. `convert()`'s own objects must stay compatible with a further
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
        # X8. Nested guids on the model_tables[] entry (fqn) and the model
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
        # X6: a model with an already-normalised name and no ThoughtSpot-only
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
        assert rel_stash["join_shape"] == "referencing"
        assert rel_stash["referencing_join"] == "orders_to_customers"
        assert rel_stash["type"] == "INNER"
        assert rel_stash["cardinality"] == "MANY_TO_ONE"

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
        unrep = model_stash["unrepresentable_joins"][0]
        assert unrep["on_expression"] == bad_condition
        assert any(i["code"] == "TS-JOIN-MALFORMED" for i in result.issues.as_dicts())


class TestUnconsumedColumnProperties:
    """Neither convert_field nor convert_metric preserves a ThoughtSpot-only
    column property (`index_type`, `value_casing`, ...): they never returned
    a fragment for the assembler to merge, so it vanished with no issue. The
    assembler now stashes the complement of what the converter actually
    reads, rather than an enumeration of known ThoughtSpot-only names."""

    ORDERS = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])

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
        assert _own_stash(field)["column_properties"] == {
            "index_type": "DONT_INDEX", "value_casing": "UPPER",
        }

    def test_a_metric_with_only_consumed_properties_gets_no_column_properties_key(self):
        result = self._convert_one({"column_type": "MEASURE", "aggregation": "SUM"})
        metric = result.model["semantic_model"][0]["metrics"][0]
        stashed = _own_stash(metric) or {}
        assert "column_properties" not in stashed

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
        assert _own_stash(field)["column_properties"] == {
            "a_property_ossie_thoughtspot_has_never_seen": 42
        }

    def test_the_metric_side_behaves_the_same_as_the_field_side(self):
        result = self._convert_one(
            {"column_type": "MEASURE", "aggregation": "SUM", "index_type": "DONT_INDEX"}
        )
        metric = result.model["semantic_model"][0]["metrics"][0]
        assert _own_stash(metric)["column_properties"] == {"index_type": "DONT_INDEX"}

    def test_identity_shaped_content_nested_in_a_property_value_is_dropped_not_stashed(self):
        # Found while re-verifying X8 for this fix: the complement copies an
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
        assert _own_stash(field)["column_properties"] == {"index_type": "DONT_INDEX"}
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

        unsurfaced = _own_stash(dataset)["unsurfaced_columns"]
        assert len(unsurfaced) == 1
        assert unsurfaced[0]["name"] == "Internal Flag"
        assert unsurfaced[0]["db_column_name"] == "INTERNAL_FLAG"

    def test_a_column_surfaced_only_as_a_measure_is_not_unsurfaced(self):
        # column_aggregation-shape metrics surface their physical column via
        # column_id too -- only ATTRIBUTE fields were checked before this
        # fix, which would have wrongly called this column unsurfaced.
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[{"name": "Total", "column_id": "ORDERS::Amount",
                      "properties": {"column_type": "MEASURE", "aggregation": "SUM"}}],
        )

        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        stashed = _own_stash(dataset) or {}
        assert "unsurfaced_columns" not in stashed

    def test_a_dataset_with_no_unsurfaced_columns_gets_no_such_key(self):
        orders = _table("ORDERS", columns=[_column("Amount", "AMOUNT", "DOUBLE")])
        model = _model(
            model_tables=[{"name": "ORDERS"}],
            columns=[_attribute("Amount", "ORDERS::Amount")],
        )
        result = convert(_document_set(model, orders))
        dataset = result.model["semantic_model"][0]["datasets"][0]
        stashed = _own_stash(dataset) or {}
        assert "unsurfaced_columns" not in stashed

    def test_unsurfaced_columns_populates_the_dataset_stash_on_its_own(self):
        # A dataset's stash always carries at least tml_object, so X6's
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
        assert stashed["unsurfaced_columns"][0]["name"] == "Amount"


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
        assert "parameters" not in stashed
        assert "obj_id" not in json.dumps(result.model)
        assert any(i["code"] == "TS-STASH-IDENTITY-DROPPED" for i in result.issues.as_dicts())

    def test_a_guid_nested_in_filters_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            filters=[{"column": "Region", "values": [{"nested": {"fqn": "f-1"}}]}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert "filters" not in stashed
        assert "fqn" not in json.dumps(result.model)

    def test_a_guid_nested_in_column_groups_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            column_groups=[{"name": "Sales", "meta": {"guid": "g-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert "column_groups" not in stashed
        assert "guid" not in json.dumps(result.model)

    def test_a_guid_nested_in_lesson_plans_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            lesson_plans=[{"lesson_id": 0, "extra": {"obj_id": "l-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert "lesson_plans" not in stashed
        assert "obj_id" not in json.dumps(result.model)

    def test_a_guid_nested_in_action_object_associations_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            action_object_associations=[{"action_name": "A", "context": {"fqn": "a-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert "action_object_associations" not in stashed
        assert "fqn" not in json.dumps(result.model)

    def test_a_guid_nested_in_constraints_is_dropped_not_fatal(self):
        orders, model = self._model_with(constraints={"rolling": {"window": {"guid": "c-1"}}})
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert "constraints" not in stashed
        assert "guid" not in json.dumps(result.model)

    def test_a_guid_nested_in_model_joins_with_is_dropped_not_fatal(self):
        orders, model = self._model_with(
            joins_with=[{"name": "j", "destination": {"fqn": "j-1"}}]
        )
        result = convert(_document_set(model, orders))
        stashed = _own_stash(result.model["semantic_model"][0]) or {}
        assert "model_joins_with" not in stashed
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
        assert "parameters" not in stashed
        assert stashed["filters"] == [{"column": "Region", "values": ["US"]}]


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
        assert rel_stash["residual_predicates"] == [
            "[ORDERS::Order Date] >= [CUSTOMERS::Effective Date]"
        ]
        assert rel_stash["on_expression"] == on_expr
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
        assert model_stash["unrepresentable_joins"][0]["on_expression"] == on_expr

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
        assert _own_stash(rel)["cardinality"] == "MANY_TO_MANY"
