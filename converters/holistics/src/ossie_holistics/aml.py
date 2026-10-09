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

"""A typed reading of `holistics aml compile` output.

The compiled JSON is the boundary. Everything past this module works on the
dataclasses below rather than on raw dictionaries, so a shape the compiler
changes breaks here with a named error instead of silently reaching the
converted document.

Three things in the payload are deliberately not read:

`distinct_rows` is deprecated in AML.

`models[]` on a `QueryModel` is a full inlined copy of every model the query
references. The same models are already in the dataset's own `models` list, so
reading the copy would duplicate every dataset.

`__doc__` holds the AML comment text. The forward path has nowhere to put it,
and `reverse.md` records comments as not surviving the round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import ConversionError


class ModelKind(str, Enum):
    TABLE = "table"
    QUERY = "query"


class RelationshipKind(str, Enum):
    MANY_TO_ONE = "many_to_one"
    ONE_TO_ONE = "one_to_one"
    MANY_TO_MANY = "many_to_many"
    RANGE = "range"


#: Only an equality relationship has an Ossie form. The other two carry an AQL
#: `match` condition rather than column pairs, so no column mapping exists.
EQUALITY_KINDS = frozenset({RelationshipKind.MANY_TO_ONE, RelationshipKind.ONE_TO_ONE})


@dataclass(frozen=True)
class Heredoc:
    """An AML `@sql` or `@aql` body. `language` is the heredoc's own name."""

    language: str
    content: str

    @property
    def is_aql(self) -> bool:
        return self.language == "aql"


@dataclass(frozen=True)
class Field:
    """One `dimension` or `measure`.

    `aggregation_type` is None on a dimension and set on a measure, which is the
    only structural difference between the two in the compiled payload.
    """

    name: str
    label: str | None
    description: str | None
    hidden: bool
    aml_type: str | None
    format: str | None
    definition: Heredoc | None
    primary_key: bool
    aggregation_type: str | None
    #: Set on a dataset-level dimension, naming the model it attaches to.
    owner_model_fqn: str | None = None


@dataclass(frozen=True)
class Param:
    name: str
    label: str | None
    aml_type: str | None


@dataclass(frozen=True)
class Model:
    name: str
    fqn: str
    label: str | None
    description: str | None
    owner: str | None
    data_source_name: str | None
    kind: ModelKind
    table_name: str | None
    query: Heredoc | None
    dimensions: list[Field]
    measures: list[Field]
    params: list[Param]
    persistence: dict[str, Any] | None
    aml_type: str


@dataclass(frozen=True)
class Endpoint:
    """One side of a relationship. `model` is spelled with dots, not `::`."""

    model: str
    field: str


@dataclass(frozen=True)
class Relationship:
    kind: RelationshipKind
    #: Column pairs, `from` first then each `on` block. They combine with AND.
    pairs: list[tuple[Endpoint, Endpoint]]
    active: bool
    direction: str | None
    nullable: bool | None
    rlp_propagation: str | None
    where: Any | None
    aml_type: str
    #: The payload as the compiler wrote it. A kind with no Ossie form is
    #: carried in the stash verbatim, so the reverse path can rebuild it.
    raw: dict[str, Any]


@dataclass(frozen=True)
class Dataset:
    name: str
    fqn: str
    label: str | None
    description: str | None
    owner: str | None
    data_source_name: str | None
    models: list[Model]
    relationships: list[Relationship]
    #: Dataset-level `dimension` entries, each naming its owning model.
    dimensions: list[Field]
    #: Dataset-level `metric` entries, always AQL in practice.
    metrics: list[Field]

    def model_by_fqn(self) -> dict[str, Model]:
        return {model.fqn: model for model in self.models}


def _require(payload: Any, key: str, where: str) -> Any:
    if not isinstance(payload, dict) or key not in payload:
        raise ConversionError(f"{where}: compiled AML is missing {key!r}")
    return payload[key]


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    """A string property, with the compiler's empty string read as absent.

    `holistics aml compile` writes `""` for a property the source omits, and an
    empty description is not a description.
    """
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _heredoc(payload: Any, where: str) -> Heredoc | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ConversionError(f"{where}: expected a heredoc object, got {type(payload).__name__}")
    return Heredoc(
        language=_require(payload, "name", where),
        content=_require(payload, "content", where),
    )


def _ordered(container: Any, where: str) -> list[dict[str, Any]]:
    """Members of a `dimension`/`measure`/`param` block in AML source order.

    The compiler writes these as an object keyed by name, and a dataset-level
    `dimension` as an array. `__childIdx__` is the source position, used as the
    sort key so the converted document lists fields in the order the AML author
    wrote them rather than in whatever order the payload happens to hold.
    """
    if container is None:
        return []
    if isinstance(container, dict):
        members = list(container.values())
    elif isinstance(container, list):
        members = list(container)
    else:
        raise ConversionError(f"{where}: expected an object or array")
    return sorted(members, key=lambda m: m.get("__childIdx__", 0))


def _field(payload: dict[str, Any], where: str) -> Field:
    name = _require(payload, "name", where)
    return Field(
        name=name,
        label=_optional_text(payload, "label"),
        description=_optional_text(payload, "description"),
        hidden=bool(payload.get("hidden", False)),
        aml_type=payload.get("type"),
        format=_optional_text(payload, "format"),
        definition=_heredoc(payload.get("definition"), f"{where}.{name}"),
        primary_key=bool(payload.get("primary_key", False)),
        aggregation_type=payload.get("aggregation_type"),
        owner_model_fqn=(payload.get("model") or {}).get("__fqn__"),
    )


def _param(payload: dict[str, Any], where: str) -> Param:
    return Param(
        name=_require(payload, "name", where),
        label=_optional_text(payload, "label"),
        aml_type=payload.get("type"),
    )


def _model(payload: dict[str, Any]) -> Model:
    name = _require(payload, "name", "model")
    where = f"model {name!r}"
    raw_kind = _require(payload, "type", where)
    try:
        kind = ModelKind(raw_kind)
    except ValueError as exc:
        raise ConversionError(f"{where}: unknown model type {raw_kind!r}") from exc
    return Model(
        name=name,
        fqn=payload.get("__fqn__") or name,
        label=_optional_text(payload, "label"),
        description=_optional_text(payload, "description"),
        owner=_optional_text(payload, "owner"),
        data_source_name=_optional_text(payload, "data_source_name"),
        kind=kind,
        table_name=_optional_text(payload, "table_name"),
        query=_heredoc(payload.get("query"), where),
        dimensions=[_field(d, where) for d in _ordered(payload.get("dimension"), where)],
        measures=[_field(m, where) for m in _ordered(payload.get("measure"), where)],
        params=[_param(p, where) for p in _ordered(payload.get("param"), where)],
        persistence=payload.get("persistence"),
        aml_type=payload.get("__type__", ""),
    )


def _endpoint(payload: dict[str, Any], where: str) -> Endpoint:
    return Endpoint(
        model=_require(payload, "model", where),
        field=_require(payload, "field", where),
    )


def _relationship(payload: dict[str, Any], index: int) -> Relationship:
    where = f"relationship {index}"
    rel = _require(payload, "rel", where)
    raw_kind = _require(rel, "type", where)
    try:
        kind = RelationshipKind(raw_kind)
    except ValueError as exc:
        raise ConversionError(f"{where}: unknown relationship type {raw_kind!r}") from exc

    pairs: list[tuple[Endpoint, Endpoint]] = []
    if "from" in rel and "to" in rel:
        pairs.append((_endpoint(rel["from"], where), _endpoint(rel["to"], where)))
    for extra in _ordered(rel.get("on"), where):
        pairs.append((_endpoint(extra["from"], where), _endpoint(extra["to"], where)))

    return Relationship(
        kind=kind,
        pairs=pairs,
        active=bool(payload.get("active", True)),
        direction=payload.get("direction"),
        nullable=payload.get("nullable"),
        rlp_propagation=payload.get("rlp_propagation"),
        where=rel.get("where"),
        aml_type=rel.get("__type__", ""),
        raw=payload,
    )


def parse(payload: Any) -> Dataset:
    """Read one compiled `*.dataset.aml.json` document."""
    if not isinstance(payload, dict):
        raise ConversionError("compiled AML is not an object")
    if payload.get("__type__") != "Dataset":
        raise ConversionError(
            f"expected a compiled Dataset, got __type__={payload.get('__type__')!r}"
        )
    name = _require(payload, "name", "dataset")
    return Dataset(
        name=name,
        fqn=payload.get("__fqn__") or name,
        label=_optional_text(payload, "label"),
        description=_optional_text(payload, "description"),
        owner=_optional_text(payload, "owner"),
        data_source_name=_optional_text(payload, "data_source_name"),
        models=[_model(m) for m in payload.get("models") or []],
        relationships=[
            _relationship(r, i) for i, r in enumerate(payload.get("relationships") or [])
        ],
        dimensions=[_field(d, "dataset") for d in _ordered(payload.get("dimension"), "dataset")],
        metrics=[_field(m, "dataset") for m in _ordered(payload.get("metric"), "dataset")],
    )
