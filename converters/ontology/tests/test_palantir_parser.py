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

"""Tests for the Palantir parser's input-shape handling.

A Palantir export can arrive in several layouts — a ZIP archive, an already
extracted folder, a folder that wraps a single ZIP, and any of those packaged
under a single root directory. These tests exercise each supported layout plus
the validation failure paths (missing/empty ``data_sets`` folder, ambiguous or
missing ontology JSON, unsupported inputs).

Layout is not the only thing that varies between export formats: the last
section covers the several keys under which a datasource carries its own
identifier, and how a subclass teaches the parser one more.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ossie_ontology.external.palantir.model import DataType, Ontology
from ossie_ontology.external.palantir.parser import PalantirOntologyParser, PalantirParser
from ossie_ontology.model import BUILTIN_CONCEPTS

# A minimal-but-complete Palantir export: one object type backed by one dataset.
_ONTOLOGY_JSON = {
    "objectTypes": [
        {
            "rid": "ri.ot.1",
            "id": "widget",
            "displayName": "Widget",
            "properties": [
                {"rid": "ri.p.1", "id": "widget_id", "baseType": {"type": "STRING"}}
            ],
            "primaryKeys": ["widget_id"],
        }
    ],
    "relations": [],
}
_DATASET_JSON = [
    {
        "mainDatasetId": "ri.ot.1",
        "datasetName": "widget",
        "datasetSchema": [{"name": "widget_id", "type": "STRING"}],
    }
]


# ----- builders ---------------------------------------------------------

def _write_dir_export(base: Path, *, root: str | None = None) -> Path:
    """Create an extracted-folder export under *base*, optionally nested inside a
    single wrapping *root* directory. Returns *base* (the path to hand to parse)."""
    target = base / root if root else base
    (target / "data_sets").mkdir(parents=True)
    (target / "ontology.json").write_text(json.dumps(_ONTOLOGY_JSON))
    (target / "data_sets" / "ds.json").write_text(json.dumps(_DATASET_JSON))
    return base


def _write_zip_export(zip_path: Path, *, root: str | None = None) -> Path:
    """Create a ZIP export at *zip_path*, optionally packaged under a single
    *root* directory. Returns *zip_path*."""
    prefix = f"{root}/" if root else ""
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{prefix}ontology.json", json.dumps(_ONTOLOGY_JSON))
        zf.writestr(f"{prefix}data_sets/ds.json", json.dumps(_DATASET_JSON))
    return zip_path


def _assert_widget_model(model: Ontology) -> None:
    assert isinstance(model, Ontology)
    object_types = model.object_types()
    assert list(object_types.keys()) == ["ri.ot.1"]
    widget = object_types["ri.ot.1"]
    assert widget.readable_id() == "widget"
    # The backing dataset should have been matched and synced onto the object type.
    assert widget.has_syncs_from()


# ----- supported layouts ------------------------------------------------

def test_parse_top_level_zip(tmp_path: Path):
    zip_path = _write_zip_export(tmp_path / "export.zip")
    _assert_widget_model(PalantirParser().parse(zip_path))


def test_parse_single_root_zip(tmp_path: Path):
    zip_path = _write_zip_export(tmp_path / "export.zip", root="export")
    _assert_widget_model(PalantirParser().parse(zip_path))


def test_parse_extracted_directory(tmp_path: Path):
    export = _write_dir_export(tmp_path / "export")
    _assert_widget_model(PalantirParser().parse(export))


def test_parse_single_root_directory(tmp_path: Path):
    # base/ contains exactly one child dir which holds the export.
    export = _write_dir_export(tmp_path / "export", root="inner")
    _assert_widget_model(PalantirParser().parse(export))


def test_parse_directory_wrapping_single_zip(tmp_path: Path):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    _write_zip_export(wrapper / "export.zip")
    _assert_widget_model(PalantirParser().parse(wrapper))


# ----- unsupported / missing inputs -------------------------------------

def test_parse_missing_path_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        PalantirParser().parse(tmp_path / "nope")


def test_parse_non_zip_file_raises(tmp_path: Path):
    bad = tmp_path / "notes.txt"
    bad.write_text("not a zip")
    with pytest.raises(ValueError, match="Expected a ZIP archive or a directory"):
        PalantirParser().parse(bad)


# ----- invalid data_sets -----------------------------------------------

# Datasets are optional: without them the ontology still parses in full, but
# nothing maps its concepts to source tables, so it answers queries with
# nothing. Each case warns and carries on rather than failing.

def test_zip_missing_data_sets_folder_warns(tmp_path: Path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ontology.json", json.dumps(_ONTOLOGY_JSON))
    with pytest.warns(UserWarning, match="no 'data_sets' folder"):
        ontology = PalantirParser().parse(zip_path)
    assert ontology.object_types()


def test_directory_missing_data_sets_folder_warns(tmp_path: Path):
    export = tmp_path / "export"
    export.mkdir()
    (export / "ontology.json").write_text(json.dumps(_ONTOLOGY_JSON))
    with pytest.warns(UserWarning, match="no 'data_sets' folder"):
        ontology = PalantirParser().parse(export)
    assert ontology.object_types()


def test_data_sets_folder_without_json_warns(tmp_path: Path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ontology.json", json.dumps(_ONTOLOGY_JSON))
        # A data_sets/ entry exists but contains no JSON files.
        zf.writestr("data_sets/README.txt", "no json here")
    with pytest.warns(UserWarning, match="contains no JSON files"):
        ontology = PalantirParser().parse(zip_path)
    assert ontology.object_types()


# ----- ontology JSON resolution -----------------------------------------

def test_multiple_top_level_json_in_zip_raises(tmp_path: Path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ontology.json", json.dumps(_ONTOLOGY_JSON))
        zf.writestr("other.json", json.dumps(_ONTOLOGY_JSON))
        zf.writestr("data_sets/ds.json", json.dumps(_DATASET_JSON))
    with pytest.raises(ValueError, match="exactly one top-level JSON file"):
        PalantirParser().parse(zip_path)


def test_multiple_top_level_json_in_directory_raises(tmp_path: Path):
    export = _write_dir_export(tmp_path / "export")
    (export / "other.json").write_text(json.dumps(_ONTOLOGY_JSON))
    with pytest.raises(ValueError, match="exactly one top-level JSON file"):
        PalantirParser().parse(export)


# ----- datasource identifiers -------------------------------------------

# A datasource names itself under a key that depends on how old the export is:
# `datasourceRid` in the ones that introduced it, plain `rid` in the versioned
# (v1/v2/v3) ones. The parser reads through DATASOURCE_RID_KEYS so both work and
# a further spelling costs one entry rather than a rewrite of the incoming JSON.
# `backingResourceRid` is what ties the datasource to a dataset, and is matched
# against the dataset JSON's `mainDatasetId` — hence "ri.ot.1" below.

def _write_export_with_datasource(base: Path, datasource: dict) -> Path:
    """An extracted-folder export whose object type declares one *datasource*."""
    ontology = json.loads(json.dumps(_ONTOLOGY_JSON))
    ontology["objectTypes"][0]["datasources"] = [datasource]

    (base / "data_sets").mkdir(parents=True)
    (base / "ontology.json").write_text(json.dumps(ontology))
    (base / "data_sets" / "ds.json").write_text(json.dumps(_DATASET_JSON))
    return base


def _sole_data_source(model: Ontology):
    (data_source,) = model.object_types()["ri.ot.1"].data_sources()
    return data_source


def test_datasource_rid_under_legacy_key(tmp_path: Path):
    export = _write_export_with_datasource(
        tmp_path / "export",
        {"datasourceRid": "ri.datasource.1", "backingResourceRid": "ri.ot.1"},
    )
    model = PalantirParser().parse(export)

    assert _sole_data_source(model).backing_datasource_id() == "ri.datasource.1"
    _assert_widget_model(model)


def test_datasource_rid_under_rid_key(tmp_path: Path):
    export = _write_export_with_datasource(
        tmp_path / "export",
        {"rid": "ri.datasource.1", "backingResourceRid": "ri.ot.1"},
    )
    model = PalantirParser().parse(export)

    assert _sole_data_source(model).backing_datasource_id() == "ri.datasource.1"
    _assert_widget_model(model)


def test_datasource_rid_prefers_the_earlier_key(tmp_path: Path):
    """An export carrying both spellings resolves in DATASOURCE_RID_KEYS order."""
    export = _write_export_with_datasource(
        tmp_path / "export",
        {
            "datasourceRid": "ri.datasource.preferred",
            "rid": "ri.datasource.other",
            "backingResourceRid": "ri.ot.1",
        },
    )
    model = PalantirParser().parse(export)

    assert _sole_data_source(model).backing_datasource_id() == "ri.datasource.preferred"


def test_datasource_without_any_known_rid_key_raises(tmp_path: Path):
    export = _write_export_with_datasource(
        tmp_path / "export", {"backingResourceRid": "ri.ot.1"}
    )
    with pytest.raises(ValueError, match="must be non-empty"):
        PalantirParser().parse(export)


def test_subclass_can_add_a_datasource_rid_spelling(tmp_path: Path):
    """The one-line extension the key list exists for."""

    class _ExtendedOntologyParser(PalantirOntologyParser):
        DATASOURCE_RID_KEYS = PalantirOntologyParser.DATASOURCE_RID_KEYS + ("datasourceId",)

    class _ExtendedParser(PalantirParser):
        def _make_ontology_parser(self) -> PalantirOntologyParser:
            return _ExtendedOntologyParser()

    export = _write_export_with_datasource(
        tmp_path / "export",
        {"datasourceId": "ri.datasource.1", "backingResourceRid": "ri.ot.1"},
    )

    with pytest.raises(ValueError, match="must be non-empty"):
        PalantirParser().parse(export)

    model = _ExtendedParser().parse(export)
    assert _sole_data_source(model).backing_datasource_id() == "ri.datasource.1"


# ----- dataset schema ---------------------------------------------------

def _write_export_with_schema(base: Path, schema: list[dict]) -> Path:
    """An extracted-folder export whose dataset declares the given *schema*."""
    datasets = json.loads(json.dumps(_DATASET_JSON))
    datasets[0]["datasetSchema"] = schema

    (base / "data_sets").mkdir(parents=True)
    (base / "ontology.json").write_text(json.dumps(_ONTOLOGY_JSON))
    (base / "data_sets" / "ds.json").write_text(json.dumps(datasets))
    return base


def test_column_without_a_name_is_skipped(tmp_path: Path):
    """A nameless column is reported here, where the dataset is known.

    Kept out of the model rather than carried as `None`: the converter
    normalizes column names, so a nameless one used to surface much later as an
    AttributeError that named neither the dataset nor the file it came from.
    """
    export = _write_export_with_schema(
        tmp_path / "export",
        [
            {"name": "widget_id", "type": "STRING"},
            {"type": "STRING"},  # no name at all
            {"name": "   ", "type": "STRING"},  # blank name
        ],
    )

    with pytest.warns(UserWarning, match="Skipping column with missing name") as records:
        model = PalantirParser().parse(export)

    assert len(records) == 2
    [dataset] = model.data_sets().values()
    assert [column.name() for column in dataset.columns()] == ["widget_id"]
    _assert_widget_model(model)


# ----- data types -------------------------------------------------------

def test_every_data_type_maps_to_a_builtin_concept():
    """The converter looks this name up and raises when it is not a builtin, so
    an unmapped member would fail every export that uses it."""
    unmapped = {t.name: t.to_type() for t in DataType if t.to_type() not in BUILTIN_CONCEPTS}
    assert unmapped == {}


def test_only_integral_types_map_to_integer():
    """Typing a blob or a reference as a number asserts arithmetic over it.

    The mapping ends in a catch-all, so this is the check that a member added
    to the enum does not quietly join the numeric branch.
    """
    numeric = {t.name for t in DataType if t.to_type() == "Integer"}
    assert numeric == {"INTEGER", "LONG", "SHORT"}
