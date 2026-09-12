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

"""One fixture per Cube data-model feature, each asserted four ways.

The two whole-model fixtures cover the common shapes well but say nothing about the
long tail of the data model, which is where the silent defects were: a `case` dimension
converted to an expression naming a column that did not exist, a `switch` dimension
came back as a plain string one, a computed primary key moved onto a synthesized
dimension reading a nonexistent column, and a bare YAML date in an access policy
aborted the conversion. None of those were visible to a field-level assertion.

Every fixture here is a *valid Cube model* -- verified by compiling it -- and each is
put through the same four questions:

1. does it convert at all, and what does the converter say it could not carry;
2. is the Ossie it produces valid per the spec's own validator;
3. does `Cube -> Ossie -> Cube` reproduce it structurally;
4. does Cube itself still compile the result.

Layout follows Cube's own test suite, which keeps a fixture per feature
(`hierarchies.yml`, `switch-dimension.yml`, `folders.yml`, `calendar_orders.yml`).
Adding a feature means adding a fixture; the four assertions come for free.
"""

import pathlib

import pytest
from _cube_gate import (
    assert_cube_compiles,
    assert_ossie_is_valid,
    cube_gate,
    validator_gate,
)
from _util import by_name, expr_of, model_of, parse_files, stash_of

from ossie_cube import IssueType, convert_cube_to_ossie, convert_ossie_to_cube

_FEATURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "features"
_FIXTURES = sorted(p.name for p in _FEATURES.glob("*.yml"))


def _load(name):
    """One fixture, keyed the way a Cube model directory would key it."""
    return {f"model/cubes/{name}": (_FEATURES / name).read_text()}


def _roundtrip(name):
    files = _load(name)
    ossie, issues = convert_cube_to_ossie(files)
    back, _ = convert_ossie_to_cube(ossie)
    return files, ossie, back, issues


# --- the four questions, asked of every fixture ----------------------------------

@pytest.mark.parametrize("name", _FIXTURES)
def test_every_feature_converts(name):
    _, ossie, _, _ = _roundtrip(name)
    assert model_of(ossie)["datasets"]


@pytest.mark.parametrize("name", _FIXTURES)
def test_every_feature_roundtrips_structurally(name):
    files, _, back, _ = _roundtrip(name)
    assert parse_files(back) == parse_files(files)


@validator_gate
@pytest.mark.parametrize("name", _FIXTURES)
def test_every_feature_produces_valid_ossie(name):
    _, ossie, _, _ = _roundtrip(name)
    assert_ossie_is_valid(ossie, name)


@cube_gate
@pytest.mark.parametrize("name", _FIXTURES)
def test_every_feature_still_compiles_in_cube(name):
    """The fixture compiles by construction; what this asks is whether the *converted*
    model still does. A round trip can reproduce a model structurally and still emit
    something Cube refuses -- a `sql` alongside `case`, an unescaped brace, two members
    of one name."""
    files, _, back, _ = _roundtrip(name)
    assert_cube_compiles(files, f"{name} (as committed)")
    assert_cube_compiles(back, f"{name} (after a round trip)")


# --- what each feature is expected to do -----------------------------------------

def test_a_case_dimension_becomes_an_ossie_case_expression():
    _, ossie, _, _ = _roundtrip("conditional_dimensions.yml")
    fields = by_name(by_name(model_of(ossie)["datasets"])["products"]["fields"])
    assert expr_of(fields["size"]) == (
        "CASE WHEN size_value = 'xl-en' THEN 'xl' "
        "WHEN size_value = 'xxl' THEN 'it''s xxl' ELSE 'Unknown' END")
    # A dynamic label is an expression, not a literal.
    assert expr_of(fields["localized_size"]) == (
        "CASE WHEN size_value = 'xl' THEN english_size END")


def test_a_switch_dimension_has_no_ossie_field():
    _, ossie, _, issues = _roundtrip("conditional_dimensions.yml")
    dataset = by_name(model_of(ossie)["datasets"])["products"]
    assert "currency" not in by_name(dataset["fields"])
    # It rides on the stash with its position instead, and that is reported.
    parked = stash_of(dataset)["extra_dimensions"]
    assert [p["dimension"]["name"] for p in parked] == ["currency"]
    assert any("switch" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))


def test_a_sub_query_dimension_is_parked_not_emitted_as_a_field():
    """`{orders.count}` reads a *measure* through a correlated subquery. Emitting
    the flattened reference as an Ossie expression claimed a column no dataset has
    -- text that reads as valid SQL and computes nothing anywhere -- so the
    dimension rides whole on the stash, the same protocol switch dimensions use,
    and comes back at its original position."""
    files, ossie, back, issues = _roundtrip("sub_query_dimension.yml")
    assert any("sub_query" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))
    products = by_name(model_of(ossie)["datasets"])["products"]
    assert "order_count" not in by_name(products.get("fields") or [])
    parked = stash_of(products)["extra_dimensions"]
    assert parked[0]["dimension"]["name"] == "order_count"
    # And it returns to Cube exactly as written.
    cube = parse_files(back)["model/cubes/sub_query_dimension.yml"]["cubes"]
    dims = by_name(by_name(cube)["products"]["dimensions"])
    assert dims["order_count"] == {
        "name": "order_count", "sql": "{orders.count}", "type": "number",
        "sub_query": True}


def test_a_computed_primary_key_returns_to_its_own_dimension():
    files, ossie, back, _ = _roundtrip("computed_primary_key.yml")
    dataset = by_name(model_of(ossie)["datasets"])["order_lines"]
    assert dataset["primary_key"] == ["line_key"]
    # Recorded, because the Ossie document alone cannot tell a dimension name from a
    # column name afterwards.
    assert stash_of(dataset)["computed_primary_key"] == ["line_key"]
    cube = parse_files(back)["model/cubes/computed_primary_key.yml"]["cubes"][0]
    keys = [d for d in cube["dimensions"] if d.get("primary_key")]
    assert [d["name"] for d in keys] == ["line_key"]
    assert keys[0]["sql"].startswith("CONCAT(")


def test_a_multi_stage_measure_is_parked_with_its_position():
    _, ossie, _, issues = _roundtrip("measure_variants.yml")
    dataset = by_name(model_of(ossie)["datasets"])["sales"]
    parked = [p["measure"]["name"] for p in stash_of(dataset)["extra_measures"]]
    # A rolling window, an inner GROUP BY and a time shift all compute over a grain
    # other than the query's. Emitting the bare aggregate would have been worse than
    # dropping it: `revenue_last_3_months` came out as `SUM(sales.amount)` -- the exact
    # expression of the ordinary `revenue` measure beside it.
    assert set(parked) == {"revenue_last_3_months", "revenue_by_region",
                           "revenue_prior_year"}
    assert issues.of_type(IssueType.MULTI_STAGE_MEASURE_PARKED)
    assert "revenue_last_3_months" not in by_name(model_of(ossie)["metrics"])


def test_a_filtered_measure_folds_its_filter_into_the_expression():
    _, ossie, _, _ = _roundtrip("measure_variants.yml")
    metric = by_name(model_of(ossie)["metrics"])["completed_revenue"]
    assert expr_of(metric) == (
        "SUM(CASE WHEN (sales.status = 'completed') THEN sales.amount END)")


def test_an_access_policy_keeps_its_security_context_and_dates():
    """Two things must not be touched: a `securityContext` reference is Cube's own
    interpolation, and a bare YAML date is not JSON-serializable -- it used to abort
    the conversion with a raw TypeError."""
    files, ossie, back, _ = _roundtrip("access_policy.yml")
    policy = stash_of(by_name(model_of(ossie)["datasets"])["orders"])[
        "cube_extras"]["access_policy"]
    values = policy[0]["row_level"]["filters"][1]["values"]
    assert "{ securityContext.currentDate }" in values
    assert "2022-01-01" in values


def test_a_bare_yaml_date_is_normalized_rather_than_crashing():
    """PyYAML resolves an unquoted `2022-01-01` to a `datetime.date`, which the JSON
    stash cannot hold -- it used to abort the conversion with a raw TypeError. It
    becomes an ISO string, which is what Cube compares against anyway: every value in a
    policy filter reaches SQL as text."""
    files = _load("access_policy.yml")
    bare = {k: v.replace("- '2022-01-01'", "- 2022-01-01")
            for k, v in files.items()}
    ossie, _ = convert_cube_to_ossie(bare)
    policy = stash_of(by_name(model_of(ossie)["datasets"])["orders"])[
        "cube_extras"]["access_policy"]
    assert policy[0]["row_level"]["filters"][1]["values"][0] == "2022-01-01"


def test_view_curation_survives_untouched():
    _, ossie, back, _ = _roundtrip("view_curation.yml")
    model = model_of(ossie)
    # The view supplies the model's identity, and its curation has no Ossie form.
    assert model["name"] == "sales"
    view = stash_of(model)["views"]["sales"]
    assert view["folders"][0]["name"] == "Attributes"
    assert any(entry.get("prefix") for entry in view["cubes"])


@pytest.mark.parametrize("name,keys", [
    ("dimension_display.yml", ["format", "currency", "order", "mask", "public"]),
    ("time_granularities.yml", ["granularities"]),
    ("hierarchies_and_segments.yml", ["hierarchies", "segments"]),
    ("pre_aggregations.yml", ["pre_aggregations"]),
])
def test_cube_only_keys_are_stashed_rather_than_dropped(name, keys):
    """Everything here is legitimately Cube-specific -- presentation, physical
    layout, access control -- so the right behaviour is to carry it in the stash and
    leave the Ossie document clean, not to approximate it."""
    _, ossie, back, _ = _roundtrip(name)
    emitted = parse_files(back)[f"model/cubes/{name}"]["cubes"][0]
    flat = str(emitted)
    for key in keys:
        assert key in flat, f"{key} did not survive the round trip"
