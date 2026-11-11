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

"""Structural portability checks against deliberately damaged round trips."""

import copy
import json
from pathlib import Path

import pytest

import grade


@pytest.fixture
def model():
    key = ["customer", "sales_org", "distr_channel", "division"]
    return {
        "version": "0.2.0.dev0",
        "name": "areas",
        "datasets": [
            {
                "name": "areas", "source": "erp.areas",
                "primary_key": key[:], "unique_keys": [["area_code"], ["external_id", "tenant"]],
                "fields": [{"name": "amount", "expression": {"dialects": [
                    {"dialect": "ANSI_SQL", "expression": "amount"},
                ]}}],
            },
            {"name": "sales", "source": "erp.sales"},
        ],
        "relationships": [{
            "name": "sales_to_areas", "from": "sales", "to": "areas",
            "from_columns": key[:], "to_columns": key[:],
        }],
        "metrics": [{"name": "total", "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": "SUM(areas.amount)"},
        ]}}],
    }


def findings(before, after, diagnostics=""):
    return {
        (finding.kind, finding.name): finding
        for finding in grade.grade(before, after, diagnostics).findings
        if finding.kind in {"key", "relationship"}
    }


def outcomes(before, after, diagnostics=""):
    return {key: finding.outcome for key, finding in findings(before, after, diagnostics).items()}


def test_identity_collects_every_structural_declaration(model):
    assert outcomes(model, model) == {
        ("key", "areas.primary_key"): grade.PORTABLE,
        ("key", 'areas.unique_keys["area_code"]'): grade.PORTABLE,
        ("key", 'areas.unique_keys["external_id","tenant"]'): grade.PORTABLE,
        ("relationship", "sales_to_areas"): grade.PORTABLE,
    }


def test_key_and_alternate_key_list_order_is_immaterial(model):
    returned = copy.deepcopy(model)
    returned["datasets"][0]["primary_key"].reverse()
    returned["datasets"][0]["unique_keys"].reverse()
    returned["datasets"][0]["unique_keys"][0].reverse()
    assert set(outcomes(model, returned).values()) == {grade.PORTABLE}


def test_reordering_complete_join_pairs_preserves_predicate(model):
    returned = copy.deepcopy(model)
    returned["relationships"][0]["from_columns"].reverse()
    returned["relationships"][0]["to_columns"].reverse()
    assert outcomes(model, returned)[("relationship", "sales_to_areas")] == grade.PORTABLE


@pytest.mark.parametrize("part", ["from_columns", "to_columns"])
def test_reordering_just_one_endpoint_changes_predicate(model, part):
    returned = copy.deepcopy(model)
    returned["relationships"][0][part].reverse()
    assert outcomes(model, returned)[("relationship", "sales_to_areas")] == grade.REWRITTEN


@pytest.mark.parametrize("part", ["from", "to"])
def test_endpoint_dataset_is_part_of_relationship(model, part):
    returned = copy.deepcopy(model)
    returned["relationships"][0][part] = "another_dataset"
    assert outcomes(model, returned)[("relationship", "sales_to_areas")] == grade.REWRITTEN


@pytest.mark.parametrize("part", ["from_columns", "to_columns"])
def test_unpaired_join_column_is_not_discarded_by_zip(model, part):
    returned = copy.deepcopy(model)
    returned["relationships"][0][part].append("extra")
    assert outcomes(model, returned)[("relationship", "sales_to_areas")] == grade.REWRITTEN


def test_all_structure_can_be_dropped_while_expressions_survive(model):
    returned = copy.deepcopy(model)
    returned["datasets"][0].pop("primary_key")
    returned["datasets"][0].pop("unique_keys")
    returned.pop("relationships")
    report = grade.grade(model, returned)
    assert report.counts() == {grade.PORTABLE: 2, grade.DROPPED_SILENT: 4}
    assert not report.rejected


@pytest.mark.parametrize("part", ["from", "to", "from_columns", "to_columns"])
def test_missing_relationship_property_drops_only_the_relationship(model, part):
    returned = copy.deepcopy(model)
    del returned["relationships"][0][part]
    result = outcomes(model, returned)
    assert result[("relationship", "sales_to_areas")] == grade.DROPPED_SILENT
    assert result[("key", "areas.primary_key")] == grade.PORTABLE


def test_invalid_key_and_relationship_column_sequences_are_dropped(model):
    returned = copy.deepcopy(model)
    returned["datasets"][0]["primary_key"] = "id"
    returned["datasets"][0]["unique_keys"][0] = "area_code"
    returned["relationships"][0]["to_columns"] = [42]
    result = outcomes(model, returned)
    assert result[("key", "areas.primary_key")] == grade.DROPPED_SILENT
    assert result[("key", 'areas.unique_keys["area_code"]')] == grade.DROPPED_SILENT
    assert result[("key", 'areas.unique_keys["external_id","tenant"]')] == grade.PORTABLE
    assert result[("relationship", "sales_to_areas")] == grade.DROPPED_SILENT
    assert grade.ADDED not in result.values()


@pytest.mark.parametrize("diagnostics, expected", [
    ("", grade.DROPPED_SILENT),
    ("areas.primary_key dropped", grade.DROPPED_DIAGNOSED),
    ("other.primary_key dropped", grade.DROPPED_SILENT),
    ("other.areas.primary_key dropped", grade.DROPPED_SILENT),
])
def test_drop_diagnostic_must_identify_the_key(model, diagnostics, expected):
    returned = copy.deepcopy(model)
    returned["datasets"][0].pop("primary_key")
    assert outcomes(model, returned, diagnostics)[("key", "areas.primary_key")] == expected


@pytest.mark.parametrize("diagnostics, expected", [
    ("sales_to_areas dropped", grade.DROPPED_DIAGNOSED),
    ("Dropped relationship sales_to_areas.", grade.DROPPED_DIAGNOSED),
    ("Dropped relationship sales_to_areas.extra.", grade.DROPPED_SILENT),
])
def test_relationship_drop_diagnostic(model, diagnostics, expected):
    returned = copy.deepcopy(model)
    returned.pop("relationships")
    assert outcomes(model, returned, diagnostics)[("relationship", "sales_to_areas")] == expected


def test_truncated_primary_key_is_rewritten_without_guessing_surrogate(model):
    returned = copy.deepcopy(model)
    returned["datasets"][0]["primary_key"] = ["customer"]
    assert outcomes(model, returned)[("key", "areas.primary_key")] == grade.REWRITTEN


@pytest.mark.parametrize("diagnostics, expected", [
    ("", grade.REWRITTEN),
    ("other.primary_key replaced by surrogate area_id", grade.REWRITTEN),
    ("areas.primary_key replaced by surrogate other_id", grade.REWRITTEN),
    ("areas.primary_key replaced by surrogate area_id", grade.PLACEHOLDER),
    ("areas.primary_key replaced by surrogate area_id.", grade.PLACEHOLDER),
    ("areas.primary_key replaced by surrogate area_id.extra.", grade.REWRITTEN),
])
def test_surrogate_needs_scoped_evidence(model, diagnostics, expected):
    returned = copy.deepcopy(model)
    returned["datasets"][0]["primary_key"] = ["area_id"]
    assert outcomes(model, returned, diagnostics)[("key", "areas.primary_key")] == expected


@pytest.mark.parametrize("source_role", ["primary_key", "unique_key"])
def test_key_role_change_is_one_rewritten_finding(model, source_role):
    key = model["datasets"][0].pop("primary_key")
    model["datasets"][0]["unique_keys"] = []
    returned = copy.deepcopy(model)
    if source_role == "primary_key":
        model["datasets"][0]["primary_key"] = key
        returned["datasets"][0]["unique_keys"] = [key]
        name = "areas.primary_key"
    else:
        model["datasets"][0]["unique_keys"] = [key]
        returned["datasets"][0]["primary_key"] = key
        name = 'areas.unique_keys["customer","distr_channel","division","sales_org"]'
    result = findings(model, returned)
    assert result[("key", name)].outcome == grade.REWRITTEN
    assert "role changed" in result[("key", name)].detail
    assert len(result) == 2  # one key and the unchanged relationship, no extra ADDED


def test_unchanged_unique_does_not_hide_dropped_primary(model):
    primary = model["datasets"][0]["primary_key"]
    model["datasets"][0]["unique_keys"].append(primary[:])
    returned = copy.deepcopy(model)
    returned["datasets"][0].pop("primary_key")
    assert outcomes(model, returned)[("key", "areas.primary_key")] == grade.DROPPED_SILENT


def test_compound_unique_key_split_is_rewritten_not_portable(model):
    model["datasets"][0]["unique_keys"] = [["external_id", "tenant"]]
    returned = copy.deepcopy(model)
    returned["datasets"][0]["unique_keys"] = [["external_id"], ["tenant"]]
    result = findings(model, returned)
    name = ("key", 'areas.unique_keys["external_id","tenant"]')
    assert result[name].outcome == grade.REWRITTEN
    assert "split" in result[name].detail
    assert len(result) == 3  # primary, original compound unique, relationship


def test_changed_unique_key_is_rewritten(model):
    model["datasets"][0]["unique_keys"] = [["area_code"]]
    returned = copy.deepcopy(model)
    returned["datasets"][0]["unique_keys"] = [["new_code"]]
    assert outcomes(model, returned)[("key", 'areas.unique_keys["area_code"]')] == grade.REWRITTEN


def test_unique_key_ambiguity_is_reported_as_drops_and_additions(model):
    returned = copy.deepcopy(model)
    returned["datasets"][0]["unique_keys"] = [["new_code"], ["new_id"]]
    result = outcomes(model, returned)
    assert list(result.values()).count(grade.DROPPED_SILENT) == 2
    assert list(result.values()).count(grade.ADDED) == 2


def test_shared_split_parts_are_not_arbitrarily_assigned(model):
    model["datasets"][0]["unique_keys"] = [["a", "b"], ["a", "c"]]
    returned = copy.deepcopy(model)
    returned["datasets"][0]["unique_keys"] = [["a"], ["b"], ["c"]]
    result = outcomes(model, returned)
    assert list(result.values()).count(grade.DROPPED_SILENT) == 2
    assert list(result.values()).count(grade.ADDED) == 3


def test_keys_do_not_match_across_datasets(model):
    returned = copy.deepcopy(model)
    primary = returned["datasets"][0].pop("primary_key")
    returned["datasets"][1]["primary_key"] = primary
    result = outcomes(model, returned)
    assert result[("key", "areas.primary_key")] == grade.DROPPED_SILENT
    assert result[("key", "sales.primary_key")] == grade.ADDED


def test_relationship_rename_recovers_identity_with_review(model):
    returned = copy.deepcopy(model)
    returned["relationships"][0]["name"] = "renamed"
    result = findings(model, returned)
    original = result[("relationship", "sales_to_areas")]
    assert original.outcome == grade.REWRITTEN
    assert "renamed" in original.detail
    assert len(result) == 4


def test_relationship_rename_ambiguity_does_not_guess(model):
    returned = copy.deepcopy(model)
    first = returned["relationships"][0]
    first["name"] = "first"
    second = copy.deepcopy(first)
    second["name"] = "second"
    returned["relationships"].append(second)
    result = outcomes(model, returned)
    assert result[("relationship", "sales_to_areas")] == grade.DROPPED_SILENT
    assert result[("relationship", "first")] == grade.ADDED
    assert result[("relationship", "second")] == grade.ADDED


def test_structures_are_not_repaired_by_expression(model):
    returned = copy.deepcopy(model)
    returned["relationships"] = []
    returned["metrics"].append({
        "name": "sales_to_areas",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "sales_to_areas"}]},
    })
    result = outcomes(model, returned)
    assert result[("relationship", "sales_to_areas")] == grade.DROPPED_SILENT


def test_structure_only_documents_can_be_graded_and_rejected():
    original = {"datasets": [{"name": "t", "source": "t", "primary_key": ["id"]}]}
    assert grade.grade(original, original).counts() == {grade.PORTABLE: 1}
    report = grade.grade(original, None)
    assert report.rejected
    assert report.counts() == {grade.REJECTED: 1}


def test_structural_grading_does_not_mutate_documents(model):
    before = copy.deepcopy(model)
    grade.grade(model, model)
    assert model == before


def test_unique_key_names_do_not_collide_on_commas():
    doc = {"datasets": [{"name": "t", "unique_keys": [["a,b"], ["a", "b"]]}]}
    assert grade.grade(doc, doc).counts() == {grade.PORTABLE: 2}


def test_cli_json_contains_structural_findings(model, tmp_path, capsys):
    source = tmp_path / "source.json"
    returned = tmp_path / "returned.json"
    source.write_text(json.dumps(model), encoding="utf-8")
    changed = copy.deepcopy(model)
    changed["relationships"][0]["to_columns"].reverse()
    returned.write_text(json.dumps(changed), encoding="utf-8")
    assert grade.main(["--input", str(source), "--returned", str(returned), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    matches = {(f["kind"], f["name"]): f["outcome"] for f in report["findings"]}
    assert matches[("relationship", "sales_to_areas")] == grade.REWRITTEN


def test_four_part_fixture_is_in_manifest_and_fully_graded():
    root = Path(__file__).resolve().parents[1]
    manifest = grade._load(str(root / "manifest.yaml"))
    rung = next(r for r in manifest["rungs"] if r["id"] == "K0")
    doc = grade._load(str(root / rung["variants"][0]["file"]))
    collected = grade._collect(doc)
    report = grade.grade(doc, doc)
    assert all(f.outcome == grade.PORTABLE for f in report.findings)
    structural = {key for key, element in collected.items() if element.declaration is not None}
    assert structural == {(e["kind"], e["name"]) for e in rung["elements_under_test"]}
    assert len(structural) == 4
    damaged = copy.deepcopy(doc)
    for dataset in damaged["datasets"]:
        dataset.pop("primary_key", None)
        dataset.pop("unique_keys", None)
    damaged.pop("relationships")
    dropped = [f for f in grade.grade(doc, damaged).findings if f.outcome == grade.DROPPED_SILENT]
    assert {(f.kind, f.name) for f in dropped} == structural
