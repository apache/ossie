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

"""How an export's column types become concepts, and how names survive."""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.model import DatasetField, OssieOntology
from ossie_ontology.parser import OssieParser

from tests.palantir.converter.builders import (
    dataset,
    object_type,
    ot_rid,
    prop,
)

from tests.palantir.converter.helpers import (
    _convert,
    _mapped_relationship_names,
    _identifier_columns,
)


def _role_player_types(model: OssieOntology) -> dict[str, str]:
    """The concept each relationship's value role is typed by."""
    return {r.full_name: r.signature[-1].name for r in model.ontology.relationships}

def _field_types(model: OssieOntology) -> dict[str, str]:
    """The concept each dataset field is typed by."""
    return {
        field.name: field.type.name
        for om in model.ontology_mappings
        for dataset in om.semantic_model.datasets
        for field in dataset.fields
        if field.type is not None
    }

def test_property_types_without_a_scalar_equivalent_are_not_numeric(tmp_path: Path):
    """A blob or a reference must not come out typed as a number.

    Both the madlib role and the dataset field are typed from the same mapping,
    so a wrong answer asserts arithmetic over an attachment column in two
    places at once, with nothing downstream to reject it.
    """
    types = {
        "attachment": "ATTACHMENT",
        "media_ref": "MEDIA_REFERENCE",
        "secret": "CIPHER_TEXT",
        "shape": "STRUCT",
        "embedding": "VECTOR",
        "whatever": "ANY",
        "big": "LONG",
        "small": "SHORT",
    }
    object_types = [
        object_type(
            "doc",
            "Doc",
            status="active",
            properties=[
                prop(f"ri.p.{name}", name, column=name, type_name=palantir_type)
                for name, palantir_type in types.items()
            ],
        )
    ]
    datasets = [
        {
            "mainDatasetId": ot_rid("doc"),
            "datasetName": "doc_table",
            "datasetSchema": [{"name": "doc_id", "type": "STRING"}]
            + [{"name": name, "type": palantir_type} for name, palantir_type in types.items()],
        }
    ]

    model = _convert(tmp_path, object_types, datasets=datasets)

    expected = {
        "attachment": "String",  # an attachment rid
        "media_ref": "String",  # a media reference
        "secret": "String",  # ciphertext
        "shape": "String",  # no builtin describes a struct
        "embedding": "String",  # nor a vector
        "whatever": "Any",  # this one has an exact builtin
        "big": "Integer",
        "small": "Integer",
    }
    role_types = _role_player_types(model)
    assert {name: role_types[f"Doc.{name}"] for name in expected} == expected
    field_types = _field_types(model)
    assert {name: field_types[name] for name in expected} == expected

def test_unrecognized_column_type_falls_back_to_string(tmp_path: Path):
    """A warehouse type the enum has no name for must not fail the conversion.

    Column types drift with the export format, the field type is not serialized
    downstream at all, and a column with no type already falls back to String —
    so an unknown one is reported and carried, not raised. A bare ARRAY column
    is still dropped, as before.
    """
    object_types = [
        object_type(
            "doc",
            "Doc",
            status="active",
            properties=[prop("ri.p.blob", "blob", column="blob")],
        )
    ]
    datasets = [
        {
            "mainDatasetId": ot_rid("doc"),
            "datasetName": "doc_table",
            "datasetSchema": [
                {"name": "doc_id", "type": "STRING"},
                {"name": "blob", "type": "BINARY"},
                {"name": "props", "type": "MAP<STRING, STRING>"},
                {"name": "tags", "type": "ARRAY"},
                {"name": "untyped"},
            ],
        }
    ]

    with pytest.warns(UserWarning, match="Unrecognized column type 'BINARY'"):
        model = _convert(tmp_path, object_types, datasets=datasets)

    assert _field_types(model) == {
        "doc_id": "String",
        "blob": "String",
        "props": "String",
        "untyped": "String",  # the pre-existing fallback this one now matches
    }
    # And the mapping is still built, rather than lost with the run.
    assert _identifier_columns(model, "Doc") == ["doc_id"]

def test_column_name_becomes_an_identifier_and_keeps_reading_its_column(tmp_path: Path):
    """A column name that is not an identifier has to be handled on both sides.

    The field *name* is what a mapping expression refers to, and it is matched
    against an identifier pattern when the spec is read back — a name holding a
    space fails that and is quietly taken for a formula rather than the field.
    The field's *expression* is the SQL that reads the column, so it has to go
    on naming the column exactly, quoted.
    """
    columns = {"net amt": "net_amt", "a-b": "a_b", "x.y": "x_y", "9lives": "_9lives"}
    object_types = [
        object_type(
            "doc",
            "Doc",
            status="active",
            properties=[
                prop(f"ri.p.p{i}", f"p{i}", column=column)
                for i, column in enumerate(columns)
            ],
        )
    ]
    datasets = [
        {
            "mainDatasetId": ot_rid("doc"),
            "datasetName": "doc_table",
            "datasetSchema": [{"name": "doc_id", "type": "STRING"}]
            + [{"name": column, "type": "STRING"} for column in columns],
        }
    ]

    model = _convert(tmp_path, object_types, datasets=datasets)

    [dataset] = [ds for om in model.ontology_mappings for ds in om.semantic_model.datasets]
    expressions = {
        field.name: field.expression.dialects[0].expression
        for field in dataset.fields
        if field.expression is not None
    }
    assert set(expressions) == {"doc_id", *columns.values()}
    assert expressions == {
        "doc_id": "doc_id",  # already an identifier, so left alone
        "net_amt": '"net amt"',
        "a_b": '"a-b"',
        "x_y": '"x.y"',
        "_9lives": '"9lives"',  # a leading digit is not a bare identifier either
    }

def test_field_name_survives_a_round_trip_as_a_field(tmp_path: Path):
    """The reason the name matters: it has to resolve on the way back in."""
    object_types = [
        object_type(
            "doc",
            "Doc",
            status="active",
            properties=[prop("ri.p.net_amt", "net_amt", column="net amt")],
        )
    ]
    datasets = [
        {
            "mainDatasetId": ot_rid("doc"),
            "datasetName": "doc_table",
            "datasetSchema": [
                {"name": "doc_id", "type": "STRING"},
                {"name": "net amt", "type": "STRING"},
            ],
        }
    ]
    model = _convert(tmp_path, object_types, datasets=datasets)

    roundtrip = tmp_path / "roundtrip.yaml"
    roundtrip.write_text(OssieToSpecConverter.convert(model).dump_yaml())
    reparsed = OssieParser().parse(roundtrip)

    expressions = [
        child.object_mapping.expression
        for om in reparsed.ontology_mappings
        for cm in om.concept_mappings
        for link_mapping in cm.link_mappings
        for child in link_mapping.children or []
    ]
    assert expressions, "the property mapping did not survive the round trip"
    for expression in expressions:
        assert isinstance(expression, DatasetField), f"demoted to {type(expression).__name__}"

def test_column_without_a_name_does_not_reach_the_converter(tmp_path: Path):
    """The parser drops it; conversion proceeds on the columns that remain.

    This used to crash in `_normalize_field_name` on a `None` name, naming
    neither the dataset nor the column.
    """
    object_types = [
        object_type(
            "doc",
            "Doc",
            status="active",
            properties=[prop("ri.p.label", "label", column="label")],
        )
    ]
    datasets = [
        {
            "mainDatasetId": ot_rid("doc"),
            "datasetName": "doc_table",
            "datasetSchema": [
                {"name": "doc_id", "type": "STRING"},
                {"type": "STRING"},
                {"name": "label", "type": "STRING"},
            ],
        }
    ]

    with pytest.warns(UserWarning, match="Skipping column with missing name"):
        model = _convert(tmp_path, object_types, datasets=datasets)

    assert set(_field_types(model)) == {"doc_id", "label"}
    assert _mapped_relationship_names(model, "Doc") == {"Doc.label"}

