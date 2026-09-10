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
"""Command line round trips through both output formats."""

import json
from pathlib import Path

import pytest
import yaml

from ossie import OssieDocument
from ossie_lightdash.cli import main

TPCDS_PATH = Path(__file__).parent / ".." / ".." / ".." / "examples" / "tpcds_semantic_model.yaml"


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_import_writes_a_loadable_document(tmp_path, suffix):
    schema_yml = tmp_path / "schema.yml"
    assert main(["export", str(TPCDS_PATH), str(schema_yml), "--format", "dbt-meta"]) == 0

    document_path = tmp_path / f"semantic_model{suffix}"
    assert main(["import", str(schema_yml), str(document_path), "--schema", "public"]) == 0

    text = document_path.read_text(encoding="utf-8")
    if suffix == ".json":
        document = OssieDocument.model_validate_json(text)
    else:
        document = OssieDocument.model_validate(yaml.safe_load(text))
    assert {dataset.name for dataset in document.semantic_model[0].datasets} == {
        "store_sales", "date_dim", "customer", "item", "store"
    }

def test_export_writes_a_lightdash_project(tmp_path, capsys):
    project = tmp_path / "project"
    assert main(["export", "-i", str(TPCDS_PATH), "-o", str(project), "--dialect", "BIGQUERY"]) == 0
    captured = capsys.readouterr()
    # Like the other converters: stdout stays clean, stderr carries the report.
    assert captured.out == ""
    assert captured.err.splitlines()[-1].startswith("Wrote 5 model file(s) to ")
    # The one loss is named, explained, and counted.
    assert captured.err.splitlines()[:5] == [
        "TIME_ROLE_NOT_REPRESENTABLE  (1 element)",
        "  is_time on a non-date type (e.g. an integer year); Lightdash has no such marker, "
        "the column is a plain dimension",
        "    d_year",
        "",
        "1 issue(s); everything else converted cleanly. Pass --verbose to list every element.",
    ]
    files = sorted(p.name for p in (project / "lightdash" / "models").iterdir())
    assert files == ["customer.yml", "date_dim.yml", "item.yml", "store.yml", "store_sales.yml"]
    model = yaml.safe_load((project / "lightdash" / "models" / "store_sales.yml").read_text())
    assert model["type"] == "model"
    assert model["sql_from"] == "tpcds.public.store_sales"
    assert all({"name", "type", "sql"} <= set(d) for d in model["dimensions"])
    config = yaml.safe_load((project / "lightdash.config.yml").read_text())
    assert config["warehouse"] == {"type": "bigquery"}
    assert config["name"] == "tpcds_retail_model"
    # A config that someone has given a real warehouse type is kept ...
    (project / "lightdash.config.yml").write_text("name: mine\nversion: '1.0'\nwarehouse:\n  type: postgres\n")
    assert main(["export", str(TPCDS_PATH), str(project)]) == 0
    assert yaml.safe_load((project / "lightdash.config.yml").read_text())["name"] == "mine"


def test_placeholder_config_is_replaced_on_the_next_run(tmp_path):
    project = tmp_path / "project"
    # ... but the placeholder we wrote ourselves is not.
    assert main(["export", "-i", str(TPCDS_PATH), "-o", str(project)]) == 0
    assert yaml.safe_load((project / "lightdash.config.yml").read_text())["warehouse"] == {"type": "CHANGE_ME"}
    assert main(["export", "-i", str(TPCDS_PATH), "-o", str(project), "--dialect", "BIGQUERY"]) == 0
    assert yaml.safe_load((project / "lightdash.config.yml").read_text())["warehouse"] == {"type": "bigquery"}


def test_export_dbt_meta_still_writes_one_schema_file(tmp_path):
    schema_yml = tmp_path / "schema.yml"
    assert main(["export", str(TPCDS_PATH), str(schema_yml), "--format", "dbt-meta"]) == 0
    assert yaml.safe_load(schema_yml.read_text())["version"] == 2

def test_import_reads_a_whole_dbt_project(tmp_path, capsys):
    project = tmp_path / "dbt"
    (project / "models" / "marts").mkdir(parents=True)
    (project / "target").mkdir()
    (project / "dbt_project.yml").write_text("name: p\nmodels:\n  p:\n    +materialized: table\n")
    (project / "models" / "orders.yml").write_text(
        "models:\n  - name: orders\n    columns:\n      - name: amount\n        meta:\n          metrics:\n            total: {type: sum}\n"
    )
    (project / "models" / "marts" / "customers.yaml").write_text(
        "models:\n  - name: customers\n    columns:\n      - name: id\n"
    )
    (project / "data.yml").write_text("seeds:\n  - name: statuses\n    columns:\n      - name: code\n")
    (project / "target" / "stale.yml").write_text("models:\n  - name: stale\n")
    (project / "models" / "template.yml").write_text("{project_name}:\n  +materialized: view\n")

    document_path = tmp_path / "model.yaml"
    assert main(["import", "--input", str(project), "--output", str(document_path), "--schema", "marts"]) == 0
    document = OssieDocument.model_validate(yaml.safe_load(document_path.read_text()))
    assert [d.name for d in document.semantic_model[0].datasets] == ["customers", "orders", "statuses"]
    err = capsys.readouterr().err
    assert "Skipped" in err and "template.yml" in err
    assert document.semantic_model[0].metrics[0].name == "orders_total"

def test_import_takes_types_from_a_dbt_catalog(tmp_path):
    schema_yml = tmp_path / "schema.yml"
    schema_yml.write_text("models:\n  - name: races\n    columns:\n      - name: race_date\n      - name: laps\n")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "nodes": {
            "model.demo.races": {"columns": {"race_date": {"type": "DATE"}, "LAPS": {"type": "INT64"}}}
        }
    }))
    out = tmp_path / "model.yaml"
    assert main(["import", str(schema_yml), str(out), "--schema", "marts", "--catalog", str(catalog)]) == 0
    fields = {f["name"]: f for f in yaml.safe_load(out.read_text())["semantic_model"][0]["datasets"][0]["fields"]}
    assert fields["race_date"]["datatype"] == "Date"
    assert fields["laps"]["datatype"] == "Integer"

def test_input_and_output_are_required(capsys):
    with pytest.raises(SystemExit):
        main(["export", "-i", str(TPCDS_PATH)])
    assert "both --input and --output are required" in capsys.readouterr().err

def test_issues_are_grouped_by_type_unless_verbose(tmp_path, capsys):
    schema_yml = tmp_path / "schema.yml"
    columns = "".join(
        f"      - name: c{i}\n        meta:\n          metrics:\n            m{i}: {{type: sum, filters: [{{x: y}}]}}\n"
        for i in range(11)
    )
    schema_yml.write_text("models:\n  - name: t\n    columns:\n" + columns)
    assert main(["import", "-i", str(schema_yml), "-o", str(tmp_path / "a.yaml"), "--schema", "s"]) == 0
    grouped = capsys.readouterr().err.splitlines()
    assert grouped[0] == "METRIC_FILTER_NOT_PORTABLE  (11 elements)"
    assert grouped[2] == "    m0, m1, m2, m3, m4, m5, m6, m7"
    assert grouped[3] == "    ... and 3 more"
    assert main(["import", "-i", str(schema_yml), "-o", str(tmp_path / "b.yaml"), "--schema", "s", "--verbose"]) == 0
    verbose = capsys.readouterr().err.splitlines()
    assert verbose[2] == "    m0, m1, m2, m3, m4, m5, m6, m7, m8, m9, m10"
    assert "... and" not in "\n".join(verbose)

def test_missing_input_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["import", "-i", str(tmp_path / "nope"), "-o", str(tmp_path / "out.yaml")])
    assert "input not found" in capsys.readouterr().err

def test_lightdash_model_files_import_and_round_trip(tmp_path):
    project = tmp_path / "project"
    assert main(["export", "-i", str(TPCDS_PATH), "-o", str(project), "--dialect", "SNOWFLAKE"]) == 0
    back = tmp_path / "back.yaml"
    # No --database/--schema: the model files name their own sources.
    assert main(["import", "-i", str(project), "-o", str(back), "--dialect", "SNOWFLAKE"]) == 0
    original = OssieDocument.model_validate(yaml.safe_load(TPCDS_PATH.read_text())).semantic_model[0]
    roundtripped = OssieDocument.model_validate(yaml.safe_load(back.read_text())).semantic_model[0]
    assert {d.name: d.source for d in roundtripped.datasets} == {d.name: d.source for d in original.datasets}
    assert {d.name: d.primary_key for d in roundtripped.datasets} == {d.name: d.primary_key for d in original.datasets}
    assert {(d.name, f.name): f.dimension is not None for d in roundtripped.datasets for f in d.fields} == {
        (d.name, f.name): f.dimension is not None for d in original.datasets for f in d.fields
    }
    assert {(r.from_dataset, r.to) for r in roundtripped.relationships} == {
        (r.from_dataset, r.to) for r in original.relationships
    }
    assert len(roundtripped.metrics) == len(original.metrics)
