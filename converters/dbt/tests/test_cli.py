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

"""End-to-end tests for the ossie-dbt CLI entry point."""

import json
from pathlib import Path

import pytest

from ossie_dbt import cli
from tests.helpers import _ossie_dataset, _ossie_doc, _ossie_field, _ossie_metric


def _run_cli(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["ossie-dbt", *argv])
    cli.main()


def test_ossie_to_msi_writes_valid_manifest_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ossie-to-msi must serialize the manifest to JSON without crashing.

    Regression test for the CLI calling the pydantic v2 ``.model_dump_json()``
    on ``PydanticSemanticManifest``, which subclasses ``pydantic.v1.BaseModel``
    and only exposes ``.json()``. Before the fix this raised ``AttributeError``
    on every input, including the repository's own example model.
    """
    document = _ossie_doc(
        datasets=[
            _ossie_dataset(
                name="orders",
                fields=[_ossie_field("order_id"), _ossie_field("amount")],
                primary_key=["order_id"],
            )
        ],
        metrics=[_ossie_metric("total_amount", "SUM(orders.amount)")],
    )
    input_path = tmp_path / "model.yaml"
    output_path = tmp_path / "semantic_manifest.json"
    input_path.write_text(document.to_ossie_yaml())

    _run_cli(["ossie-to-msi", "-i", str(input_path), "-o", str(output_path)], monkeypatch)

    assert output_path.exists()
    manifest = json.loads(output_path.read_text())
    assert "semantic_models" in manifest
    assert "metrics" in manifest
