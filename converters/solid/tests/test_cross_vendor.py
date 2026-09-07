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

"""Interop with Apache Ossie models this converter did not produce.

Import (Solid -> Apache Ossie) is covered by round-trip equality, because both ends of
that trip are Solid's own format. Export (Apache Ossie -> Solid) has no such anchor: the
models it will really be handed come from *other vendors' converters*, and those look
nothing like the ones this converter emits -- no SOLID stash, no `dimension` blocks,
usually no `datatype`, metrics written against bare column names.

Two complementary passes cover that:

1. A **sweep** over the Ossie fixtures the other converters in this repository ship.
   It asserts only the robust contract -- every model converts, and the result is a
   well-formed Solid model that re-imports schema-valid. It deliberately pins no
   warning counts: those fixtures belong to other converters, and each converter's CI
   is path-filtered to its own directory, so a pin here would break on a main-branch
   push from a PR that never ran this suite.

2. Exact pins against `foreign_ossie.yaml`, a fixture *this* converter owns that
   reproduces the same constructs. That is where the interop gaps are recorded
   precisely, so a change in any of them shows up as a diff in this file.
"""

import json

import pytest
import yaml
from conftest import (
    CONVERTERS,
    convert_quietly,
    fixture,
    model_of,
    solid_model_of,
)

from ossie_solid import convert_ossie_to_solid, convert_solid_to_ossie


def _foreign_ossie_fixtures():
    """Every Apache Ossie document among the other converters' fixtures.

    Discovered rather than listed, so a fixture that is renamed or removed upstream
    drops out of the sweep instead of failing it.
    """
    found = []
    for path in sorted(CONVERTERS.glob("*/tests/fixtures/*.yaml")):
        if path.parts[-4] == "solid":
            continue
        try:
            document = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        # An Apache Ossie document keys `semantic_model` to a list; Solid's and every
        # other vendor's native format does not.
        if isinstance(document, dict) and isinstance(
            document.get("semantic_model"), list
        ):
            found.append(path)
    return found


FOREIGN = _foreign_ossie_fixtures()


def _fixture_id(path):
    return f"{path.parts[-4]}/{path.name}"


@pytest.mark.skipif(not FOREIGN, reason="no other converters ship Ossie fixtures")
@pytest.mark.parametrize("path", FOREIGN, ids=_fixture_id)
def test_another_vendors_model_converts_to_a_well_formed_solid_model(path):
    """The bar is a Solid model Solid could actually read, not a warning-free one.

    Losses are expected here and are reported as warnings; what must not happen is a
    hard failure or a structurally invalid Solid document.
    """
    solid, _ = convert_quietly(convert_ossie_to_solid, path.read_text())
    model = solid_model_of(solid)

    assert model["name"]
    assert model["tables"], "a Solid model needs at least one table"
    for table in model["tables"]:
        assert table["name"], "every Solid table needs its fully-qualified name"
        # Solid always emits `facts`, as [] when a table has none.
        assert "facts" in table
        for column in (table.get("dimensions") or []) + table["facts"]:
            assert column["name"]
    for metric in model.get("metrics") or []:
        assert metric["name"] and metric["expression"]
    for relationship in model.get("relationships") or []:
        assert relationship["left_table"] and relationship["right_table"]
        keys = relationship["join_keys"]
        assert len(keys["left"]) == len(keys["right"])


@pytest.mark.skipif(not FOREIGN, reason="no other converters ship Ossie fixtures")
@pytest.mark.parametrize("path", FOREIGN, ids=_fixture_id)
def test_reimporting_another_vendors_model_is_schema_valid(path, assert_valid_ossie):
    """The full hop: their Apache Ossie -> Solid -> Apache Ossie, still valid."""
    solid, _ = convert_quietly(convert_ossie_to_solid, path.read_text())
    ossie, _ = convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")
    assert_valid_ossie(ossie)


@pytest.mark.skipif(not FOREIGN, reason="no other converters ship Ossie fixtures")
def test_the_sweep_actually_found_the_other_converters():
    """Guard against the discovery silently matching nothing and the sweep passing.

    A lower bound, not an exact count, so a converter added or removed upstream does
    not fail this suite.
    """
    vendors = {path.parts[-4] for path in FOREIGN}
    assert len(vendors) >= 3, f"only found fixtures for {vendors}"


# --- exact pins, against a fixture this converter owns -----------------------------


@pytest.fixture(scope="module")
def foreign():
    solid, warnings_ = convert_quietly(
        convert_ossie_to_solid, fixture("foreign_ossie.yaml")
    )
    return solid_model_of(solid), warnings_


def test_a_foreign_model_converts_to_the_expected_solid_shape(foreign):
    model, _ = foreign
    assert model["name"] == "foreign_retail_model"
    assert [t["name"] for t in model["tables"]] == [
        "tpcds.public.store_sales",
        "tpcds.public.date_dim",
    ]
    # No `dimension` block anywhere, so the split falls back to Solid's type rule:
    # a numeric type is a fact, everything else a dimension. With no datatype at all,
    # a column cannot be shown to be numeric and so lands in `dimensions`.
    store_sales = model["tables"][0]
    assert [c["name"] for c in store_sales["facts"]] == ["ss_net_paid"]
    assert [c["name"] for c in store_sales["dimensions"]] == [
        "ss_item_sk",
        "ss_ticket_number",
        "ss_sold_date_sk",
        "ticket_number",
    ]
    assert store_sales["primary_key"] == "ss_item_sk, ss_ticket_number"


def test_the_documented_interop_gaps_are_exactly_these(foreign):
    """The record of what is lost bringing another vendor's model into Solid.

    Each line is a known gap, not a defect to be fixed here. Widening or narrowing any
    of them should be a visible diff in this test.
    """
    _, warnings_ = foreign
    counts = {
        # No `datatype` -> no Solid `type`. The single largest gap: Solid's columns are
        # typed from the warehouse catalog, and an offline converter has no catalog.
        "has no datatype": 5,
        # A field renamed relative to its column keeps the alias and loses the column
        # it actually reads, because a Solid column IS a catalog column.
        "is a computed field": 1,
        # Solid has no slot for any of these.
        "label 'Year' has no Solid equivalent": 1,
        "unique_keys have no Solid equivalent": 1,
        "datatype 'Decimal' has no Solid equivalent": 1,
        "ai_context.instructions, ai_context.synonyms have no Solid equivalent": 1,
        "custom_extensions for": 4,
        # Solid resolves a metric's columns through `tables`, and a metric written
        # against bare column names carries nothing to resolve them from.
        "names no table": 2,
    }
    for text, expected in counts.items():
        assert sum(text in w for w in warnings_) == expected, text
    assert len(warnings_) == sum(counts.values()) == 16


def test_a_renamed_field_loses_the_column_it_reads(foreign):
    """Pinned because it is the sharpest gap: the output names a column that is not
    in the warehouse. `ticket_number` reads `ss_ticket_number`, but Solid columns map
    to catalog columns, so only the alias survives."""
    model, warnings_ = foreign
    renamed = {c["name"] for c in model["tables"][0]["dimensions"]}
    assert "ticket_number" in renamed
    assert any(
        "ticket_number' is a computed field (`ss_ticket_number`)" in w
        for w in warnings_
    )


def test_a_bare_column_metric_reaches_solid_with_no_tables(foreign):
    """Also pinned as a gap: Solid needs `tables` to resolve a formula's columns.

    Only a dataset-qualified reference identifies its owner, and another vendor has no
    reason to qualify -- Apache Ossie's own TPC-DS example does, which is why this does
    not show up against it.
    """
    model, _ = foreign
    assert {m["name"]: m["tables"] for m in model["metrics"]} == {
        "revenue": [],
        "order_count": [],
    }


def test_a_foreign_model_survives_the_round_trip_back_to_ossie(assert_valid_ossie):
    solid, _ = convert_quietly(convert_ossie_to_solid, fixture("foreign_ossie.yaml"))
    ossie, _ = convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")
    assert_valid_ossie(ossie)

    original = model_of(fixture("foreign_ossie.yaml"))
    restored = model_of(ossie)
    assert restored["name"] == original["name"]
    assert [d["source"] for d in restored["datasets"]] == [
        d["source"] for d in original["datasets"]
    ]
    # The relationship survives with its direction intact: `store_sales` joins
    # `date_dim` on the latter's primary key, so it is the many side.
    edge = restored["relationships"][0]
    assert (edge["from"], edge["to"]) == ("store_sales", "date_dim")


def test_the_dialect_recorded_on_a_reimported_foreign_model_is_explicit():
    """A foreign model has no stash, so the re-import records what it resolved to."""
    solid, _ = convert_quietly(convert_ossie_to_solid, fixture("foreign_ossie.yaml"))
    ossie, _ = convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")
    stash = next(
        json.loads(ext["data"])
        for ext in model_of(ossie)["custom_extensions"]
        if ext["vendor_name"] == "SOLID"
    )
    assert stash["dialect"] == "ANSI_SQL"
