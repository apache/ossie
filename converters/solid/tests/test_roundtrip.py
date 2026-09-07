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

"""Round-trip fidelity in both directions.

`Solid -> Apache Ossie -> Solid` is exact: everything Solid's format can express either
maps onto an Apache Ossie core field or is preserved in `custom_extensions[SOLID]`.

`Apache Ossie -> Solid -> Apache Ossie` is lossy by construction, because Solid's format
has no slot for several Apache Ossie constructs (unique keys, foreign-vendor extensions,
computed dimensions, multi-dialect expressions). Those losses are asserted explicitly
here, so a regression that widens them fails the suite.
"""

import pytest
import yaml
from conftest import (
    by_name,
    convert_quietly,
    example,
    fixture,
    model_of,
    normalized_yaml,
    solid_model_of,
)

from ossie_solid import convert_ossie_to_solid, convert_solid_to_ossie

SOLID_FIXTURES = ["tpcds_solid.yaml", "databricks_solid.yaml", "bigquery_solid.yaml"]



@pytest.mark.parametrize("name", SOLID_FIXTURES)
def test_solid_to_ossie_to_solid_is_exact(name):
    original = fixture(name)
    ossie, _ = convert_quietly(convert_solid_to_ossie, original)
    restored, _ = convert_quietly(convert_ossie_to_solid, ossie)
    assert normalized_yaml(restored) == normalized_yaml(original)


@pytest.mark.parametrize("name", SOLID_FIXTURES)
def test_solid_round_trip_is_stable_on_a_second_pass(name):
    """A second round trip changes nothing a first one did not."""
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture(name))
    once, _ = convert_quietly(convert_ossie_to_solid, ossie)
    twice_ossie, _ = convert_quietly(convert_solid_to_ossie, once)
    twice, _ = convert_quietly(convert_ossie_to_solid, twice_ossie)
    assert normalized_yaml(twice) == normalized_yaml(once)
    assert yaml.safe_load(twice_ossie) == yaml.safe_load(ossie)


@pytest.mark.parametrize("name", SOLID_FIXTURES)
def test_the_intermediate_ossie_model_is_schema_valid(name, assert_valid_ossie):
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture(name))
    assert_valid_ossie(ossie)


def test_ossie_to_solid_to_ossie_stays_schema_valid(assert_valid_ossie):
    solid, _ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    ossie, _ = convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")
    assert_valid_ossie(ossie)


def test_ossie_to_solid_to_ossie_preserves_the_model_shape():
    original = model_of(example("tpcds_semantic_model.yaml"))
    solid, _ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    restored, _ = convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")
    restored = model_of(restored)

    assert restored["name"] == original["name"]
    assert [d["source"] for d in restored["datasets"]] == [
        d["source"] for d in original["datasets"]
    ]
    assert [d["name"] for d in restored["datasets"]] == [
        d["name"] for d in original["datasets"]
    ]
    assert sorted(by_name(restored["metrics"])) == sorted(by_name(original["metrics"]))
    for dataset in original["datasets"]:
        assert by_name(restored["datasets"])[dataset["name"]].get("primary_key") == (
            dataset.get("primary_key")
        )


def test_ossie_to_solid_to_ossie_preserves_relationship_semantics():
    original = model_of(example("tpcds_semantic_model.yaml"))
    solid, _ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    restored = model_of(
        convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")[0]
    )

    def edges(model):
        return {
            (r["from"], r["to"], tuple(r["from_columns"]), tuple(r["to_columns"]))
            for r in model["relationships"]
        }

    assert edges(restored) == edges(original)


def test_ossie_to_solid_to_ossie_preserves_metric_expressions():
    original = model_of(example("tpcds_semantic_model.yaml"))
    solid, _ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    restored = model_of(
        convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")[0]
    )

    def expressions(model):
        return {
            m["name"]: m["expression"]["dialects"][0]["expression"]
            for m in model["metrics"]
        }

    before, after = expressions(original), expressions(restored)
    # Single-table metrics survive verbatim. Cross-table ones lose their qualifiers,
    # because Solid's format cannot record which table each column belongs to.
    assert after["total_sales"] == before["total_sales"]
    assert after["total_profit"] == before["total_profit"]
    assert after["customer_lifetime_value"] == (
        "SUM(ss_ext_sales_price) / COUNT(DISTINCT c_customer_sk)"
    )


def test_the_documented_losses_of_an_ossie_export_are_exactly_these():
    """Pin the known one-way losses, so a regression that adds another one fails."""
    _, going = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    assert {w.split("]")[0].lstrip("[") for w in going} == {
        "model", "dataset", "field", "metric", "relationship"
    }
    assert sum("custom_extensions for" in w for w in going) == 1
    assert sum("unique_keys have no Solid equivalent" in w for w in going) == 1
    assert sum("computed field" in w for w in going) == 1
    assert sum("no datatype" in w for w in going) == 2
    # Solid types a metric by evaluating its formula, and its relationships carry no
    # free text, so a declared metric datatype and a relationship annotation both have
    # nowhere to land.
    assert sum("datatype 'Decimal' has no Solid equivalent" in w for w in going) == 5
    assert sum("ai_context.synonyms has no Solid equivalent" in w for w in going) == 4
    assert len(going) == 14


def test_reimporting_that_export_only_loses_the_cross_table_qualifiers():
    solid, _ = convert_quietly(
        convert_ossie_to_solid, example("tpcds_semantic_model.yaml")
    )
    _, coming = convert_quietly(convert_solid_to_ossie, solid, dialect="ANSI_SQL")
    assert all("spans 2 tables" in w for w in coming), coming
    assert len(coming) == 2


def test_a_solid_round_trip_warns_only_about_unqualifiable_metrics():
    _, going = convert_quietly(convert_solid_to_ossie, fixture("tpcds_solid.yaml"))
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture("tpcds_solid.yaml"))
    _, coming = convert_quietly(convert_ossie_to_solid, ossie)
    assert all("spans 2 tables" in w for w in going), going
    assert coming == [], coming


@pytest.mark.parametrize(
    ("name", "dialect"),
    [
        ("tpcds_solid.yaml", "SNOWFLAKE"),
        ("databricks_solid.yaml", "DATABRICKS"),
        ("bigquery_solid.yaml", "BIGQUERY"),
    ],
)
def test_the_dialect_survives_a_round_trip(name, dialect):
    ossie, _ = convert_quietly(convert_solid_to_ossie, fixture(name))
    model = model_of(ossie)
    for field in model["datasets"][0]["fields"]:
        assert field["expression"]["dialects"][0]["dialect"] == dialect
    restored, _ = convert_quietly(convert_ossie_to_solid, ossie)
    again, _ = convert_quietly(convert_solid_to_ossie, restored)
    assert model_of(again)["datasets"][0]["fields"][0]["expression"]["dialects"][0][
        "dialect"
    ] == dialect


def test_an_empty_description_is_normalized_away():
    """A documented, deliberate difference.

    solid-server renders `manual_description` from a whitespace-only value as an empty
    block scalar, which parses back as `''`. That carries no information, so the
    converter drops it rather than round-tripping the emptiness.
    """
    solid = yaml.safe_load(fixture("databricks_solid.yaml"))
    solid["semantic_model"]["tables"][0]["dimensions"][0]["manual_description"] = ""
    ossie, _ = convert_quietly(convert_solid_to_ossie, yaml.safe_dump(solid))
    assert "ai_context" not in model_of(ossie)["datasets"][0]["fields"][0]
    restored, _ = convert_quietly(convert_ossie_to_solid, ossie)
    column = solid_model_of(restored)["tables"][0]["dimensions"][0]
    assert "manual_description" not in column
