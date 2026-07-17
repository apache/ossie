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

"""Round-trip tests for the model shape Omni's API/IDE actually emits.

A model pulled from a real instance (`omni models yaml-get`) differs from the
canonical `views/<name>.view.yaml` layout these converters write:

  - view files live in per-schema folders (`DELIGHTED/response.view`) and the
    model refers to them by a schema-qualified name (`delighted__response`)
    recorded in a `# Reference this view as ...` header comment;
  - joins restate Omni defaults (`join_type: always_left`,
    `relationship_type: many_to_one`) and write compound keys / on_sql with
    `${view.field}` references, mixed-case AND, and alias references;
  - extends-only views, non-equi joins, template-syntax sql, empty-string
    metadata, `timeframes: []`, camelCase/underscore-edged identifiers, and
    schema names with spaces all occur.

Every one of these was found in production models; the round trip through OSI
must reproduce the original files exactly (same paths, same parsed content).
"""

import warnings

import pytest

from osi_omni import ConversionError, convert_omni_to_osi, convert_osi_to_omni
from osi_omni._common import dump_yaml, load_yaml


REAL_FILES = {
    "model": dump_yaml({"included_schemas": ["DELIGHTED", "GITHUB"]}),
    "relationships": dump_yaml([
        # Omni defaults restated explicitly; ${view.field} refs; declared type.
        {"join_from_view": "delighted__response",
         "join_to_view": "delighted__person",
         "join_type": "always_left",
         "on_sql": "${delighted__response.person_id} = ${delighted__person.id}",
         "relationship_type": "assumed_many_to_one"},
        # Non-equi (range) join: valid Omni, no OSI form -- stashed.
        {"join_from_view": "delighted__response",
         "join_to_view": "github__team_membership",
         "join_type": "always_left",
         "on_sql": "${delighted__response.created_at} >= "
                   "${github__team_membership.valid_from}",
         "relationship_type": "many_to_one"},
        # Aliased join: on_sql references the alias, lowercase `and`.
        {"join_from_view": "github__team_membership",
         "join_to_view": "delighted__person",
         "join_to_view_as": "membership_owner",
         "on_sql": "${github__team_membership.user_id} = ${membership_owner.id} "
                   "and ${github__team_membership.org} = ${membership_owner.org}",
         "relationship_type": "many_to_one"},
        # Second join between the same pair (name dedup on the OSI side).
        {"join_from_view": "github__team_membership",
         "join_to_view": "delighted__person",
         "on_sql": "${github__team_membership.approver_id} = "
                   "${delighted__person.id}",
         "relationship_type": "many_to_one"},
    ]),
    "DELIGHTED/response.view": (
        "# Reference this view as delighted__response\n" + dump_yaml({
            "schema": "DELIGHTED", "table_name": "RESPONSE",
            "description": "",  # present-but-empty metadata survives
            "custom_compound_primary_key_sql": [
                "${delighted__response.id}", "${delighted__response.org}"],
            "dimensions": {
                "id": {"sql": '"ID"'},
                "org": {"sql": '"ORG"'},
                "person_id": {"sql": '"PERSON_ID"'},
                "created_at": {"sql": '"CREATED_AT"', "timeframes": []},
                "self_named": {"sql": "self_named"},  # explicit same-named col
                "_fivetran_id": {"sql": '"_FIVETRAN_ID"'},
                "payload_modelId": {  # camelCase (JSON-flattened) name
                    "sql": "CAST(${delighted__response.payload}['modelId'] "
                           "AS VARCHAR)"},
                "templated": {"sql": "CASE WHEN {{# delighted__response.f.filter }}"
                                     " ${id} {{/ delighted__response.f.filter }} "
                                     "THEN 1 ELSE 0 END"},
            },
            "measures": {"count": {"aggregate_type": "count"}},
        })),
    "DELIGHTED/person.view": (
        "# Reference this view as delighted__person\n" + dump_yaml({
            "schema": "DELIGHTED",  # table_name implicit = file name
            "dimensions": {"id": {"sql": '"ID"', "primary_key": True},
                           "org": {"sql": '"ORG"'}},
        })),
    "GITHUB/team_membership.view": (
        "# Reference this view as github__team_membership\n" + dump_yaml({
            "schema": "GITHUB", "table_name": "TEAM_MEMBERSHIP",
            "dimensions": {"user_id": {}, "approver_id": {}, "org": {},
                           "valid_from": {}},
        })),
    # Extends-only view: no schema/sql of its own -- preserved, not converted.
    "DELIGHTED/response_ext.view": (
        "# Reference this view as delighted__response_ext\n" + dump_yaml({
            "extends": ["delighted__response"],
            "dimensions": {"extra": {"sql": "1"}},
        })),
    # Uploaded-CSV schema with a space in its name.
    "Omni Views/upload.view": (
        "# Reference this view as upload\n" + dump_yaml({
            "schema": "Omni Views",
            "uploaded_table_name": "upload.csv::abc",
            "dimensions": {"user_id": {}},
        })),
    "Marketing/insights.topic": dump_yaml({
        "base_view": "delighted__response",
        "description": "Survey insights."}),
}


def _roundtrip(files):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        osi_yaml = convert_omni_to_osi(files)
        return osi_yaml, convert_osi_to_omni(osi_yaml)


def test_api_layout_roundtrip_is_lossless():
    _, exported = _roundtrip(REAL_FILES)
    original = {k if "." in k.split("/")[-1] else k + ".yaml": v
                for k, v in REAL_FILES.items()}
    assert set(exported) == set(original)
    for fname in original:
        assert load_yaml(exported[fname]) == load_yaml(original[fname]), fname


def test_qualified_view_names_come_from_reference_comment():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        osi = load_yaml(convert_omni_to_osi(REAL_FILES))["semantic_model"][0]
    names = {d["name"] for d in osi["datasets"]}
    assert "delighted__response" in names
    assert "upload" in names  # comment name wins even with a folder
    # The qualified name resolves relationship references and same-view
    # ${view.field} compound-key entries.
    rel = osi["relationships"][0]
    assert rel["from"] == "delighted__response"
    assert rel["to"] == "delighted__person"
    response = next(d for d in osi["datasets"]
                    if d["name"] == "delighted__response")
    assert response["primary_key"] == ["id", "org"]
    assert response["source"] == "DELIGHTED.RESPONSE"
    upload = next(d for d in osi["datasets"] if d["name"] == "upload")
    assert upload["source"] == '"Omni Views".upload'


def test_unmappable_omni_features_drop_out_of_osi_but_survive():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        osi = load_yaml(convert_omni_to_osi(REAL_FILES))["semantic_model"][0]
    # The extends view is not a dataset; the templated dimension not a field.
    assert "delighted__response_ext" not in {d["name"] for d in osi["datasets"]}
    response = next(d for d in osi["datasets"]
                    if d["name"] == "delighted__response")
    assert "templated" not in {f["name"] for f in response["fields"]}
    # The non-equi join is not an OSI relationship; the two same-pair joins get
    # unique OSI names.
    names = [r["name"] for r in osi["relationships"]]
    assert len(names) == len(set(names)) == 3


def test_duplicate_canonical_view_names_rejected():
    files = {
        "A/orders.view": "# Reference this view as orders\nschema: A\n",
        "B/orders.view": "# Reference this view as orders\nschema: B\n",
    }
    with pytest.raises(ConversionError, match="two view files"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            convert_omni_to_osi(files)
