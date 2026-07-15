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

"""Data models for GoodData declarative LDM and Ossie semantic model.

GoodData LDM structure (from declarative API):
  DeclarativeModel
    └── ldm
        ├── datasets[]
        │   ├── id, title, description, tags
        │   ├── dataSourceTableId {id, dataSourceId, type, path[]}
        │   ├── grain[] {id, type}
        │   ├── attributes[] {id, title, description, sourceColumn, sourceColumnDataType,
        │   │                  sortColumn, sortDirection, labels[], tags}
        │   ├── facts[] {id, title, description, sourceColumn, sourceColumnDataType, tags}
        │   ├── references[] {identifier{id, type}, multivalue,
        │   │                  sources[] {column, dataType, target{id, type}}}
        │   └── workspaceDataFilterReferences[]
        └── dateInstances[]
            ├── id, title, description, tags
            ├── granularities[]
            └── granularitiesFormatting {titleBase, titlePattern}

Ossie semantic model structure:
  version, semantic_model[]
    ├── name, description, ai_context, custom_extensions[]
    ├── datasets[] {name, source, primary_key[], fields[], custom_extensions[]}
    ├── relationships[] {name, from, to, from_columns[], to_columns[]}
    └── metrics[] {name, expression{dialects[]}, description}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- GoodData Declarative LDM types ---


@dataclass
class GdLabel:
    id: str
    title: str
    source_column: str
    description: str = ""
    value_type: str = "TEXT"
    tags: list[str] = field(default_factory=list)


@dataclass
class GdAttribute:
    id: str
    title: str
    source_column: str
    description: str = ""
    source_column_data_type: str = "STRING"
    sort_column: str | None = None
    sort_direction: str | None = None
    labels: list[GdLabel] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class GdFact:
    id: str
    title: str
    source_column: str
    description: str = ""
    source_column_data_type: str = "NUMERIC"
    tags: list[str] = field(default_factory=list)


@dataclass
class GdGrain:
    id: str
    type: str = "attribute"


@dataclass
class GdReferenceIdentifier:
    id: str
    type: str = "dataset"


@dataclass
class GdReferenceTarget:
    id: str
    type: str = "attribute"


@dataclass
class GdReferenceSource:
    column: str
    target: GdReferenceTarget
    data_type: str | None = None


@dataclass
class GdReference:
    identifier: GdReferenceIdentifier
    sources: list[GdReferenceSource] = field(default_factory=list)
    multivalue: bool = False


@dataclass
class GdDataSourceTableId:
    id: str
    data_source_id: str
    type: str = "dataSource"
    path: list[str] = field(default_factory=list)


@dataclass
class GdDataset:
    id: str
    title: str
    grain: list[GdGrain] = field(default_factory=list)
    references: list[GdReference] = field(default_factory=list)
    attributes: list[GdAttribute] = field(default_factory=list)
    facts: list[GdFact] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    data_source_table_id: GdDataSourceTableId | None = None


@dataclass
class GdGranularitiesFormatting:
    title_base: str = ""
    title_pattern: str = "%granularityTitle (%titleBase)"


@dataclass
class GdDateInstance:
    id: str
    title: str
    description: str = ""
    granularities: list[str] = field(default_factory=list)
    granularities_formatting: GdGranularitiesFormatting = field(default_factory=GdGranularitiesFormatting)
    tags: list[str] = field(default_factory=list)


@dataclass
class GdLdm:
    datasets: list[GdDataset] = field(default_factory=list)
    date_instances: list[GdDateInstance] = field(default_factory=list)


@dataclass
class GdDeclarativeModel:
    ldm: GdLdm = field(default_factory=GdLdm)


# --- Serialization helpers ---


def gd_model_from_dict(data: dict[str, Any]) -> GdDeclarativeModel:
    """Parse a GoodData declarative model JSON dict into typed dataclasses."""
    ldm_data = data.get("ldm", {})

    datasets = []
    for ds in ldm_data.get("datasets", []):
        attributes = []
        for attr in ds.get("attributes", []):
            labels = [
                GdLabel(
                    id=lb["id"],
                    title=lb.get("title", ""),
                    source_column=lb.get("sourceColumn", ""),
                    description=lb.get("description", ""),
                    value_type=lb.get("valueType", "TEXT"),
                    tags=lb.get("tags", []),
                )
                for lb in attr.get("labels", [])
            ]
            attributes.append(
                GdAttribute(
                    id=attr["id"],
                    title=attr.get("title", ""),
                    source_column=attr.get("sourceColumn", ""),
                    description=attr.get("description", ""),
                    source_column_data_type=attr.get("sourceColumnDataType", "STRING"),
                    sort_column=attr.get("sortColumn"),
                    sort_direction=attr.get("sortDirection"),
                    labels=labels,
                    tags=attr.get("tags", []),
                )
            )

        facts = [
            GdFact(
                id=f["id"],
                title=f.get("title", ""),
                source_column=f.get("sourceColumn", ""),
                description=f.get("description", ""),
                source_column_data_type=f.get("sourceColumnDataType", "NUMERIC"),
                tags=f.get("tags", []),
            )
            for f in ds.get("facts", [])
        ]

        grain = [GdGrain(id=g["id"], type=g.get("type", "attribute")) for g in ds.get("grain", [])]

        references = []
        for ref in ds.get("references", []):
            ident = ref["identifier"]
            if "sources" not in ref:
                raise ValueError(
                    f"Dataset '{ds['id']}' reference to '{ident['id']}' is missing 'sources'. "
                    "The legacy 'sourceColumns' format is not supported; please upgrade the LDM "
                    "to the new 'sources' format (see DeclarativeReferenceSource in GoodData backend)."
                )
            sources = [
                GdReferenceSource(
                    column=s["column"],
                    target=GdReferenceTarget(
                        id=s["target"]["id"],
                        type=s["target"].get("type", "attribute"),
                    ),
                    data_type=s.get("dataType"),
                )
                for s in ref["sources"]
            ]
            references.append(
                GdReference(
                    identifier=GdReferenceIdentifier(id=ident["id"], type=ident.get("type", "dataset")),
                    sources=sources,
                    multivalue=ref.get("multivalue", False),
                )
            )

        ds_table_id = None
        if "dataSourceTableId" in ds:
            t = ds["dataSourceTableId"]
            ds_table_id = GdDataSourceTableId(
                id=t["id"],
                data_source_id=t.get("dataSourceId", ""),
                type=t.get("type", "dataSource"),
                path=t.get("path", []),
            )

        datasets.append(
            GdDataset(
                id=ds["id"],
                title=ds.get("title", ""),
                grain=grain,
                references=references,
                attributes=attributes,
                facts=facts,
                description=ds.get("description", ""),
                tags=ds.get("tags", []),
                data_source_table_id=ds_table_id,
            )
        )

    date_instances = []
    for di in ldm_data.get("dateInstances", []):
        fmt = di.get("granularitiesFormatting", {})
        date_instances.append(
            GdDateInstance(
                id=di["id"],
                title=di.get("title", ""),
                description=di.get("description", ""),
                granularities=di.get("granularities", []),
                granularities_formatting=GdGranularitiesFormatting(
                    title_base=fmt.get("titleBase", ""),
                    title_pattern=fmt.get("titlePattern", "%granularityTitle (%titleBase)"),
                ),
                tags=di.get("tags", []),
            )
        )

    return GdDeclarativeModel(ldm=GdLdm(datasets=datasets, date_instances=date_instances))


def gd_model_to_dict(model: GdDeclarativeModel) -> dict[str, Any]:
    """Serialize a GoodData declarative model to a JSON-compatible dict."""
    datasets = []
    for ds in model.ldm.datasets:
        ds_dict: dict[str, Any] = {
            "id": ds.id,
            "title": ds.title,
            "grain": [{"id": g.id, "type": g.type} for g in ds.grain],
            "references": [_reference_to_dict(ref) for ref in ds.references],
            "attributes": [_attr_to_dict(a) for a in ds.attributes],
            "facts": [_fact_to_dict(f) for f in ds.facts],
        }
        if ds.description:
            ds_dict["description"] = ds.description
        if ds.tags:
            ds_dict["tags"] = ds.tags
        if ds.data_source_table_id:
            t = ds.data_source_table_id
            ds_table: dict[str, Any] = {"id": t.id, "dataSourceId": t.data_source_id, "type": t.type}
            if t.path:
                ds_table["path"] = t.path
            ds_dict["dataSourceTableId"] = ds_table
        datasets.append(ds_dict)

    date_instances = []
    for di in model.ldm.date_instances:
        di_dict: dict[str, Any] = {
            "id": di.id,
            "title": di.title,
            "granularities": di.granularities,
            "granularitiesFormatting": {
                "titleBase": di.granularities_formatting.title_base,
                "titlePattern": di.granularities_formatting.title_pattern,
            },
        }
        if di.description:
            di_dict["description"] = di.description
        if di.tags:
            di_dict["tags"] = di.tags
        date_instances.append(di_dict)

    return {"ldm": {"datasets": datasets, "dateInstances": date_instances}}


def _reference_to_dict(ref: GdReference) -> dict[str, Any]:
    sources = []
    for s in ref.sources:
        sd: dict[str, Any] = {
            "column": s.column,
            "target": {"id": s.target.id, "type": s.target.type},
        }
        if s.data_type is not None:
            sd["dataType"] = s.data_type
        sources.append(sd)
    return {
        "identifier": {"id": ref.identifier.id, "type": ref.identifier.type},
        "multivalue": ref.multivalue,
        "sources": sources,
    }


def _attr_to_dict(a: GdAttribute) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": a.id,
        "title": a.title,
        "sourceColumn": a.source_column,
        "labels": [
            {
                "id": lb.id,
                "title": lb.title,
                "sourceColumn": lb.source_column,
                **({"description": lb.description} if lb.description else {}),
                **({"valueType": lb.value_type} if lb.value_type != "TEXT" else {}),
                **({"tags": lb.tags} if lb.tags else {}),
            }
            for lb in a.labels
        ],
    }
    if a.description:
        d["description"] = a.description
    if a.source_column_data_type != "STRING":
        d["sourceColumnDataType"] = a.source_column_data_type
    if a.sort_column:
        d["sortColumn"] = a.sort_column
    if a.sort_direction:
        d["sortDirection"] = a.sort_direction
    if a.tags:
        d["tags"] = a.tags
    return d


def _fact_to_dict(f: GdFact) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": f.id,
        "title": f.title,
        "sourceColumn": f.source_column,
    }
    if f.description:
        d["description"] = f.description
    if f.source_column_data_type != "NUMERIC":
        d["sourceColumnDataType"] = f.source_column_data_type
    if f.tags:
        d["tags"] = f.tags
    return d
