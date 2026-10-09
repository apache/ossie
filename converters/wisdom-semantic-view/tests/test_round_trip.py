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

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from ossie_wisdom_semantic_view.cli import main
from ossie_wisdom_semantic_view.enrich import apply_enrichment, load_enrichment
from ossie_wisdom_semantic_view.model import dump_ossie, load_ossie
from ossie_wisdom_semantic_view.pipeline import convert_finance
from ossie_wisdom_semantic_view.semantic_view import (
    dump_semantic_view,
    load_semantic_view,
    ossie_to_semantic_view,
    semantic_view_to_ossie,
)
from ossie_wisdom_semantic_view.wisdom import dump_wisdom, load_wisdom, ossie_to_wisdom, wisdom_to_ossie

ROOT = Path(__file__).resolve().parents[1]
FINANCE = ROOT / "tests" / "fixtures" / "finance"
REPO = ROOT.parent.parent
_HEADER_END = "# under the License.\n"


def fixture_body(path: Path) -> str:
    text = path.read_text()
    if path.suffix == ".json" or not text.startswith("# Licensed to the Apache Software Foundation"):
        return text
    marker = text.find(_HEADER_END)
    assert marker != -1, path
    body = text[marker + len(_HEADER_END) :]
    if body.startswith("\n"):
        body = body[1:]
    return body


def test_before_file_round_trips_without_enrichment():
    text = fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml")
    view = load_semantic_view(text)
    again = dump_semantic_view(ossie_to_semantic_view(semantic_view_to_ossie(view)))
    assert again == text


def test_pipeline_matches_goldens():
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    assert artifacts.ossie_yaml == fixture_body(FINANCE / "ossie" / "cfo_cockpit.yaml")
    assert artifacts.ossie_enriched_yaml == fixture_body(FINANCE / "ossie" / "cfo_cockpit.enriched.yaml")
    assert artifacts.wisdom_imported_json == (FINANCE / "wisdom" / "cfo_cockpit.imported.json").read_text()
    assert artifacts.wisdom_enriched_json == (FINANCE / "wisdom" / "cfo_cockpit.enriched.json").read_text()
    assert artifacts.semantic_after_yaml == fixture_body(FINANCE / "snowflake" / "cfo_cockpit.after.yaml")


def test_enriched_wisdom_round_trips_through_ossie():
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    enriched = load_wisdom(artifacts.wisdom_enriched_json)
    assert dump_wisdom(ossie_to_wisdom(wisdom_to_ossie(enriched))) == artifacts.wisdom_enriched_json
    assert dump_ossie(wisdom_to_ossie(enriched)) == artifacts.ossie_enriched_yaml


def test_enrichment_survives_the_wisdom_side():
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    after = yaml.safe_load(artifacts.semantic_after_yaml)
    revenue = next(metric for metric in after["metrics"] if metric["name"] == "revenue")
    assert revenue["expr"] == "SUM(bookings.amount)"
    assert revenue["synonyms"] == ["revenue", "net revenue", "board revenue"]
    assert after["module_custom_instructions"]["sql_generation"] == (
        "Board revenue is recognized bookings. It is not cash collected. "
        "The fiscal year starts in February."
    )
    booking_amount = next(metric for metric in after["metrics"] if metric["name"] == "booking_amount")
    assert "synonyms" not in booking_amount
    knowledge = json.loads(artifacts.wisdom_enriched_json)["domain"]["zsheet_json"]["knowledge"]
    assert [item["name"] for item in knowledge] == ["Synonyms for revenue", "Board revenue"]
    assert "BOOKINGS" not in artifacts.semantic_after_yaml


def test_ossie_goldens_match_schema():
    schema = json.loads((REPO / "core-spec" / "ossie-schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for name in ("cfo_cockpit.yaml", "cfo_cockpit.enriched.yaml"):
        document = load_ossie((FINANCE / "ossie" / name).read_text())
        validator.validate(document)
        assert document["version"] == "0.2.0.dev0"


def test_synonym_sets_stay_empty():
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    for text in (artifacts.wisdom_imported_json, artifacts.wisdom_enriched_json):
        export = json.loads(text)
        assert export["synonym_sets"] == {"ref": None, "items_json": "{}"}


def test_imported_knowledge_is_empty_and_enrichment_adds_the_metric():
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    imported = json.loads(artifacts.wisdom_imported_json)
    enriched = json.loads(artifacts.wisdom_enriched_json)
    assert imported["domain"]["zsheet_json"]["knowledge"] == []
    measures = enriched["tables"][0]["zsheet_json"]["measures"]
    assert [item["name"] for item in measures] == ["booking_amount", "revenue"]


def test_other_relationship_is_not_inferred(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["relationships"][0]["left_table"] = "fiscal_calendar"
    view["relationships"][0]["right_table"] = "bookings"
    with pytest.raises(SystemExit) as caught:
        semantic_view_to_ossie(view)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "bookings_to_fiscal_calendar"


def test_unknown_key_exits(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["verified_queries"] = []
    with pytest.raises(SystemExit) as caught:
        semantic_view_to_ossie(view)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "verified_queries"


def test_unsupported_expression_exits(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["metrics"][0]["expr"] = "AVG(bookings.amount)"
    with pytest.raises(SystemExit) as caught:
        semantic_view_to_ossie(view)
    assert caught.value.code == 1
    assert "AVG(bookings.amount)" in capsys.readouterr().err


def test_other_data_type_exits(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["tables"][0]["facts"][0]["data_type"] = "FLOAT"
    with pytest.raises(SystemExit) as caught:
        semantic_view_to_ossie(view)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "FLOAT"


def test_enrichment_rejects_preexisting_sql_generation(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["module_custom_instructions"] = {
        "sql_generation": "Board revenue is recognized bookings."
    }
    export = ossie_to_wisdom(semantic_view_to_ossie(view))
    assert [item["name"] for item in export["domain"]["zsheet_json"]["knowledge"]] == ["Board revenue"]
    enrichment = load_enrichment(fixture_body(FINANCE / "wisdom" / "enrichment.yaml"))
    with pytest.raises(SystemExit) as caught:
        apply_enrichment(export, enrichment)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "Board revenue already exists"
    assert [item["name"] for item in export["domain"]["zsheet_json"]["knowledge"]] == ["Board revenue"]


def test_null_synonym_sets_are_empty_and_non_dict_items_exit(capsys):
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    original = load_wisdom(artifacts.wisdom_imported_json)
    nulled = load_wisdom(artifacts.wisdom_imported_json)
    nulled["synonym_sets"] = None
    assert dump_ossie(wisdom_to_ossie(nulled)) == dump_ossie(wisdom_to_ossie(original))

    listed = load_wisdom(artifacts.wisdom_imported_json)
    listed["synonym_sets"]["items_json"] = "[]"
    with pytest.raises(SystemExit) as caught:
        wisdom_to_ossie(listed)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "synonym_sets.items_json"


def test_newline_synonym_exits(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["metrics"][0]["synonyms"] = ["board\nrevenue"]
    with pytest.raises(SystemExit) as caught:
        semantic_view_to_ossie(view)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "synonyms"


def test_duplicate_synonym_exits(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["metrics"][0]["synonyms"] = ["revenue", "revenue"]
    with pytest.raises(SystemExit) as caught:
        semantic_view_to_ossie(view)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "synonyms"


def test_empty_synonyms_are_absent_after_import():
    original = fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml")
    view = load_semantic_view(original)
    view["metrics"][0]["synonyms"] = []
    model = semantic_view_to_ossie(view)
    assert "ai_context" not in model["metrics"][0]
    assert model == semantic_view_to_ossie(load_semantic_view(original))
    assert dump_semantic_view(ossie_to_semantic_view(model)) == original


def test_knowledge_id_collision_exits(capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    model = semantic_view_to_ossie(view)
    expression = model["metrics"][0]["expression"]
    model["metrics"] = [
        {
            "name": "net-revenue",
            "description": "Net revenue",
            "expression": expression,
            "ai_context": {"synonyms": ["net revenue"]},
        },
        {
            "name": "net_revenue",
            "description": "Net revenue underscore",
            "expression": expression,
            "ai_context": {"synonyms": ["net_revenue"]},
        },
    ]
    with pytest.raises(SystemExit) as caught:
        ossie_to_wisdom(model)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == (
        "duplicate knowledge id: ET_UNSTRUCTURED_KNOWLEDGE_synonyms_for_net_revenue "
        "(Synonyms for net-revenue, Synonyms for net_revenue)"
    )


def test_nonempty_synonym_sets_exit(capsys):
    artifacts = convert_finance(
        fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
        fixture_body(FINANCE / "wisdom" / "enrichment.yaml"),
    )
    export = load_wisdom(artifacts.wisdom_imported_json)
    export["synonym_sets"]["items_json"] = '{"synonyms":["revenue"]}'
    with pytest.raises(SystemExit) as caught:
        wisdom_to_ossie(export)
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "synonym_sets.items_json"


def test_cli_both_directions(tmp_path):
    domain = tmp_path / "domain.json"
    assert main(
        [
            "to-wisdom",
            str(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"),
            "-o",
            str(domain),
            "--enrich",
            str(FINANCE / "wisdom" / "enrichment.yaml"),
        ]
    ) == 0
    assert domain.read_text() == (FINANCE / "wisdom" / "cfo_cockpit.enriched.json").read_text()
    written = tmp_path / "after.yaml"
    assert main(["to-semantic-view", str(domain), "-o", str(written)]) == 0
    assert written.read_text() == fixture_body(FINANCE / "snowflake" / "cfo_cockpit.after.yaml")


def test_cli_unknown_key_exits(tmp_path, capsys):
    view = load_semantic_view(fixture_body(FINANCE / "snowflake" / "cfo_cockpit.before.yaml"))
    view["tags"] = ["board"]
    source = tmp_path / "view.yaml"
    source.write_text(dump_semantic_view(view))
    with pytest.raises(SystemExit) as caught:
        main(["to-wisdom", str(source), "-o", str(tmp_path / "domain.json")])
    assert caught.value.code == 1
    assert capsys.readouterr().err.strip() == "tags"


def test_runtime_dependencies_are_pyyaml_only():
    project = (ROOT / "pyproject.toml").read_text()
    dependencies = project.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "PyYAML" in dependencies
    assert "ossie" not in dependencies.lower()


def test_package_does_not_call_the_other_spokes():
    sources = "\n".join(path.read_text() for path in (ROOT / "src").rglob("*.py"))
    assert "ossie_snowflake" not in sources
    assert "from ossie_wisdom " not in sources
    assert "import ossie_wisdom\n" not in sources
    assert "subprocess" not in sources
