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

"""Tests for the shared TML fixture sets under tests/fixtures/.

Two fixture sets live there. `tpcds/` mirrors the dataset, field,
relationship and metric names of the TPC-DS retail model every other
converter in this repository round-trips, so this converter is comparable
to its siblings rather than tested against a shape only it has seen; it
also deliberately carries the constructs that have broken in this
converter's own history and are easy for a fixture author to omit: a
display name differing from its db_column_name, a column name spelled as a
YAML 1.1 boolean token, a brace-carrying window formula, a formula
cross-reference, a connection-specific BOOL column, a SQL View with an
output alias differing from its column name, a physical column the Model
does not surface, a non-equality join condition, a composite-key
relationship, and one metric of each of the three TML shapes this
converter has to compose. `minimal/` is the smallest possible pair -- one
model, two tables, one relationship -- for debugging a failure without the
larger fixture's noise.

Each fixture directory holds the TML documents (`*.table.tml`,
`*.sql_view.tml`, `*.model.tml`) plus one `expected.ossie.yaml`: the Ossie
document `tml_to_ossie.convert()` produces from that TML, checked by hand
against the construct mapping this converter implements before being
committed here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ossie_thoughtspot import _yaml, tml, tml_to_ossie
from ossie_thoughtspot.constants import (
    DATASET_STASH_UNSURFACED_COLUMNS,
    DIALECT,
    FIELD_STASH_DATA_TYPE,
    METRIC_SHAPE_COLUMN_AGGREGATION,
    METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION,
    METRIC_STASH_SHAPE,
    PORTABLE_DIALECT,
    RELATIONSHIP_STASH_ON_EXPRESSION,
    VENDOR_KEY,
)

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
FIXTURE_SETS = ("minimal", "tpcds")

#: Every TML document kind `tml.load_document` accepts, mirrored here so a
#: fixture that accidentally ships a document of some other kind (a
#: `worksheet:`, say) is caught by the loading test rather than silently
#: skipped by whatever later step happens to ignore it.
_TML_KINDS = frozenset({"model", "table", "sql_view"})


def _tml_paths(fixture_dir: Path) -> list[Path]:
    return sorted(fixture_dir.glob("*.tml"))


def _load_document_set(fixture_dir: Path) -> tml.DocumentSet:
    texts = [
        (str(path), path.read_text(encoding="utf-8")) for path in _tml_paths(fixture_dir)
    ]
    return tml.load_document_set(texts)


def _load_expected(fixture_dir: Path) -> dict:
    text = (fixture_dir / "expected.ossie.yaml").read_text(encoding="utf-8")
    document = _yaml.load(text)
    assert isinstance(document, dict), (
        f"{fixture_dir / 'expected.ossie.yaml'} did not parse to a mapping"
    )
    return document


@pytest.mark.parametrize("fixture_name", FIXTURE_SETS)
class TestFixtureSetsLoad:
    def test_the_fixture_directory_has_tml_documents(self, fixture_name):
        fixture_dir = FIXTURES_ROOT / fixture_name
        paths = _tml_paths(fixture_dir)
        assert paths, f"expected at least one .tml fixture in {fixture_dir}"

    def test_every_tml_fixture_loads(self, fixture_name):
        fixture_dir = FIXTURES_ROOT / fixture_name
        for path in _tml_paths(fixture_dir):
            document = tml.load_document(path.read_text(encoding="utf-8"), source=str(path))
            assert document.kind in _TML_KINDS
            # R2: a fixture must never carry a root-level guid -- these are
            # hand-authored, portable documents, not exports from a live
            # instance.
            assert document.guid is None

    def test_the_fixture_set_loads_as_one_document_set(self, fixture_name):
        fixture_dir = FIXTURES_ROOT / fixture_name
        document_set = _load_document_set(fixture_dir)
        assert document_set.model.kind == "model"
        assert document_set.tables, "expected at least one table/sql_view document"


@pytest.mark.parametrize("fixture_name", FIXTURE_SETS)
class TestExpectedOutputIsValid:
    def test_expected_output_validates_against_the_upstream_schema(self, fixture_name):
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = Path(__file__).resolve().parents[3] / "core-spec" / "ossie-schema.json"
        with open(schema_path) as fh:
            schema = json.load(fh)
        expected = _load_expected(FIXTURES_ROOT / fixture_name)
        jsonschema.Draft202012Validator(schema).validate(expected)


@pytest.mark.parametrize("fixture_name", FIXTURE_SETS)
class TestConversionMatchesExpected:
    def test_converting_the_fixture_set_produces_the_expected_document(self, fixture_name):
        fixture_dir = FIXTURES_ROOT / fixture_name
        document_set = _load_document_set(fixture_dir)
        result = tml_to_ossie.convert(document_set)
        expected = _load_expected(fixture_dir)
        assert result.model == expected


@pytest.fixture(scope="module")
def _tpcds_semantic_model() -> dict:
    document_set = _load_document_set(FIXTURES_ROOT / "tpcds")
    return tml_to_ossie.convert(document_set).model["semantic_model"][0]


class TestTpcdsFixtureCoversItsRequiredConstructs:
    """Assertions naming the specific constructs the TPC-DS fixture set was
    built to exercise, so a future edit that accidentally drops one of them
    fails here with a clear message rather than only failing the (much
    larger) exact-document comparison above."""

    @pytest.fixture
    def dataset(self, _tpcds_semantic_model):
        return _tpcds_semantic_model

    def test_mirrors_the_tpcds_model_name(self, dataset):
        assert dataset["name"] == "tpcds_retail_model"

    def test_mirrors_the_five_core_datasets(self, dataset):
        names = {d["name"] for d in dataset["datasets"]}
        assert {"store_sales", "date_dim", "customer", "item", "store"} <= names

    def test_mirrors_the_four_core_relationships(self, dataset):
        names = {r["name"] for r in dataset["relationships"]}
        assert {
            "store_sales_to_date", "store_sales_to_customer",
            "store_sales_to_item", "store_sales_to_store",
        } <= names

    def test_mirrors_the_five_core_metrics(self, dataset):
        names = {m["name"] for m in dataset["metrics"]}
        assert {
            "total_sales", "total_profit", "customer_lifetime_value",
            "sales_by_brand", "store_productivity",
        } <= names

    def test_a_computed_attribute_formula_is_present(self, dataset):
        customer = next(d for d in dataset["datasets"] if d["name"] == "customer")
        field = next(f for f in customer["fields"] if f["name"] == "customer_full_name")
        assert "datatype" not in field  # a formula-backed field declares no type

    def test_store_has_a_display_name_differing_from_its_db_column_name(self, dataset):
        store = next(d for d in dataset["datasets"] if d["name"] == "store")
        field = next(f for f in store["fields"] if f["name"] == "s_store_name")
        dialects = {d["dialect"]: d["expression"] for d in field["expression"]["dialects"]}
        assert dialects[PORTABLE_DIALECT] == "store.STORE_NM"

    def test_the_on_column_survives_as_a_string_not_a_boolean(self, dataset):
        store = next(d for d in dataset["datasets"] if d["name"] == "store")
        field = next(f for f in store["fields"] if f["name"] == "on")
        assert field["name"] == "on"
        assert field["datatype"] == "Boolean"

    def test_a_brace_carrying_window_formula_round_trips_verbatim(self, dataset):
        metric = next(m for m in dataset["metrics"] if m["name"] == "prior_period_profit")
        dialects = {d["dialect"]: d["expression"] for d in metric["expression"]["dialects"]}
        assert dialects[DIALECT] == (
            "last_value ( sum ( [store_sales::ss_net_profit] ) , query_groups ( ) , "
            "{ [date_dim::d_date] } )"
        )

    def test_a_formula_cross_reference_is_preserved(self, dataset):
        metric = next(m for m in dataset["metrics"] if m["name"] == "profit_margin")
        dialects = {d["dialect"]: d["expression"] for d in metric["expression"]["dialects"]}
        assert dialects[DIALECT] == "[formula_total_profit] / [formula_total_sales]"
        assert PORTABLE_DIALECT not in dialects  # a cross-reference is never portable

    def test_a_bool_column_keeps_its_connection_specific_spelling(self, dataset):
        store = next(d for d in dataset["datasets"] if d["name"] == "store")
        field = next(f for f in store["fields"] if f["name"] == "on")
        extensions = {e["vendor_name"]: json.loads(e["data"]) for e in field["custom_extensions"]}
        assert extensions[VENDOR_KEY][FIELD_STASH_DATA_TYPE] == "BOOL"

    def test_a_sql_view_is_present_with_a_differing_output_alias(self, dataset):
        sv = next(d for d in dataset["datasets"] if d["name"] == "store_returns_sv")
        field = next(f for f in sv["fields"] if f["name"] == "sr_return_amt")
        dialects = {d["dialect"]: d["expression"] for d in field["expression"]["dialects"]}
        assert dialects[PORTABLE_DIALECT] == "store_returns_sv.RETURN_AMT"

    def test_a_physical_column_the_model_does_not_surface_is_stashed(self, dataset):
        store_sales = next(d for d in dataset["datasets"] if d["name"] == "store_sales")
        extensions = {
            e["vendor_name"]: json.loads(e["data"]) for e in store_sales["custom_extensions"]
        }
        unsurfaced = {
            c["name"] for c in extensions[VENDOR_KEY][DATASET_STASH_UNSURFACED_COLUMNS]
        }
        assert "ss_ticket_number" in unsurfaced

    def test_a_composite_key_relationship_is_present(self, dataset):
        relationship = next(
            r for r in dataset["relationships"] if r["name"] == "store_returns_sv_to_store_sales"
        )
        assert relationship["to_columns"] == ["ss_item_sk", "ss_ticket_number"]
        store_sales = next(d for d in dataset["datasets"] if d["name"] == "store_sales")
        assert store_sales["primary_key"] == ["ss_item_sk", "ss_ticket_number"]

    def test_a_non_equality_join_condition_yields_a_relationship_with_residuals(self, dataset):
        relationship = next(
            r for r in dataset["relationships"] if r["name"] == "store_returns_sv_to_item"
        )
        extensions = {
            e["vendor_name"]: json.loads(e["data"]) for e in relationship["custom_extensions"]
        }
        assert extensions[VENDOR_KEY][RELATIONSHIP_STASH_ON_EXPRESSION] == (
            "[store_returns_sv::sr_item_sk] = [item::i_item_sk] and "
            "[store_returns_sv::sr_return_amt] <= [item::i_current_price]"
        )

    def test_the_three_metric_shapes_are_all_present(self, dataset):
        metrics_by_name = {m["name"]: m for m in dataset["metrics"]}

        # Bare aggregate over a physical column with no separate stash --
        # this is also the default TML shape (`formula`), which the writer
        # never stashes.
        assert "custom_extensions" not in metrics_by_name["total_sales"]

        # A physical column plus a load-bearing aggregation.
        column_aggregation = metrics_by_name["total_return_quantity"]
        extensions = {
            e["vendor_name"]: json.loads(e["data"])
            for e in column_aggregation["custom_extensions"]
        }
        assert extensions[VENDOR_KEY][METRIC_STASH_SHAPE] == METRIC_SHAPE_COLUMN_AGGREGATION

        # A scalar formula plus a load-bearing aggregation, composed into
        # one expression.
        scalar_plus_agg = metrics_by_name["avg_price_adjustment"]
        extensions = {
            e["vendor_name"]: json.loads(e["data"]) for e in scalar_plus_agg["custom_extensions"]
        }
        assert (
            extensions[VENDOR_KEY][METRIC_STASH_SHAPE]
            == METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION
        )
