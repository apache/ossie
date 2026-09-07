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

"""Tests for what the Palantir converter admits into the model, and why.

Two things decide it. The first is *status*: an export marks each object type,
property and relation as active, experimental, deprecated and so on, and only
some of those belong in a converted model. The defaults here suit an ontology in
production use — active work, plus the endorsed and intermediary entities it
leans on. They are class attributes read through ``allowed_*_statuses()``
methods rather than inline checks, so a caller with a different definition of
"finished" (a draft ontology, say, where nearly everything is still
experimental) subclasses and widens them. These tests pin both the defaults and
the overriding.

The second is *column matching*: an export's two halves routinely disagree on
the case of a physical column name, so a primary key naming ``LOCNO`` has to
find a dataset field called ``locno``. Exact matches still win.

Each test builds the smallest export that shows the behaviour and runs it
through the real parser, so the fixtures stay in the JSON shapes an export
actually uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pytest

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.external.palantir.model import Status
from ossie_ontology.external.palantir.parser import PalantirParser
from ossie_ontology.model import ConceptMapping, DatasetField, OssieOntology
from ossie_ontology.parser import OssieParser


# ----- export builders --------------------------------------------------

def _prop(
    rid: str,
    id_: str,
    *,
    status: str | None = None,
    column: str | None = None,
    pk_columns: dict[str, str] | None = None,
    type_name: str = "STRING",
) -> dict[str, Any]:
    prop: dict[str, Any] = {"rid": rid, "id": id_, "baseType": {"type": type_name}}
    if status:
        prop["status"] = {"type": status}
    if column:
        prop["column"] = column
    if pk_columns:
        # Keyed by the backing dataset's rid: a primary key can sit in a
        # differently named column in each dataset that feeds the object type.
        prop["primaryKeyMapping"] = {
            ds_rid: {"columnName": name} for ds_rid, name in pk_columns.items()
        }
    return prop


def _object_type(
    key: str,
    name: str,
    *,
    status: str | None = None,
    properties: Iterable[dict[str, Any]] = (),
    primary_keys: Iterable[str] | None = None,
    datasources: Iterable[str] | None = None,
) -> dict[str, Any]:
    """An object type identified by ``<key>_id``, plus any *properties* given.

    With no *datasources*, carries no ``datasources`` entry, so the parser falls
    back to matching a dataset whose ``mainDatasetId`` is the object type's own
    rid — which is what :func:`_dataset` builds. Pass dataset rids to back the
    object type with several datasets instead.
    """
    props = [_prop(f"ri.p.{key}_id", f"{key}_id"), *properties]
    ot: dict[str, Any] = {
        "rid": _ot_rid(key),
        "id": key,
        "displayName": name,
        "properties": props,
        "primaryKeys": list(primary_keys) if primary_keys is not None else [f"{key}_id"],
    }
    if status:
        ot["status"] = {"type": status}
    if datasources is not None:
        ot["datasources"] = [
            {"datasourceRid": rid, "backingResourceRid": rid} for rid in datasources
        ]
    return ot


def _many_to_one(
    key: str,
    *,
    one: str,
    many: str,
    key_map: dict[str, str],
    status: str | None = None,
) -> dict[str, Any]:
    """A M:1 relation from the *many* object type to the *one* object type.

    ``key_map`` maps a primary-key property rid on the one side to the property
    rid holding it on the many side.
    """
    relation: dict[str, Any] = {
        "rid": f"ri.rel.{key}",
        "id": key,
        "definition": {
            "type": "oneToMany",
            "oneToMany": {
                "objectTypeRidOneSide": _ot_rid(one),
                "objectTypeRidManySide": _ot_rid(many),
                "oneSidePrimaryKeyToManySidePropertyMapping": key_map,
            },
        },
    }
    if status:
        relation["status"] = {"type": status}
    return relation


def _many_to_many(key: str, *, a: str, b: str, status: str | None = None) -> dict[str, Any]:
    """A M:M relation between object types *a* and *b*, keyed on their own ids.

    Each side's primary key maps to the like-named column of the join table,
    which the relation carries as its backing datasource.
    """
    relation: dict[str, Any] = {
        "rid": f"ri.rel.{key}",
        "id": key,
        "definition": {
            "type": "manyToMany",
            "manyToMany": {
                "objectTypeRidA": _ot_rid(a),
                "objectTypeRidB": _ot_rid(b),
                "objectTypeAPrimaryKeyPropertyMapping": {f"ri.p.{a}_id": f"{a}_id"},
                "objectTypeBPrimaryKeyPropertyMapping": {f"ri.p.{b}_id": f"{b}_id"},
                "joinTableDatasource": [
                    {"datasourceRid": f"ri.ds.{key}", "backingResourceRid": f"ri.res.{key}"}
                ],
            },
        },
    }
    if status:
        relation["status"] = {"type": status}
    return relation


def _intermediary(
    key: str,
    *,
    a: str,
    b: str,
    via: str,
    link_a: str,
    link_b: str,
    status: str | None = None,
) -> dict[str, Any]:
    """An intermediary relation joining *a* to *b* through the *via* object type.

    ``link_a`` and ``link_b`` name the two M:1 relations it is derived from;
    both must be present in the export or the parser rejects it.
    """
    relation: dict[str, Any] = {
        "rid": f"ri.rel.{key}",
        "id": key,
        "definition": {
            "type": "intermediary",
            "intermediary": {
                "objectTypeRidA": _ot_rid(a),
                "objectTypeRidB": _ot_rid(b),
                "intermediaryObjectTypeRid": _ot_rid(via),
                "aToIntermediaryLinkTypeRid": f"ri.rel.{link_a}",
                "intermediaryToBLinkTypeRid": f"ri.rel.{link_b}",
            },
        },
    }
    if status:
        relation["status"] = {"type": status}
    return relation


def _dataset(key: str, columns: Iterable[str]) -> dict[str, Any]:
    """The dataset backing object type *key*, with the columns named."""
    return {
        "mainDatasetId": _ot_rid(key),
        "datasetName": f"{key}_table",
        "datasetSchema": [{"name": column, "type": "STRING"} for column in columns],
    }


def _ot_rid(key: str) -> str:
    return f"ri.ot.{key}"


def _convert(
    tmp_path: Path,
    object_types: Iterable[dict[str, Any]],
    *,
    relations: Iterable[dict[str, Any]] = (),
    datasets: Iterable[dict[str, Any]] = (),
    converter: type[PalantirToOssieConverter] = PalantirToOssieConverter,
) -> OssieOntology:
    """Write an extracted-folder export, parse it, and convert it.

    Both files are rewritten on every call, so a test that converts the same
    export under two different policies can call this twice.
    """
    export = tmp_path / "export"
    (export / "data_sets").mkdir(parents=True, exist_ok=True)
    (export / "ontology.json").write_text(
        json.dumps({"objectTypes": list(object_types), "relations": list(relations)})
    )
    (export / "data_sets" / "ds.json").write_text(json.dumps(list(datasets)))
    return converter().convert(PalantirParser().parse(export))


# ----- assertion helpers ------------------------------------------------

def _concept_names(model: OssieOntology) -> set[str]:
    return {c.name for c in model.ontology.concepts(exclude_builtin=True)}


def _relationship_names(model: OssieOntology) -> set[str]:
    return {r.full_name for r in model.ontology.relationships}


def _concept_mapping(model: OssieOntology, concept_name: str) -> ConceptMapping:
    mappings = [
        cm
        for om in model.ontology_mappings
        for cm in om.concept_mappings
        if cm.concept.name == concept_name
    ]
    assert len(mappings) == 1, f"expected one mapping for '{concept_name}', got {len(mappings)}"
    return mappings[0]


def _mapped_relationship_names(model: OssieOntology, concept_name: str) -> set[str]:
    """Names of the relationships a concept's mapping actually populates."""
    names = set()
    for lm in _concept_mapping(model, concept_name).link_mappings:
        for child in lm.children or []:
            assert child.relationship is not None
            names.add(child.relationship.full_name)
    return names


def _identifier_columns(model: OssieOntology, concept_name: str) -> list[str]:
    """The dataset fields a concept's mapping identifies its instances by."""
    [object_mapping] = _concept_mapping(model, concept_name).object_mappings
    columns = []
    for rm in object_mapping.referent_mappings or []:
        assert isinstance(rm.expression, DatasetField)
        columns.append(rm.expression.name)
    return columns


# ----- object type statuses ---------------------------------------------

def test_default_policy_admits_active_endorsed_and_intermediary(tmp_path: Path):
    model = _convert(tmp_path, [
        _object_type("alpha", "Alpha", status="active"),
        _object_type("bravo", "Bravo", status="endorsed"),
        _object_type("charlie", "Charlie", status="intermediary"),
        _object_type("delta", "Delta", status="experimental"),
        _object_type("echo", "Echo", status="deprecated"),
    ])
    assert _concept_names(model) == {"Alpha", "Bravo", "Charlie"}


def test_object_type_named_like_a_builtin_is_reported(tmp_path: Path):
    """`date` pascal-cases to `Date`, which is a builtin value type.

    Letting it through gives the entity the builtin's name, and every DATE
    column in the export is then typed by this object type — so it is reported
    as the concept-name collision it is, naming the object type rather than
    surfacing later as a complaint about a missing 'date_id' property.
    """
    object_types = [
        _object_type(
            "date",
            "date",
            status="active",
            properties=[_prop("ri.p.label", "label", column="label")],
        )
    ]

    with pytest.raises(ValueError, match="which is a builtin value type"):
        _convert(tmp_path, object_types, datasets=[_dataset("date", ["date_id", "label"])])


def test_object_type_statuses_can_be_widened_by_a_subclass(tmp_path: Path):
    """The extension point a draft-ontology converter is built on."""

    class DraftConverter(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    object_types = [
        _object_type("alpha", "Alpha", status="active"),
        _object_type("delta", "Delta", status="experimental"),
    ]
    assert _concept_names(_convert(tmp_path, object_types)) == {"Alpha"}

    widened = _convert(tmp_path, object_types, converter=DraftConverter)
    assert _concept_names(widened) == {"Alpha", "Delta"}


# ----- property statuses ------------------------------------------------

def _widget_export() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One object type with a property per status, and its backing dataset."""
    object_types = [
        _object_type(
            "widget",
            "Widget",
            status="active",
            properties=[
                _prop("ri.p.label", "label", status="active", column="label"),
                _prop("ri.p.note", "note", status="experimental", column="note"),
                _prop("ri.p.legacy", "legacy", status="deprecated", column="legacy"),
            ],
        )
    ]
    return object_types, [_dataset("widget", ["widget_id", "label", "note", "legacy"])]


def test_experimental_property_is_declared_but_not_mapped(tmp_path: Path):
    """An experimental property is part of the ontology, but nothing populates it.

    The two policies differ on purpose: the relationship is worth declaring
    while the work is in progress, but pointing a mapping at a column that is
    still being reshaped is not.
    """
    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert "Widget.note" in _relationship_names(model)
    assert _mapped_relationship_names(model, "Widget") == {"Widget.label"}


def test_deprecated_property_is_not_converted_at_all(tmp_path: Path):
    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert "Widget.legacy" not in _relationship_names(model)


def test_mapping_property_statuses_can_be_widened_by_a_subclass(tmp_path: Path):
    class DraftConverter(PalantirToOssieConverter):
        MAPPING_PROPERTY_STATUSES = (
            PalantirToOssieConverter.MAPPING_PROPERTY_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets, converter=DraftConverter)

    assert _mapped_relationship_names(model, "Widget") == {"Widget.label", "Widget.note"}


# ----- property types ---------------------------------------------------

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
        _object_type(
            "doc",
            "Doc",
            status="active",
            properties=[
                _prop(f"ri.p.{name}", name, column=name, type_name=palantir_type)
                for name, palantir_type in types.items()
            ],
        )
    ]
    datasets = [
        {
            "mainDatasetId": _ot_rid("doc"),
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
        _object_type(
            "doc",
            "Doc",
            status="active",
            properties=[_prop("ri.p.blob", "blob", column="blob")],
        )
    ]
    datasets = [
        {
            "mainDatasetId": _ot_rid("doc"),
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
        _object_type(
            "doc",
            "Doc",
            status="active",
            properties=[
                _prop(f"ri.p.p{i}", f"p{i}", column=column)
                for i, column in enumerate(columns)
            ],
        )
    ]
    datasets = [
        {
            "mainDatasetId": _ot_rid("doc"),
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
        _object_type(
            "doc",
            "Doc",
            status="active",
            properties=[_prop("ri.p.net_amt", "net_amt", column="net amt")],
        )
    ]
    datasets = [
        {
            "mainDatasetId": _ot_rid("doc"),
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
        _object_type(
            "doc",
            "Doc",
            status="active",
            properties=[_prop("ri.p.label", "label", column="label")],
        )
    ]
    datasets = [
        {
            "mainDatasetId": _ot_rid("doc"),
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


# ----- relation statuses ------------------------------------------------

def _linked_export(
    *, ot_status: str, relation_status: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two object types and the M:1 relation between them, at the given statuses."""
    object_types = [
        _object_type("widget", "Widget", status=ot_status),
        _object_type(
            "gadget",
            "Gadget",
            status=ot_status,
            properties=[_prop("ri.p.gadget_widget_id", "gadget_widget_id", column="widget_id")],
        ),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.gadget_widget_id"},
            status=relation_status,
        )
    ]
    return object_types, relations


def test_relation_statuses_gate_conversion(tmp_path: Path):
    object_types, relations = _linked_export(ot_status="active", relation_status="deprecated")
    model = _convert(tmp_path, object_types, relations=relations)

    assert "Gadget.gadget_widget" not in _relationship_names(model)


def test_experimental_relation_between_active_object_types_is_converted(tmp_path: Path):
    """Experimental relations ride in on their endpoints rather than their own status."""
    object_types, relations = _linked_export(ot_status="active", relation_status="experimental")
    model = _convert(tmp_path, object_types, relations=relations)

    assert "Gadget.gadget_widget" in _relationship_names(model)


def test_experimental_relation_needs_its_endpoints_admitted_too(tmp_path: Path):
    """Widening object types alone leaves their experimental relations behind.

    The endpoint check is separate from the object-type policy, so a converter
    that admits experimental object types still drops every experimental
    relation between them until it widens the endpoint statuses as well.
    """

    class WidenedObjectTypes(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    class WidenedEndpoints(WidenedObjectTypes):
        RELATION_ENDPOINT_STATUSES = (
            PalantirToOssieConverter.RELATION_ENDPOINT_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, relations = _linked_export(
        ot_status="experimental", relation_status="experimental"
    )

    partial = _convert(tmp_path, object_types, relations=relations, converter=WidenedObjectTypes)
    assert _concept_names(partial) == {"Widget", "Gadget"}
    assert "Gadget.gadget_widget" not in _relationship_names(partial)

    full = _convert(tmp_path, object_types, relations=relations, converter=WidenedEndpoints)
    assert "Gadget.gadget_widget" in _relationship_names(full)


def test_link_colliding_with_a_property_name_is_skipped(tmp_path: Path):
    """A shared name costs the link, not the conversion.

    Exports routinely name a link after the foreign-key column backing it, and
    both become relationships on the same concept — which the ontology rejects
    as a duplicate. The relation is dropped with a warning; the property, which
    is converted first, keeps the name.
    """
    object_types = [
        _object_type("widget", "Widget", status="active"),
        _object_type(
            "gadget",
            "Gadget",
            status="active",
            properties=[_prop("ri.p.owner", "owner", column="owner")],
        ),
    ]
    relations = [
        _many_to_one(
            "owner",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.owner"},
            status="active",
        )
    ]
    datasets = [_dataset("widget", ["widget_id"]), _dataset("gadget", ["gadget_id", "owner"])]

    with pytest.warns(UserWarning, match="Relation 'Gadget.owner' collides"):
        model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    # One 'owner' relationship, and it is the property's — still mapped to its
    # own column, rather than the skipped link's walk into Widget.
    assert _relationship_names(model) == {"Widget.widget_id", "Gadget.gadget_id", "Gadget.owner"}
    assert _mapped_relationship_names(model, "Gadget") == {"Gadget.owner"}


# ----- many-to-many and intermediary relations --------------------------

def _tagging_export(*, widget_properties: Iterable[dict[str, Any]] = ()):
    """Widget tagged with Tag through the WTag join object type.

    The two M:1 links run from WTag, which holds a foreign key to each side —
    the ordinary join-table shape, and non-primary-key columns, so neither link
    is read as inheritance.
    """
    object_types = [
        _object_type("widget", "Widget", status="active", properties=list(widget_properties)),
        _object_type("tag", "Tag", status="active"),
        _object_type(
            "wtag",
            "WTag",
            status="active",
            properties=[
                _prop("ri.p.wtag_widget", "wtag_widget"),
                _prop("ri.p.wtag_tag", "wtag_tag"),
            ],
        ),
    ]
    relations = [
        _many_to_one(
            "a_link", one="widget", many="wtag",
            key_map={"ri.p.widget_id": "ri.p.wtag_widget"}, status="active",
        ),
        _many_to_one(
            "b_link", one="tag", many="wtag",
            key_map={"ri.p.tag_id": "ri.p.wtag_tag"}, status="active",
        ),
        _intermediary(
            "tagged", a="widget", b="tag", via="wtag",
            link_a="a_link", link_b="b_link", status="active",
        ),
    ]
    return object_types, relations


def _derived_by_expressions(model: OssieOntology, full_name: str) -> list[str]:
    [relationship] = [r for r in model.ontology.relationships if r.full_name == full_name]
    return [f.raw_expr for f in relationship.derived_by]


def test_many_to_many_relation_becomes_a_relationship_on_role_a(tmp_path: Path):
    object_types = [
        _object_type("widget", "Widget", status="active"),
        _object_type("tag", "Tag", status="active"),
    ]
    relations = [_many_to_many("tags", a="widget", b="tag", status="active")]

    model = _convert(tmp_path, object_types, relations=relations)

    assert "Widget.tags" in _relationship_names(model)


def test_intermediary_relation_is_derived_from_its_two_links(tmp_path: Path):
    """The relationship lands on role A and is derived by joining both links."""
    object_types, relations = _tagging_export()

    model = _convert(tmp_path, object_types, relations=relations)

    names = _relationship_names(model)
    assert "Widget.tagged" in names
    assert {"WTag.a_link", "WTag.b_link"} <= names
    assert _supertype_names(model, "WTag") == []
    assert _derived_by_expressions(model, "Widget.tagged") == [
        "WTag.a_link(Widget) AND WTag.b_link(Tag)"
    ]


def test_many_to_many_link_colliding_with_a_property_name_is_skipped(tmp_path: Path):
    """Same guard as the M:1 case, on the other relation kind."""
    object_types = [
        _object_type(
            "widget", "Widget", status="active",
            properties=[_prop("ri.p.tags", "tags", column="tags")],
        ),
        _object_type("tag", "Tag", status="active"),
    ]
    relations = [_many_to_many("tags", a="widget", b="tag", status="active")]

    with pytest.warns(UserWarning, match="Relation 'Widget.tags' collides"):
        model = _convert(tmp_path, object_types, relations=relations)

    assert _relationship_names(model) == {"Widget.widget_id", "Widget.tags", "Tag.tag_id"}


def test_intermediary_link_colliding_with_a_property_name_is_skipped(tmp_path: Path):
    """Skipping leaves no half-built relationship: the join formula goes too."""
    object_types, relations = _tagging_export(
        widget_properties=[_prop("ri.p.tagged", "tagged", column="tagged")]
    )

    with pytest.warns(UserWarning, match="Relation 'Widget.tagged' collides"):
        model = _convert(tmp_path, object_types, relations=relations)

    # The surviving 'Widget.tagged' is the property, so it carries no join.
    assert _derived_by_expressions(model, "Widget.tagged") == []
    assert {"WTag.a_link", "WTag.b_link"} <= _relationship_names(model)


# ----- links across several datasets ------------------------------------

def _two_dataset_export(*, second_carries_primary_key: bool):
    """Gadget backed by two datasets, each holding the FK to Widget.

    With *second_carries_primary_key* false, nothing in the second dataset
    identifies a Gadget, so its ConceptMapping is dropped while the dataset —
    and its copy of the foreign-key column — remains.
    """
    object_types = [
        _object_type("widget", "Widget", status="active"),
        _object_type(
            "gadget",
            "Gadget",
            status="active",
            properties=[_prop("ri.p.widget_ref", "widget_ref", column="widget_ref")],
            datasources=["ri.ds.a", "ri.ds.b"],
        ),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.widget_ref"},
            status="active",
        )
    ]
    second_columns = ["gadget_id", "widget_ref"] if second_carries_primary_key else ["widget_ref"]
    datasets = [
        {
            "mainDatasetId": "ri.ds.a",
            "datasetName": "ds_a",
            "datasetSchema": [{"name": c, "type": "STRING"} for c in ("gadget_id", "widget_ref")],
        },
        {
            "mainDatasetId": "ri.ds.b",
            "datasetName": "ds_b",
            "datasetSchema": [{"name": c, "type": "STRING"} for c in second_columns],
        },
        _dataset("widget", ["widget_id"]),
    ]
    return object_types, relations, datasets


def _link_dataset_pairs(model: OssieOntology, concept_name: str) -> set[tuple[str, str]]:
    """For every attached link: (dataset identifying the mapping, dataset the
    link's own expression comes from). The two must always be the same one."""
    def dataset_name(expression: Any) -> str:
        assert isinstance(expression, DatasetField)
        assert expression.dataset is not None
        return expression.dataset.name

    pairs: set[tuple[str, str]] = set()
    for om in model.ontology_mappings:
        for cm in om.concept_mappings:
            if cm.concept.name != concept_name:
                continue
            [root] = cm.object_mappings
            identifying = {dataset_name(rm.expression) for rm in (root.referent_mappings or [])}
            for link_mapping in cm.link_mappings:
                for child in link_mapping.children or []:
                    for rm in child.object_mapping.referent_mappings or []:
                        pairs |= {(name, dataset_name(rm.expression)) for name in identifying}
    return pairs


def test_link_is_attached_to_the_mapping_for_its_own_dataset(tmp_path: Path):
    """Two datasets, two mappings, and each link reads its own dataset."""
    object_types, relations, datasets = _two_dataset_export(second_carries_primary_key=True)

    model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    assert _link_dataset_pairs(model, "Gadget") == {
        ("Gadget_ds_a", "Gadget_ds_a"),
        ("Gadget_ds_b", "Gadget_ds_b"),
    }


def test_link_is_not_attached_to_a_mapping_for_another_dataset(tmp_path: Path):
    """A dataset whose mapping was dropped must not have its columns rehomed.

    The second dataset keeps the foreign-key column but loses its mapping, and
    the link built from that column has nowhere to go — attaching it to the
    first dataset's mapping would join two unrelated tables under one identity.
    """
    object_types, relations, datasets = _two_dataset_export(second_carries_primary_key=False)

    with pytest.warns(UserWarning, match="cannot attach link 'Gadget.gadget_widget'"):
        model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    assert _link_dataset_pairs(model, "Gadget") == {("Gadget_ds_a", "Gadget_ds_a")}


# ----- inheritance ------------------------------------------------------

# Palantir has no subtype construct: inheritance appears as a M:1 relation whose
# property map pairs primary key to primary key, the one side being the subtype.
# Which of those the converter honours is the same status question as everywhere
# else, decided by allowed_subtype_relation_statuses() and the endpoint policy.

def _subtype_export(
    *, ot_status: str, relation_status: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gadget as a subtype of Widget, at the given statuses."""
    object_types = [
        _object_type("widget", "Widget", status=ot_status),
        _object_type("gadget", "Gadget", status=ot_status),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status=relation_status,
        )
    ]
    return object_types, relations


def _supertype_names(model: OssieOntology, concept_name: str) -> list[str]:
    concept = model.ontology.lookup_concept(concept_name)
    assert concept is not None, f"concept '{concept_name}' was not converted"
    return [parent.name for parent in concept.extends]


def test_primary_key_to_primary_key_relation_becomes_inheritance(tmp_path: Path):
    object_types, relations = _subtype_export(ot_status="active", relation_status="active")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]
    # And only as inheritance: the relation is consumed, not also emitted as a
    # reference from the supertype to the subtype.
    assert "Widget.gadget_widget" not in _relationship_names(model)


def test_cycle_broken_subtype_relation_stays_an_ordinary_relationship(tmp_path: Path):
    """The other half of the same rule: only inheritance that was actually
    applied suppresses the relation.

    Two object types each declared a subtype of the other cannot both extend
    the other, so the topological sort drops one edge. That relation is not
    carried by any `extends`, so it has to come through as a reference — the
    skip in `_convert_relationships` is keyed on what was consumed, not on
    everything `_subtype_relations()` matched.
    """
    object_types = [
        _object_type("widget", "Widget", status="active"),
        _object_type("gadget", "Gadget", status="active"),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status="active",
        ),
        _many_to_one(
            "widget_gadget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.gadget_id"},
            status="active",
        ),
    ]
    with pytest.warns(UserWarning, match="Cycle detected"):
        model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]
    assert _supertype_names(model, "Widget") == []
    names = _relationship_names(model)
    assert "Widget.gadget_widget" not in names  # consumed as the extends above
    assert "Gadget.widget_gadget" in names  # the dropped edge, as a reference


def test_relation_that_does_not_map_primary_keys_is_not_inheritance(tmp_path: Path):
    """The structural half of the rule: a plain foreign key stays a reference."""
    object_types, relations = _linked_export(ot_status="active", relation_status="active")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == []
    assert "Gadget.gadget_widget" in _relationship_names(model)


def test_partially_mapped_composite_key_is_not_inheritance(tmp_path: Path):
    """Inheritance takes the parent's key whole, not in part.

    Widget is identified by (widget_id, wb) and the relation maps only
    widget_id, so a Gadget cannot be identified as a Widget — that is an
    ordinary foreign key. Reading it as inheritance instead reached
    `_convert_mappings`, which resolves every parent primary key through the
    property map, and died there on the unmapped `wb`.
    """
    object_types = [
        _object_type(
            "widget",
            "Widget",
            status="active",
            properties=[_prop("ri.p.wb", "wb")],
            primary_keys=["widget_id", "wb"],
        ),
        _object_type("gadget", "Gadget", status="active"),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status="active",
        )
    ]
    datasets = [_dataset("widget", ["widget_id", "wb"]), _dataset("gadget", ["gadget_id"])]

    model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    assert _supertype_names(model, "Gadget") == []
    assert "Widget.gadget_widget" in _relationship_names(model)
    # Gadget keeps its own identity, and its mapping is built rather than crashing.
    assert _identifier_columns(model, "Gadget") == ["gadget_id"]


def test_supertype_excluded_by_the_object_type_policy_is_reported(tmp_path: Path):
    """The two status policies are independent, so they can disagree.

    A DEPRECATED parent joined to an ACTIVE child by an ACTIVE PK-to-PK relation
    passes the subtype-relation gate and fails the object-type gate under the
    defaults, so there is no parent concept to extend. That has to say which
    policies disagree — as an error, not an assert, so `-O` fails here too
    instead of building `Concept(extends=[None])` and dying later on a `None`.
    """
    object_types = [
        _object_type("widget", "Widget", status="deprecated"),
        _object_type("gadget", "Gadget", status="active"),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status="active",
        )
    ]

    with pytest.raises(ValueError, match="subtype of 'Widget'"):
        _convert(tmp_path, object_types, relations=relations)


def test_deprecated_subtype_relation_is_not_read_as_inheritance(tmp_path: Path):
    object_types, relations = _subtype_export(ot_status="active", relation_status="deprecated")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == []


def test_experimental_subtype_relation_between_active_object_types_is_read(tmp_path: Path):
    """Same endpoint rule as any other experimental relation."""
    object_types, relations = _subtype_export(ot_status="active", relation_status="experimental")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]


def test_subtype_statuses_follow_the_converter_policy(tmp_path: Path):
    """Inheritance keeps step with what the converter admits as a concept.

    Widening object types alone leaves an all-experimental hierarchy flat, for
    the same reason it leaves experimental relations behind: the endpoints are
    checked separately.
    """

    class WidenedObjectTypes(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    class WidenedEndpoints(WidenedObjectTypes):
        RELATION_ENDPOINT_STATUSES = (
            PalantirToOssieConverter.RELATION_ENDPOINT_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, relations = _subtype_export(
        ot_status="experimental", relation_status="experimental"
    )

    partial = _convert(tmp_path, object_types, relations=relations, converter=WidenedObjectTypes)
    assert _supertype_names(partial, "Gadget") == []

    full = _convert(tmp_path, object_types, relations=relations, converter=WidenedEndpoints)
    assert _supertype_names(full, "Gadget") == ["Widget"]


def test_subtype_relation_statuses_can_be_widened_on_their_own(tmp_path: Path):
    """The narrower default is a policy, not a fact about the export."""

    class IntermediarySubtypes(PalantirToOssieConverter):
        SUBTYPE_RELATION_STATUSES = (
            PalantirToOssieConverter.SUBTYPE_RELATION_STATUSES | {Status.INTERMEDIARY}
        )

    object_types, relations = _subtype_export(ot_status="active", relation_status="intermediary")

    assert _supertype_names(_convert(tmp_path, object_types, relations=relations), "Gadget") == []

    widened = _convert(
        tmp_path, object_types, relations=relations, converter=IntermediarySubtypes
    )
    assert _supertype_names(widened, "Gadget") == ["Widget"]


# ----- primary key column matching --------------------------------------

def _location_export(
    *, pk_column: str, dataset_columns: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A Location whose primary key names *pk_column* in its backing dataset."""
    object_types = [
        {
            "rid": _ot_rid("location"),
            "id": "location",
            "displayName": "Location",
            "properties": [
                _prop("ri.p.locno", "locno", pk_columns={_ot_rid("location"): pk_column})
            ],
            "primaryKeys": ["locno"],
        }
    ]
    return object_types, [_dataset("location", dataset_columns)]


def test_composite_identifier_column_order_is_stable(tmp_path: Path):
    """`primary_keys()` is a set of identity-hashed properties, so iterating it
    yields a different order per run — which would reach the emitted YAML
    through referent_mappings and make any diff of it noisy.

    Converting the same export repeatedly in one process is what exposes this:
    a single conversion always looks fine.
    """
    object_types = [
        _object_type(
            "thing",
            "Thing",
            status="active",
            properties=[_prop("ri.p.bee", "bee"), _prop("ri.p.aye", "aye")],
            primary_keys=["thing_id", "bee", "aye"],
        )
    ]
    datasets = [_dataset("thing", ["thing_id", "bee", "aye"])]

    orderings = {
        tuple(_identifier_columns(_convert(tmp_path, object_types, datasets=datasets), "Thing"))
        for _ in range(20)
    }

    assert orderings == {("aye", "bee", "thing_id")}


def test_primary_key_column_is_matched_case_insensitively(tmp_path: Path):
    """`primaryKeyMapping` names the warehouse column, the dataset the API spelling."""
    object_types, datasets = _location_export(pk_column="LOCNO", dataset_columns=["locno"])
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert _identifier_columns(model, "Location") == ["locno"]


def test_exact_primary_key_column_match_wins(tmp_path: Path):
    """A case-sensitive source that carries both spellings keeps its own."""
    object_types, datasets = _location_export(
        pk_column="LOCNO", dataset_columns=["locno", "LOCNO"]
    )
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert _identifier_columns(model, "Location") == ["LOCNO"]


def test_primary_key_column_absent_from_dataset_warns(tmp_path: Path):
    """A column that is missing outright is still reported, not folded away."""
    object_types, datasets = _location_export(pk_column="MISSING", dataset_columns=["locno"])

    with pytest.warns(UserWarning, match="does not contain a field named 'MISSING'"):
        model = _convert(tmp_path, object_types, datasets=datasets)

    # Nothing identifies the concept, so the mapping is dropped along with it.
    assert not [
        cm for om in model.ontology_mappings for cm in om.concept_mappings
    ]
