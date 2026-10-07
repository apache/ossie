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

"""Every Ossie document in this repository, converted to AML.

The reverse path's whole job is reading documents this converter did not write,
and the snapshot fixtures cannot cover that: one of them this converter produced
and the other is a single example. The sweep found two defects the fixtures
missed, both of them crashes rather than bad output.

`examples/flights.semantic_model.yaml` is named `Flights semantic model`. Ossie
allows any non-empty string, and AML allows letters, digits and underscore, so
the conversion raised instead of renaming.

Three fixtures use one name for the semantic model and for a dataset inside it,
including `converters/cube/tests/fixtures/databricks_ossie.yaml`, where both are
`orders`. Ossie keeps the two in separate namespaces and AML does not, so the
generated AML failed to compile with "Duplicated name 'orders'".

Adding a fixture elsewhere in the repository extends this test with no edit
here, which is the point. apache/ossie#352 asked the Solid converter for the
same sweep.
"""
from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import pytest

import snapshots
from ossie_holistics import _yaml, ossie_to_aml, sqlrefs
from test_snapshots import HOLISTICS_CLI, needs_cli

#: Any `data_source_name`. These documents have no Holistics connection behind
#: them, and the value only has to be present for the AML to compile.
PROBE_CONNECTION = "probe"


def _target_dialect(document) -> str:
    """The SQL dialect most of a document's expressions already use.

    An operator converting one of these would name the warehouse the Holistics
    connection reads. Picking the document's own majority dialect is the closest
    stand-in, and it keeps the sweep testing the conversion rather than testing
    sqlglot's rendering.
    """
    counts = Counter()
    for dataset in document.get("datasets") or []:
        for field in dataset.get("fields") or []:
            for entry in (field.get("expression") or {}).get("dialects") or []:
                counts[entry.get("dialect")] += 1
    for name, _ in counts.most_common():
        if name in sqlrefs.SQLGLOT_DIALECT:
            return name
    return "ANSI_SQL"


def _foreign_ossie_documents() -> list[Path]:
    """Every flat Ossie document outside this converter's own directory.

    A flat document carries `version` and `datasets` at the root. That is the
    shape `main` moved to, and the heuristic apache/ossie#352 settled on after
    the wrapped `semantic_model:` list went away.
    """
    found = []
    roots = [snapshots.REPO_ROOT / "converters", snapshots.REPO_ROOT / "examples"]
    for root in roots:
        for path in sorted(root.rglob("*.yaml")):
            if snapshots.CONVERTER_ROOT in path.parents or ".venv" in path.parts:
                continue
            try:
                document = _yaml.load(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - a malformed sibling fixture is not our failure
                continue
            if isinstance(document, dict) and document.get("version") and "datasets" in document:
                found.append(path)
    return found


FOREIGN = _foreign_ossie_documents()


def _ids(paths: list[Path]) -> list[str]:
    return [str(p.relative_to(snapshots.REPO_ROOT)) for p in paths]


def test_the_sweep_finds_documents():
    """A heuristic that silently matches nothing would pass every test below."""
    assert len(FOREIGN) >= 20, _ids(FOREIGN)


@pytest.mark.parametrize("path", FOREIGN, ids=_ids(FOREIGN))
def test_every_ossie_document_converts(path):
    document = _yaml.load(path.read_text(encoding="utf-8"))
    result = ossie_to_aml.convert(
        document, _target_dialect(document), data_source_name=PROBE_CONNECTION
    )
    assert result.files
    assert sum(1 for name, _ in result.files if name.endswith(".dataset.aml")) == 1


@needs_cli
@pytest.mark.parametrize("path", FOREIGN, ids=_ids(FOREIGN))
def test_every_converted_document_compiles(path, tmp_path):
    document = _yaml.load(path.read_text(encoding="utf-8"))
    result = ossie_to_aml.convert(
        document, _target_dialect(document), data_source_name=PROBE_CONNECTION
    )
    for name, text in result.files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    dataset_file = next(name for name, _ in result.files if name.endswith(".dataset.aml"))
    completed = subprocess.run(
        [HOLISTICS_CLI, "aml", "validate", dataset_file, "-r", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "No validation errors found" in completed.stdout
