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

"""Builders for Palantir export JSON.

The export format is described in one place here rather than in each suite that
needs it: the parser suite next door reads exports off disk and the suites in
this package convert them, and both otherwise know the same nesting of rids,
`baseType` wrappers and `primaryKeyMapping` spellings. A change to the format is
then a change to this file.
"""

from __future__ import annotations

from typing import Any, Iterable

def prop(
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

def object_type(
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
    props = [prop(f"ri.p.{key}_id", f"{key}_id"), *properties]
    ot: dict[str, Any] = {
        "rid": ot_rid(key),
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

def many_to_one(
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
                "objectTypeRidOneSide": ot_rid(one),
                "objectTypeRidManySide": ot_rid(many),
                "oneSidePrimaryKeyToManySidePropertyMapping": key_map,
            },
        },
    }
    if status:
        relation["status"] = {"type": status}
    return relation

def many_to_many(key: str, *, a: str, b: str, status: str | None = None) -> dict[str, Any]:
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
                "objectTypeRidA": ot_rid(a),
                "objectTypeRidB": ot_rid(b),
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

def intermediary(
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
                "objectTypeRidA": ot_rid(a),
                "objectTypeRidB": ot_rid(b),
                "intermediaryObjectTypeRid": ot_rid(via),
                "aToIntermediaryLinkTypeRid": f"ri.rel.{link_a}",
                "intermediaryToBLinkTypeRid": f"ri.rel.{link_b}",
            },
        },
    }
    if status:
        relation["status"] = {"type": status}
    return relation

def dataset(key: str, columns: Iterable[str]) -> dict[str, Any]:
    """The dataset backing object type *key*, with the columns named."""
    return {
        "mainDatasetId": ot_rid(key),
        "datasetName": f"{key}_table",
        "datasetSchema": [{"name": column, "type": "STRING"} for column in columns],
    }

def ot_rid(key: str) -> str:
    return f"ri.ot.{key}"

