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

"""Round-trip tests, in both directions, over both fixture sets.

A round trip that only compares documents is a weaker test than it looks. The
return leg of `TML -> Ossie -> TML` reads the expression back out of the
THOUGHTSPOT dialect entry, which `tml_to_ossie.py` always writes verbatim --
so the original formula text comes back byte for byte whether or not the
*portable* ANSI_SQL sibling next to it was ever built correctly. A round trip
that only asserts the documents match can pass while translation is entirely
broken, because preservation and translation are proved by two different
code paths that happen to feed the same comparison. So this module asserts
them separately: `TestTmlRoundTripReproduces*` proves preservation (the
document set comes back, expressions held to exact string equality);
`TestTmlRoundTripTranslat*` proves translation, directly on the ANSI_SQL
siblings the intermediate Ossie document carries -- these fail on a
translation regression even when every preservation assertion still passes.

A round trip that only compares documents is also blind to the issue log,
and the issue log is this converter's whole contract with whoever reads it:
a conversion that silently drops something and one that reports the same
drop correctly produce identical documents. Every assertion below that
checks a real difference also checks that the issue log named it -- object
and all -- rather than trusting a bare count.

Not every difference this module finds is a defect. `test_minimal_model_
reproduces_every_column_and_formula_except_the_aggregation_convention` and
its tpcds counterpart document a difference the converter's own code
already explains and justifies -- collapsing three ThoughtSpot Model
TML metric shapes into one on the way out. That is asserted as the
current, intentional behaviour.

Two further differences this module found while it was first written had
no such justification anywhere in the source, and were deliberately NOT
asserted as correct -- baking in a value nobody has justified is exactly
how a real regression gets permanently disguised as a passing test. Both
have since been fixed in `ossie_to_thoughtspot.py`, and this module now
asserts the fix directly instead of excluding the field it touches: a
physical Table column no longer gains a `description` its own document
never had (see `_columns_by_name` and the dedicated description test), and
a relationship whose join was Table-referenced keeps its own `name` and
its Table `joins_with[]` entry across the round trip, restored from the
stash rather than resynthesized -- with a currency check, so a relationship
renamed after the stash was written falls back to the old, safe behaviour
instead of restoring a stale reference under the wrong name (see
`TestReferencingJoinRestoration`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ossie_thoughtspot import _yaml, datatypes, ossie_to_thoughtspot, stash, tml, tml_to_ossie
from ossie_thoughtspot.constants import (
    DATASET_STASH_CONNECTION_NAME,
    DIALECT,
    DOCUMENT_VERSION,
    PORTABLE_DIALECT,
    RELATIONSHIP_STASH_CARDINALITY,
    RELATIONSHIP_STASH_TYPE,
)
from ossie_thoughtspot.datatypes import OSSIE_DATATYPES

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
FIXTURE_SETS = ("minimal", "tpcds")


# ---------------------------------------------------------------------------
# Loading helpers -- small, deliberate duplicates of test_fixtures.py's own
# (this module's fixture-loading needs are the same, but its assertions
# are a different concern and belong in a different file).
# ---------------------------------------------------------------------------

def _tml_paths(fixture_dir: Path) -> list[Path]:
    return sorted(fixture_dir.glob("*.tml"))


def _load_document_set(fixture_dir: Path) -> tml.DocumentSet:
    texts = [(str(p), p.read_text(encoding="utf-8")) for p in _tml_paths(fixture_dir)]
    return tml.load_document_set(texts)


def _load_expected(fixture_dir: Path) -> dict:
    text = (fixture_dir / "expected.ossie.yaml").read_text(encoding="utf-8")
    document = _yaml.load(text)
    assert isinstance(document, dict)
    return document


def _tml_roundtrip(fixture_name: str):
    """`(document_set, ossie_result, tml_result)` for one fixture set's own
    `TML -> Ossie -> TML` round trip. `ossie_result` is the intermediate
    Ossie document -- the one translation is checked against -- and
    `tml_result` is the returned TML document set -- the one preservation
    is checked against. Every test in this module reads one or the other of
    these, never a third, independently-converted copy of either."""
    document_set = _load_document_set(FIXTURES_ROOT / fixture_name)
    ossie_result = tml_to_ossie.convert(document_set)
    tml_result = ossie_to_thoughtspot.convert(ossie_result.model)
    return document_set, ossie_result, tml_result


def _ossie_roundtrip(fixture_name: str):
    """`(expected_ossie, tml_result, ossie_result)` for one fixture set's own
    `Ossie -> TML -> Ossie` round trip, starting from its checked-in
    `expected.ossie.yaml` rather than from the TML fixtures."""
    expected = _load_expected(FIXTURES_ROOT / fixture_name)
    tml_result = ossie_to_thoughtspot.convert(expected)
    ossie_result = tml_to_ossie.convert(tml_result.documents)
    return expected, tml_result, ossie_result


def _table_by_name(document_set: tml.DocumentSet, name: str) -> tml.TmlDocument:
    return next(t for t in document_set.tables if t.body.get("name") == name)


def _dataset(model: dict, name: str) -> dict:
    return next(d for d in model["datasets"] if d["name"] == name)


def _field(dataset: dict, name: str) -> dict:
    return next(f for f in dataset["fields"] if f["name"] == name)


def _metric(model: dict, name: str) -> dict:
    return next(m for m in model["metrics"] if m["name"] == name)


def _dialects(obj: dict) -> dict[str, str]:
    return {d["dialect"]: d["expression"] for d in obj["expression"]["dialects"]}


def _issue_refs(issues, code: str) -> set[str]:
    return {i.object_ref for i in issues.issues if i.code == code}


def _columns_by_name(body: dict) -> dict[str, dict]:
    """A Table/SQL-View document's physical columns, keyed by `name` --
    order-insensitive, content-exact (`description` included: a physical
    column reaching `_physical_table_column`/`_physical_sql_view_column` is
    always Model-surfaced, so it never carries one -- see
    `test_a_model_surfaced_fields_description_is_never_duplicated_onto_its_
    physical_column` below).

    `_build_table_body`/`_build_sql_view_body` in ossie_to_thoughtspot.py
    unconditionally emit a dataset's surfaced fields first and its still-
    unsurfaced physical columns after (see those two functions): a fixed
    policy, not a reflection of whatever order the source document
    happened to list its own columns in. The `minimal` fixture's own
    `customers` table lists its unsurfaced `customer_id` column before its
    surfaced `customer_name` field; `orders` lists its surfaced column
    first -- both legitimate authoring choices TML places no meaning on, so
    comparing column list *order* would fail on a difference the converter
    never claims to preserve.
    """
    key = "sql_view_columns" if "sql_view_columns" in body else "columns"
    return {c["name"]: c for c in body.get(key, [])}


def _model_columns_by_name(body: dict) -> dict[str, dict]:
    return {c["name"]: c for c in body.get("columns") or []}


def _model_formulas_by_id(body: dict) -> dict[str, dict]:
    return {f["id"]: f for f in body.get("formulas") or []}


def _relationship_identity(rel: dict) -> tuple:
    return (rel["from"], rel["to"], tuple(rel["from_columns"]), tuple(rel["to_columns"]))


# ---------------------------------------------------------------------------
# TML -> Ossie -> TML: preservation.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", FIXTURE_SETS)
class TestTmlRoundTripReproducesTableDocuments:
    def test_every_table_or_sql_view_document_is_present(self, fixture_name):
        document_set, _, tml_result = _tml_roundtrip(fixture_name)
        original_names = {t.body["name"] for t in document_set.tables}
        new_names = {t.body["name"] for t in tml_result.documents.tables}
        assert new_names == original_names

    def test_every_physical_column_survives_with_its_exact_content(self, fixture_name):
        document_set, _, tml_result = _tml_roundtrip(fixture_name)
        for original in document_set.tables:
            new = _table_by_name(tml_result.documents, original.body["name"])
            assert new.kind == original.kind
            assert _columns_by_name(new.body) == _columns_by_name(original.body)

    def test_shared_table_level_attributes_survive(self, fixture_name):
        document_set, _, tml_result = _tml_roundtrip(fixture_name)
        for original in document_set.tables:
            new = _table_by_name(tml_result.documents, original.body["name"])
            # "joins_with" included: a Table-referenced join's own
            # joins_with[] entry -- name, destination, condition, type,
            # cardinality -- is restored from the relationship's stash
            # rather than dropped (see TestReferencingJoinRestoration).
            for key in ("name", "db", "schema", "db_table", "sql_query", "connection", "joins_with"):
                if key in original.body or key in new.body:
                    assert new.body.get(key) == original.body.get(key), (original.body["name"], key)


def test_minimal_model_reproduces_every_column_and_formula_except_the_aggregation_convention():
    document_set, _, tml_result = _tml_roundtrip("minimal")
    original = document_set.model.body
    new = tml_result.documents.model.body
    assert new["name"] == original["name"]
    assert new.get("description") == original.get("description")
    # The relationship's own referencing_join name survives too, so
    # `model_tables[].joins[]` -- {"referencing_join": "orders_to_customers"}
    # -- comes back exactly, not resynthesized as an inline join.
    assert new["model_tables"] == original["model_tables"]

    orig_columns = _model_columns_by_name(original)
    new_columns = _model_columns_by_name(new)
    assert set(new_columns) == set(orig_columns)

    # "total_order_amount"'s formula ("sum ( ... )") already aggregates, so
    # _build_metric (ossie_to_thoughtspot.py) sets the surfacing column's own
    # `aggregation` to match, as the convention a real ThoughtSpot-authored
    # document carries -- a documented no-op over an already-aggregate
    # expression, not a change to what the metric evaluates to.
    for name in set(orig_columns) - {"total_order_amount"}:
        assert new_columns[name] == orig_columns[name], name
    assert "aggregation" not in orig_columns["total_order_amount"]["properties"]
    assert new_columns["total_order_amount"]["properties"]["aggregation"] == "SUM"
    assert _issue_refs(tml_result.issues, "TS-MODEL-METRIC-AGGREGATION-CONVENTION") == {
        "metric:total_order_amount"
    }

    # formulas[] itself is untouched by the convention -- only the
    # surfacing column's properties change.
    assert _model_formulas_by_id(new) == _model_formulas_by_id(original)


def test_tpcds_model_reproduces_every_column_and_formula_except_the_metric_shape_collapse():
    document_set, _, tml_result = _tml_roundtrip("tpcds")
    original = document_set.model.body
    new = tml_result.documents.model.body
    # All four of store_sales's referencing joins (including the one whose
    # target dataset name, "date_dim", differs from its own name,
    # "store_sales_to_date" -- the exact case that used to be resynthesized
    # as "store_sales_to_date_dim") come back exactly.
    assert new["model_tables"] == original["model_tables"]

    orig_columns = _model_columns_by_name(original)
    new_columns = _model_columns_by_name(new)
    assert set(new_columns) == set(orig_columns)

    # Three metrics keep their `formula` shape but gain the same
    # aggregation-convention property as minimal's "total_order_amount"
    # above. "total_return_quantity" is different: it arrives as
    # `column_id` + a load-bearing `aggregation` (never a `formula` in the
    # source document at all) -- Ossie's own Metric schema has no
    # `column_id` field, so the only shape available on the way back is a
    # formula, and the aggregate is composed into a brand new formulas[]
    # entry rather than surviving as a column-level property.
    convention_names = {"total_sales", "total_profit", "sales_by_brand"}
    shape_collapsed = {"total_return_quantity"}
    for name in set(orig_columns) - convention_names - shape_collapsed:
        assert new_columns[name] == orig_columns[name], name

    for name in convention_names:
        assert "aggregation" not in orig_columns[name]["properties"], name
        assert new_columns[name]["properties"]["aggregation"] == "SUM", name
    assert _issue_refs(tml_result.issues, "TS-MODEL-METRIC-AGGREGATION-CONVENTION") == {
        f"metric:{name}" for name in convention_names | shape_collapsed
    }

    assert orig_columns["total_return_quantity"]["column_id"] == "store_returns_sv::sr_return_quantity"
    assert "column_id" not in new_columns["total_return_quantity"]
    assert new_columns["total_return_quantity"]["formula_id"] == "formula_total_return_quantity"
    assert new_columns["total_return_quantity"]["properties"]["aggregation"] == "SUM"

    orig_formulas = _model_formulas_by_id(original)
    new_formulas = _model_formulas_by_id(new)
    assert set(new_formulas) == set(orig_formulas) | {"formula_total_return_quantity"}
    for formula_id in orig_formulas:
        assert new_formulas[formula_id] == orig_formulas[formula_id], formula_id
    assert new_formulas["formula_total_return_quantity"] == {
        "id": "formula_total_return_quantity",
        "name": "total_return_quantity",
        "expr": "sum ( [store_returns_sv::sr_return_quantity] )",
    }


class TestReferencingJoinRestoration:
    """`_join_entry_for_relationship` (ossie_to_thoughtspot.py) restores a
    Table-referenced join's own shape -- a `referencing_join` pointer on the
    Model entry plus a matching `joins_with[]` entry on the Table document
    -- from the relationship's `RELATIONSHIP_STASH_REFERENCING_JOIN` stash,
    rather than always collapsing it to an inline join. TML's inline join
    syntax has no name field at all, so without this a relationship whose
    join round-trips through TML gets a fresh name synthesized from its own
    from/to dataset names on the next pass -- unstable exactly when the
    relationship's own name does not already match that pattern (a target
    dataset named `date_dim` but a relationship named `..._to_date`, tpcds's
    own `store_sales_to_date`).
    """

    def test_minimal_referencing_join_is_restored_with_its_own_name(self):
        document_set, _, tml_result = _tml_roundtrip("minimal")
        original_orders = _table_by_name(document_set, "orders")
        new_orders = _table_by_name(tml_result.documents, "orders")
        assert new_orders.body["joins_with"] == original_orders.body["joins_with"]

        new_join = tml_result.documents.model.body["model_tables"][0]["joins"][0]
        assert new_join == {"referencing_join": "orders_to_customers"}

    def test_tpcds_four_referencing_joins_are_restored_with_their_own_names(self):
        document_set, _, tml_result = _tml_roundtrip("tpcds")
        original_store_sales = _table_by_name(document_set, "store_sales")
        new_store_sales = _table_by_name(tml_result.documents, "store_sales")
        assert new_store_sales.body["joins_with"] == original_store_sales.body["joins_with"]

        store_sales_entry = next(
            t for t in tml_result.documents.model.body["model_tables"] if t["name"] == "store_sales"
        )
        # The exact case a name synthesized from from/to dataset names gets
        # wrong: the target dataset is "date_dim", not "date", so a
        # resynthesized name would read "store_sales_to_date_dim".
        assert {"referencing_join": "store_sales_to_date"} in store_sales_entry["joins"]
        assert {j["referencing_join"] for j in store_sales_entry["joins"]} == {
            "store_sales_to_date", "store_sales_to_customer", "store_sales_to_item", "store_sales_to_store",
        }

        # The relationships already inline in the source document
        # (store_returns_sv's own joins, one of them carrying a residual
        # predicate) have no referencing_join to restore and are
        # unaffected -- still emitted inline, verbatim.
        store_returns_sv_entry = next(
            t for t in tml_result.documents.model.body["model_tables"]
            if t["name"] == "store_returns_sv"
        )
        original_store_returns_sv_entry = next(
            t for t in document_set.model.body["model_tables"] if t["name"] == "store_returns_sv"
        )
        assert store_returns_sv_entry["joins"] == original_store_returns_sv_entry["joins"]

    def test_a_relationship_renamed_since_the_stash_was_written_falls_back_and_logs(self):
        """A relationship's `name` can be edited directly in the Ossie
        document (there is nothing to keep it in sync with the stash it was
        written alongside). Restoring the stashed `referencing_join` under
        that stale name would point the Table's `joins_with[]` reference at
        a name the live relationship no longer answers to -- so the
        currency check (RELATIONSHIP_STASH_REFERENCING_JOIN compared
        directly against the relationship's own live `name`) drops it
        instead, exactly as if no stash were present at all, and reports
        why. The other three relationships from the same dataset, whose
        stash is still current, are unaffected.
        """
        expected = _load_expected(FIXTURES_ROOT / "tpcds")
        model = expected["semantic_model"][0]
        relationship = next(r for r in model["relationships"] if r["name"] == "store_sales_to_date")
        relationship["name"] = "renamed_relationship"

        tml_result = ossie_to_thoughtspot.convert(expected)
        store_sales = _table_by_name(tml_result.documents, "store_sales")
        joins_with_names = {j["name"] for j in store_sales.body.get("joins_with") or []}
        assert joins_with_names == {"store_sales_to_customer", "store_sales_to_item", "store_sales_to_store"}

        store_sales_entry = next(
            t for t in tml_result.documents.model.body["model_tables"] if t["name"] == "store_sales"
        )
        renamed_join = next(j for j in store_sales_entry["joins"] if j.get("with") == "date_dim")
        assert renamed_join == {
            "with": "date_dim",
            "on": "[store_sales::ss_sold_date_sk] = [date_dim::d_date_sk]",
            "type": "INNER",
            "cardinality": "MANY_TO_ONE",
        }
        assert _issue_refs(tml_result.issues, "TS-JOIN-REFERENCING-JOIN-STALE") == {
            "relationship:renamed_relationship"
        }


def test_a_model_surfaced_fields_description_is_never_duplicated_onto_its_physical_column():
    """tpcds's `store.on` field carries a `description` on the Model's own
    `columns[]` entry; the Table's own `on` column never has one. That
    description must survive on the Model side and must NOT be invented on
    the Table side -- `_physical_table_column`/`_physical_sql_view_column`
    never read a field's `description` at all, so the only place a
    Model-surfaced field's description can end up is the one place the
    mapping rule puts it.
    """
    document_set, _, tml_result = _tml_roundtrip("tpcds")
    original_store = _table_by_name(document_set, "store")
    assert "description" not in _columns_by_name(original_store.body)["on"]

    new_store = _table_by_name(tml_result.documents, "store")
    assert "description" not in _columns_by_name(new_store.body)["on"]

    new_model_columns = _model_columns_by_name(tml_result.documents.model.body)
    assert new_model_columns["on"]["description"] == "Whether the store is currently active and open for business."


# ---------------------------------------------------------------------------
# TML -> Ossie -> TML: translation. Asserted directly on the intermediate
# Ossie document's ANSI_SQL siblings -- the half a preservation test, by
# construction, cannot see (see module docstring).
# ---------------------------------------------------------------------------

def test_minimal_physical_field_translates_to_its_dataset_dot_column():
    _, ossie_result, _ = _tml_roundtrip("minimal")
    model = ossie_result.model["semantic_model"][0]
    field = _field(_dataset(model, "orders"), "order_id")
    dialects = _dialects(field)
    assert dialects[DIALECT] == "[orders::order_id]"
    assert dialects[PORTABLE_DIALECT] == "orders.order_id"


def test_minimal_known_unportable_metric_has_no_portable_sibling_and_an_issue():
    _, ossie_result, _ = _tml_roundtrip("minimal")
    model = ossie_result.model["semantic_model"][0]
    metric = _metric(model, "total_order_amount")
    assert PORTABLE_DIALECT not in _dialects(metric)
    assert "metric:total_order_amount" in _issue_refs(ossie_result.issues, "TS-EXPR-THOUGHTSPOT-ONLY")


def test_tpcds_physical_fields_translate_to_their_warehouse_column_even_when_the_display_name_differs():
    _, ossie_result, _ = _tml_roundtrip("tpcds")
    model = ossie_result.model["semantic_model"][0]
    # s_store_name's display name differs from its warehouse column name
    # (STORE_NM); the portable expression has to carry the warehouse name,
    # not the display-derived Ossie identifier.
    store_name = _field(_dataset(model, "store"), "s_store_name")
    assert _dialects(store_name)[PORTABLE_DIALECT] == "store.STORE_NM"
    # sr_return_amt is a SQL View column whose output alias (RETURN_AMT)
    # differs from its own field name -- the same fact, for a query rather
    # than a table.
    return_amt = _field(_dataset(model, "store_returns_sv"), "sr_return_amt")
    assert _dialects(return_amt)[PORTABLE_DIALECT] == "store_returns_sv.RETURN_AMT"


def test_tpcds_known_unportable_metrics_have_no_portable_sibling_and_an_issue():
    _, ossie_result, _ = _tml_roundtrip("tpcds")
    model = ossie_result.model["semantic_model"][0]

    # A formula cross-reference: inlining the referenced formulas is out of
    # scope, so only the THOUGHTSPOT dialect entry is emitted.
    profit_margin = _metric(model, "profit_margin")
    assert PORTABLE_DIALECT not in _dialects(profit_margin)
    assert "metric:profit_margin" in _issue_refs(ossie_result.issues, "TS-EXPR-FORMULA-REFERENCE")

    # A bare aggregate call over a physical column: still a function call,
    # not a bare column reference, so it is THOUGHTSPOT-only too.
    total_sales = _metric(model, "total_sales")
    assert PORTABLE_DIALECT not in _dialects(total_sales)
    assert "metric:total_sales" in _issue_refs(ossie_result.issues, "TS-EXPR-THOUGHTSPOT-ONLY")

    # A physical column reference the model never surfaces as a field
    # resolves to nothing at all, which is a different unportable reason
    # (and a different code) from the two above.
    total_return_quantity = _metric(model, "total_return_quantity")
    assert PORTABLE_DIALECT not in _dialects(total_return_quantity)
    assert "metric:total_return_quantity" in _issue_refs(ossie_result.issues, "TS-EXPR-UNRESOLVED")


def test_a_metrics_portable_expression_carries_its_column_level_aggregation():
    """A metric built from `column_id` + a load-bearing `aggregation` (never
    a bare `formula`) composes a portable ANSI_SQL sibling that carries the
    same aggregate (`_compose_aggregate_entries`, tml_to_ossie.py).

    Exercised here with a small, purpose-built document rather than either
    fixture set: neither fixture's own `column_id` + `aggregation` metric
    (tpcds's `total_return_quantity`, asserted unportable just above) takes
    this shape over a column the model ALSO surfaces as a field, which is
    what `_compose_aggregate_entries` requires before it can name a
    warehouse column at all.
    """
    table = tml.TmlDocument(
        kind="table",
        body={
            "name": "widgets",
            "db": "TESTDB",
            "schema": "PUBLIC",
            "db_table": "WIDGETS",
            "connection": {"name": "Test Connection"},
            "columns": [
                {"name": "amount", "db_column_name": "amount", "db_column_properties": {"data_type": "INT64"}},
            ],
        },
        guid=None,
    )
    model_doc = tml.TmlDocument(
        kind="model",
        body={
            "name": "aggregate_probe_model",
            "model_tables": [{"name": "widgets"}],
            "columns": [
                {"name": "amount", "column_id": "widgets::amount", "properties": {"column_type": "ATTRIBUTE"}},
                {
                    "name": "total_amount",
                    "column_id": "widgets::amount",
                    "properties": {"column_type": "MEASURE", "aggregation": "SUM"},
                },
            ],
        },
        guid=None,
    )
    document_set = tml.DocumentSet(model=model_doc, tables=(table,))

    ossie_result = tml_to_ossie.convert(document_set)
    metric = _metric(ossie_result.model["semantic_model"][0], "total_amount")
    dialects = _dialects(metric)
    assert dialects[DIALECT] == "sum ( [widgets::amount] )"
    assert dialects[PORTABLE_DIALECT] == "SUM(widgets.amount)"

    # The composed aggregate survives being written back out as a formula
    # too -- a metric is always a formula on the way back.
    tml_result = ossie_to_thoughtspot.convert(ossie_result.model)
    new_columns = _model_columns_by_name(tml_result.documents.model.body)
    formulas = _model_formulas_by_id(tml_result.documents.model.body)
    formula_id = new_columns["total_amount"]["formula_id"]
    assert formulas[formula_id]["expr"] == "sum ( [widgets::amount] )"


# ---------------------------------------------------------------------------
# Ossie -> TML -> Ossie: the datatype map's own declared_loss taxonomy.
# ---------------------------------------------------------------------------

_LOSSY_DATATYPES = sorted(dt for dt in OSSIE_DATATYPES if datatypes.declared_loss(dt))
_LOSSLESS_DATATYPES = sorted(dt for dt in OSSIE_DATATYPES if not datatypes.declared_loss(dt))


def _datatype_probe_document() -> dict:
    """One Ossie dataset with one field per datatype in the closed
    `OSSIE_DATATYPES` enum, so every entry in `datatypes._DECLARED_LOSS`
    (and every entry NOT in it) is exercised in one round trip."""
    dataset = stash.write_stash(
        {
            "name": "widgets",
            "source": "TESTDB.PUBLIC.WIDGETS",
            "fields": [
                {
                    "name": f"col_{dt.lower()}",
                    "datatype": dt,
                    "expression": {"dialects": [{"dialect": DIALECT, "expression": f"[widgets::col_{dt.lower()}]"}]},
                }
                for dt in sorted(OSSIE_DATATYPES)
            ],
        },
        {DATASET_STASH_CONNECTION_NAME: "Test Connection"},
    )
    return {
        "version": DOCUMENT_VERSION,
        "semantic_model": [{"name": "datatype_probe_model", "datasets": [dataset]}],
    }


def test_the_declared_loss_datatypes_are_exactly_datetimetz_float_opaque_and_time():
    # Pins datatypes.py's own taxonomy so the two tests below -- which
    # split their assertions on this same set -- fail loudly if a future
    # change to _DECLARED_LOSS adds or removes a member, rather than
    # silently checking a set that no longer matches the module they test.
    assert _LOSSY_DATATYPES == ["DateTimeTz", "Float", "Opaque", "Time"]


def test_every_declared_loss_datatype_is_flagged_before_the_round_trip_changes_it():
    ossie_in = _datatype_probe_document()
    tml_result = ossie_to_thoughtspot.convert(ossie_in)
    flagged = _issue_refs(tml_result.issues, "TS-FIELD-DATATYPE-DECLARED-LOSS")
    assert flagged == {f"field:col_{dt.lower()}" for dt in _LOSSY_DATATYPES}

    ossie_result = tml_to_ossie.convert(tml_result.documents)
    new_dataset = ossie_result.model["semantic_model"][0]["datasets"][0]
    new_by_name = {f["name"]: f.get("datatype") for f in new_dataset["fields"]}
    # Exactly what datatypes.py's own _TO_TML/_TO_OSSIE maps predict -- the
    # TML type each lossy datatype collapses into, mapped back.
    assert new_by_name["col_datetimetz"] == "DateTime"
    assert new_by_name["col_float"] == "Decimal"
    assert new_by_name["col_opaque"] == "String"
    assert new_by_name["col_time"] == "String"
    for dt in _LOSSY_DATATYPES:
        assert new_by_name[f"col_{dt.lower()}"] != dt


def test_every_lossless_datatype_returns_exactly():
    ossie_in = _datatype_probe_document()
    tml_result = ossie_to_thoughtspot.convert(ossie_in)
    assert _issue_refs(tml_result.issues, "TS-FIELD-DATATYPE-DECLARED-LOSS") == {
        f"field:col_{dt.lower()}" for dt in _LOSSY_DATATYPES
    }  # none of the lossless fields are flagged

    ossie_result = tml_to_ossie.convert(tml_result.documents)
    new_dataset = ossie_result.model["semantic_model"][0]["datasets"][0]
    new_by_name = {f["name"]: f.get("datatype") for f in new_dataset["fields"]}
    for dt in _LOSSLESS_DATATYPES:
        assert new_by_name[f"col_{dt.lower()}"] == dt, dt


# ---------------------------------------------------------------------------
# Ossie -> TML -> Ossie, over both fixture sets' own expected.ossie.yaml.
# ---------------------------------------------------------------------------

def _fields_with_datatype(model: dict):
    for dataset in model["datasets"]:
        for field in dataset.get("fields", []):
            if "datatype" in field:
                yield dataset["name"], field["name"], field["datatype"]


@pytest.mark.parametrize("fixture_name", FIXTURE_SETS)
def test_every_declared_field_datatype_returns_exactly(fixture_name):
    """Both fixture sets only ever declare datatypes datatypes.py calls
    lossless (String, Integer, Decimal, Boolean, Date -- see the four
    declared_loss types covered by the synthetic probe above), so every
    field with a declared datatype must come back unchanged."""
    expected, _, ossie_result = _ossie_roundtrip(fixture_name)
    original = list(_fields_with_datatype(expected["semantic_model"][0]))
    assert original, "expected at least one field with a declared datatype"

    new_by_key = {
        (d["name"], f["name"]): f.get("datatype")
        for d in ossie_result.model["semantic_model"][0]["datasets"]
        for f in d.get("fields", [])
    }
    for dataset_name, field_name, expected_datatype in original:
        assert new_by_key[(dataset_name, field_name)] == expected_datatype, (dataset_name, field_name)


def test_tpcds_metric_datatype_is_dropped_with_an_issue_naming_it():
    """A Metric's own `datatype` is a different kind of loss from anything
    datatypes.py's own map declares: Model TML has no `data_type` key
    anywhere for a formula-backed metric, so there is nowhere at all to
    write one, regardless of what the datatype is. It is still the same
    declared-and-reported shape -- a real loss, an issue naming it before
    the round trip discards it."""
    expected = _load_expected(FIXTURES_ROOT / "tpcds")
    tml_result = ossie_to_thoughtspot.convert(expected)
    assert _issue_refs(tml_result.issues, "TS-MODEL-METRIC-DATATYPE-UNWRITABLE") == {
        "metric:total_return_quantity"
    }

    original_metric = _metric(expected["semantic_model"][0], "total_return_quantity")
    assert original_metric["datatype"] == "Integer"

    ossie_result = tml_to_ossie.convert(tml_result.documents)
    new_metric = _metric(ossie_result.model["semantic_model"][0], "total_return_quantity")
    assert "datatype" not in new_metric


@pytest.mark.parametrize("fixture_name", FIXTURE_SETS)
def test_every_relationship_survives_by_from_to_columns_type_cardinality_and_name(fixture_name):
    """`name` is included in this comparison -- see
    TestReferencingJoinRestoration for why a relationship whose join was
    Table-referenced now keeps its own name across the TML leg instead of
    getting a fresh one synthesized from its from/to dataset names.
    tpcds's own `store_sales_to_date` (target dataset `date_dim`, not
    `date`) is the concrete case that used to come back renamed.
    """
    expected, _, ossie_result = _ossie_roundtrip(fixture_name)
    original_model = expected["semantic_model"][0]
    new_model = ossie_result.model["semantic_model"][0]

    original_by_identity = {
        _relationship_identity(r): r for r in original_model.get("relationships", [])
    }
    new_by_identity = {_relationship_identity(r): r for r in new_model.get("relationships", [])}
    assert set(new_by_identity) == set(original_by_identity)

    for identity, original_rel in original_by_identity.items():
        new_rel = new_by_identity[identity]
        assert new_rel["name"] == original_rel["name"], identity
        original_payload = stash.read_stash(original_rel)
        new_payload = stash.read_stash(new_rel)
        assert new_payload.get(RELATIONSHIP_STASH_TYPE) == original_payload.get(RELATIONSHIP_STASH_TYPE)
        assert new_payload.get(RELATIONSHIP_STASH_CARDINALITY) == original_payload.get(
            RELATIONSHIP_STASH_CARDINALITY
        )
