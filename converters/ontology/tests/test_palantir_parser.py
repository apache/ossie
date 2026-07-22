"""Tests for the Palantir parser's input-shape handling.

A Palantir export can arrive in several layouts — a ZIP archive, an already
extracted folder, a folder that wraps a single ZIP, and any of those packaged
under a single root directory. These tests exercise each supported layout plus
the validation failure paths (missing/empty ``data_sets`` folder, ambiguous or
missing ontology JSON, unsupported inputs).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from osi.external.palantir.model import Ontology
from osi.external.palantir.parser import PalantirParser

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

def test_zip_missing_data_sets_folder_raises(tmp_path: Path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ontology.json", json.dumps(_ONTOLOGY_JSON))
    with pytest.raises(ValueError, match="does not contain required 'data_sets' folder"):
        PalantirParser().parse(zip_path)


def test_directory_missing_data_sets_folder_raises(tmp_path: Path):
    export = tmp_path / "export"
    export.mkdir()
    (export / "ontology.json").write_text(json.dumps(_ONTOLOGY_JSON))
    with pytest.raises(ValueError, match="does not contain required 'data_sets' folder"):
        PalantirParser().parse(export)


def test_data_sets_folder_without_json_raises(tmp_path: Path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ontology.json", json.dumps(_ONTOLOGY_JSON))
        # A data_sets/ entry exists but contains no JSON files.
        zf.writestr("data_sets/README.txt", "no json here")
    with pytest.raises(ValueError, match="'data_sets' folder contains no JSON files"):
        PalantirParser().parse(zip_path)


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