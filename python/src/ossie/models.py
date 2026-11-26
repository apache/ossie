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

from __future__ import annotations

import json
import math
import os
import re
import stat
from collections import Counter
from collections.abc import Callable, Iterator
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote, unquote_to_bytes

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    StrictBool,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)

from ossie._loading import UniqueKeyLoader, unique_json_object

_AI_CONTEXT_FIELDS = frozenset({"instructions", "synonyms", "examples"})
_AI_CONTEXT_EXTRAS = TypeAdapter(dict[str, Any])

#: Paths listed in a diagnostic before the rest are counted instead.
_MAX_REPORTED = 20

# Each reads as a whole clause, so one can be followed by the paths it applies to.
_NULL = "property set to null, which the schema does not allow; omit it instead"
_NON_FINITE = "property holds a number JSON cannot represent, so it cannot round-trip"
_NOT_JSON = "property holds a value JSON cannot represent, so it cannot round-trip"
_NOT_STRING_KEY = "mapping key is not a string, which JSON cannot represent"

_JSON_TYPES = (bool, int, float, str, dict, list)
# Properties whose value is an open object of arbitrary JSON.
_JSON_OBJECTS = frozenset({"data", "annotations"})


def _cyclic(path: str) -> ValueError:
    return ValueError(
        f"document contains a cyclic alias at {path or 'the root'}, which cannot be represented as an Ossie document"
    )


def _invalid_below(
    node: Any,
    path: str,
    active: tuple[int, ...],
    walked: set[tuple[int, bool, bool, bool]],
    free_form: bool,
    ai_context: bool,
    extension_data: bool,
) -> list[tuple[str, str]]:
    """Paths below `node` holding a value an Ossie document cannot carry.

    A null is one, because no property in `ossie-schema.json` is nullable. So is a
    non-finite number: `yaml.safe_load` reads `.nan` and `.inf`, and `json.loads`
    accepts the non-standard `NaN`, `Infinity` and `-Infinity`, none of which JSON can
    express -- a `NaN` reaching `to_ossie_json` comes back out as `null`.

    `free_form` marks the parts of `ai_context` where a null is legal: its object form
    sets `additionalProperties: true`, so an extra key may hold any value. A non-finite
    number is rejected there too, since it still cannot be serialized.

    `extension_data` marks values under `nonportable_extensions` ``data`` and under an
    ontology's ``annotations``. Those objects must be JSON: any non-JSON value (such
    as a YAML date, timestamp, time, set, bytes, or ``!!omap``/``!!pairs`` tuple) or a
    non-string mapping key is a schema error. The same YAML forms stay allowed inside
    ``ai_context`` extras.

    `active` holds the containers on the current branch, so a self-reference raises
    while an anchor reused on a sibling branch still walks. `walked` holds containers
    already covered: one reached again through another alias is not reported twice,
    because an anchor is one property in the source, and because a chain of anchors
    shaped `[*previous, *previous]` expands to 2^n copies of the anchored subtree.
    """
    if isinstance(node, float) and not math.isfinite(node):
        return [(path, _NON_FINITE)]
    if extension_data and node is not None and not isinstance(node, _JSON_TYPES):
        return [(path, _NOT_JSON)]
    if not isinstance(node, (dict, list)):
        return []
    if id(node) in active:
        raise _cyclic(path)
    seen = (id(node), free_form, ai_context, extension_data)
    if seen in walked:
        return []
    walked.add(seen)
    inner = (*active, id(node))
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if extension_data and not isinstance(key, str):
                where = f"{path}[{key!r}]" if path else repr(key)
                found.append((where, _NOT_STRING_KEY))
            else:
                where = f"{path}.{key}" if path else str(key)
            legal_null = free_form or (ai_context and key not in _AI_CONTEXT_FIELDS)
            if value is None:
                if not legal_null:
                    found.append((where, _NULL))
            else:
                # Null is legal inside nonportable_extensions data. The data
                # property itself stays required; only values under that object
                # may be null. A non-finite number is still rejected. A YAML
                # date, set, or bytes value is rejected only under data, not
                # under ai_context extras. Nested extras reset ai_context, so
                # the first turn-on also requires not free_form. An ontology's
                # annotations hold any JSON too, so they follow the same rules.
                found += _invalid_below(
                    value,
                    where,
                    inner,
                    walked,
                    legal_null or key in _JSON_OBJECTS,
                    key == "ai_context",
                    extension_data or (not free_form and not ai_context and key in _JSON_OBJECTS),
                )
    else:
        for index, item in enumerate(node):
            found += _invalid_below(
                item,
                f"{path}[{index}]",
                inner,
                walked,
                free_form,
                False,
                extension_data,
            )
    return found


def _listed(paths: list[str]) -> str:
    """Join paths for a diagnostic.

    A document may hold many; list enough to act on and count the rest.
    """
    listed = ", ".join(paths[:_MAX_REPORTED])
    if len(paths) > _MAX_REPORTED:
        listed += f", and {len(paths) - _MAX_REPORTED} more"
    return listed


def _invalid_properties(node: Any) -> list[tuple[str, str]]:
    """Return every property holding a value an Ossie document cannot carry.

    Raises `ValueError` if the document holds a cyclic alias.
    """
    return _invalid_below(node, "", (), set(), False, False, False)


class OssieDialect(str, Enum):  # noqa: UP042
    """Well-known dialect values.

    The field accepts any string; see `extensions/dialects.md`.
    """

    ANSI_SQL = "ANSI_SQL"
    SNOWFLAKE = "SNOWFLAKE"
    MDX = "MDX"
    MAQL = "MAQL"
    TABLEAU = "TABLEAU"
    DATABRICKS = "DATABRICKS"
    BIGQUERY = "BIGQUERY"
    SIGMA = "SIGMA"
    THOUGHTSPOT = "THOUGHTSPOT"
    DAX = "DAX"
    HOLISTICS_AQL = "HOLISTICS_AQL"
    OSSIE_SQL_2026 = "OSSIE_SQL_2026"
    SPARQL = "SPARQL"
    TRINO = "TRINO"
    CLICKHOUSE = "CLICKHOUSE"
    POSTGRES = "POSTGRES"
    REDSHIFT = "REDSHIFT"
    PYICEBERG = "PYICEBERG"
    OMNI = "OMNI"


class OssieDataType(str, Enum):  # noqa: UP042
    """Portable logical data types for fields and metric results."""

    STRING = "String"
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    FLOAT = "Float"
    BOOLEAN = "Boolean"
    DATE = "Date"
    TIME = "Time"
    DATE_TIME = "DateTime"
    DATE_TIME_TZ = "DateTimeTz"
    OPAQUE = "Opaque"


_TEMPORAL_DATA_TYPES = frozenset(
    {
        OssieDataType.DATE,
        OssieDataType.TIME,
        OssieDataType.DATE_TIME,
        OssieDataType.DATE_TIME_TZ,
    }
)


class OssieVendor(str, Enum):  # noqa: UP042
    """Well-known vendor values.

    The field accepts any string; see `extensions/vendors.md`.
    """

    SNOWFLAKE = "SNOWFLAKE"
    SALESFORCE = "SALESFORCE"
    DBT = "DBT"
    DATABRICKS = "DATABRICKS"
    GOODDATA = "GOODDATA"
    SEMANTIDO = "SEMANTIDO"
    HONEYDEW = "HONEYDEW"
    WISDOM = "WISDOM"
    POWER_BI = "POWER_BI"
    SIGMA = "SIGMA"
    THOUGHTSPOT = "THOUGHTSPOT"
    HEX = "HEX"
    ATACCAMA = "ATACCAMA"
    OMNI = "OMNI"
    PRECISELY = "PRECISELY"
    GASP = "GASP"


class OssieAIContextObject(BaseModel):
    """Structured AI context; free-form extras retain null values when serialized."""

    model_config = ConfigDict(frozen=True, extra="allow")

    instructions: str | None = None
    synonyms: tuple[str, ...] | None = None
    examples: tuple[str, ...] | None = None

    @model_serializer(mode="wrap")
    def _serialize_context(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo):
        data = handler(self)
        if info.exclude_none and self.model_extra:
            # Null extras are valid data, unlike omitted optional model fields.
            # Let Pydantic apply include/exclude so an excluded key stays excluded.
            null_extras = {key: value for key, value in self.model_extra.items() if value is None}
            data.update(_AI_CONTEXT_EXTRAS.dump_python(null_extras, include=info.include, exclude=info.exclude))
        return data


OssieAIContext = str | OssieAIContextObject


class OssieNonportableExtension(BaseModel):
    """Vendor-specific metadata as a structured object with vendor-defined keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_name: str = Field(min_length=1)
    data: dict[str, Any]


class OssieDialectExpression(BaseModel):
    """Expression in a specific dialect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Free-form; see OssieDialect for the well-known values.
    dialect: str = Field(min_length=1)
    expression: str = Field(min_length=1)


class OssieExpression(BaseModel):
    """Expression definition with multi-dialect support."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Primary, portable expression written in OSSIE_SQL_2026.
    sql: str | None = Field(default=None, min_length=1)
    dialects: list[OssieDialectExpression] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_sql_or_dialects(self) -> OssieExpression:
        if self.sql is None and not self.dialects:
            raise ValueError("expression requires at least one of 'sql' or 'dialects'")
        return self


class OssieDimension(BaseModel):
    """Dimension metadata on a field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The schema types this `boolean`, which admits only true and false. A plain
    # `bool` would let pydantic's lax mode accept "yes", "1", 1 or 1.0 and store a
    # real boolean, so input the schema forbids is silently rewritten into output it
    # allows, and the mistake is never reported.
    is_time: StrictBool | None = None


class OssieField(BaseModel):
    """Row-level attribute for grouping, filtering, and metric expressions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    expression: OssieExpression
    dimension: OssieDimension | None = None
    label: str | None = None
    description: str | None = None
    datatype: OssieDataType | None = None
    ai_context: OssieAIContext | None = None
    nonportable_extensions: list[OssieNonportableExtension] | None = None

    def is_time_dimension(self) -> bool:
        """Return the field's effective temporal-dimension role.

        A field must have dimension metadata to be a dimension. Within that
        block, an explicit ``is_time`` value takes precedence; otherwise the
        role defaults from a temporal ``datatype``.
        """
        if self.dimension is None:
            return False
        if self.dimension.is_time is not None:
            return self.dimension.is_time
        return self.datatype in _TEMPORAL_DATA_TYPES


class OssieMetric(BaseModel):
    """Quantitative measure defined on business data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    expression: OssieExpression
    description: str | None = None
    datatype: OssieDataType | None = None
    ai_context: OssieAIContext | None = None
    nonportable_extensions: list[OssieNonportableExtension] | None = None


def _distinct(names: list[str]) -> list[str]:
    """Mirror the schema's `uniqueItems`, which `Field` cannot express.

    Names are compared as written, as the keyword does, so this serves one key:
    `primary_key` and each definition inside `unique_keys`. The ontology's name and
    expression lists (`extends`, `identify_by`, `derived_by`, `requires`,
    `verbalizes`) use it the same way.
    """
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"repeats {name!r}")
        seen.add(name)
    return names


class OssieDataset(BaseModel):
    """Logical dataset representing a business entity (fact or dimension table)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    # The schema gives both `minItems: 1`, as it does the relationship column arrays.
    # An empty key names nothing, and `Field` mirrors minItems as `min_length` here.
    # Each key is also `uniqueItems`: one that names a field twice declares fewer
    # columns than it appears to. The list of definitions is not, since two holding
    # the same key are redundant rather than wrong; validate.py warns about them,
    # reordered and recased ones included, which a schema keyword cannot reach.
    primary_key: Annotated[list[str], AfterValidator(_distinct)] | None = Field(default=None, min_length=1)
    unique_keys: list[Annotated[list[str], Field(min_length=1), AfterValidator(_distinct)]] | None = Field(
        default=None, min_length=1
    )
    description: str | None = None
    ai_context: OssieAIContext | None = None
    fields: list[OssieField] | None = None
    # Dataset-scoped: expressions reference this dataset's declared fields by single-part
    # (unqualified) name and its source columns as source.column.
    metrics: list[OssieMetric] | None = None
    nonportable_extensions: list[OssieNonportableExtension] | None = None


class OssieRelationship(BaseModel):
    """Foreign key relationship between datasets."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str = Field(..., min_length=1)
    from_dataset: str = Field(..., alias="from", min_length=1)
    to: str = Field(..., min_length=1)
    from_columns: list[str] = Field(..., min_length=1)
    to_columns: list[str] = Field(..., min_length=1)
    ai_context: OssieAIContext | None = None
    nonportable_extensions: list[OssieNonportableExtension] | None = None


# The specification version every Ossie document declares. Each schema pins it with
# `const`, so a document may not declare any other version. Mirrored as a Literal
# rather than a plain str, which would let the package serialize a version the
# schema rejects.
OssieVersion = Literal["0.3.0.dev0"]


class OssieDocument(BaseModel):
    """The root of an Ossie file: the `version` it declares and how it is read and written.

    `OssieSemanticModel` is a semantic model file and `OssieOntologyModel` an ontology file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: OssieVersion = "0.3.0.dev0"

    @model_serializer(mode="wrap")
    def _serialize_document(self, handler: SerializerFunctionWrapHandler):
        # Omit a custom return type to retain Pydantic's serialization schema.
        data = handler(self)
        if "version" in data:
            return {"version": data.pop("version"), **data}
        return data

    def to_ossie_yaml(self, **kwargs: Any) -> str:
        """Serialize to Ossie YAML using aliases and omitting optional fields set to None.

        Null-valued AI context extras are preserved. Both defaults can be overridden
        through kwargs. ``mode`` cannot: YAML output needs the plain scalars only the
        JSON mode produces.
        """
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("exclude_none", True)
        data = self.model_dump(mode="json", **kwargs)
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def to_ossie_json(self, **kwargs: Any) -> str:
        """Serialize to Ossie JSON using aliases and omitting optional fields set to None.

        Null-valued AI context extras are preserved. Both defaults can be overridden
        through kwargs.
        """
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("exclude_none", True)
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_ossie_yaml(cls, text: str) -> Self:
        """Parse an Ossie YAML document, checking what pydantic alone cannot.

        The counterpart of :meth:`to_ossie_yaml`. ``model_validate`` stays lenient,
        because a caller building a model in Python routinely passes a value that may
        be ``None`` -- ``label=dim.label`` -- and means "omit it". A document is
        different: it is the artefact the schema governs, so these rules apply to it
        and not to the models.

        Rejected here: a duplicate mapping key, a property set to null, a missing
        ``version`` (on the document or on a document embedded in it, such as an
        ontology mapping's ``semantic_model``), a non-finite number, a cyclic alias,
        and under ``nonportable_extensions`` ``data`` or an ontology's
        ``annotations`` any non-JSON value (such as a YAML date, timestamp, time,
        set, bytes, or ``!!omap``/``!!pairs`` tuple) or a non-string mapping key. A
        null inside those objects is kept; they hold arbitrary JSON. YAML merge
        overrides remain supported. The field types reject the rest of the schema.

        A diagnostic lists at most :data:`_MAX_REPORTED` paths and counts the rest, so
        fixing the ones named can reveal more on the next run.

        Not checked: the free-form content of ``ai_context``, whose object form sets
        ``additionalProperties: true``. A value there outside JSON's own data model --
        a YAML set or date, a non-string mapping key -- is passed through and
        serialized as whatever it maps to, exactly as ``model_validate`` does today.
        Auditing that, and bounding how far a shared alias graph expands on the way
        out, is a property of the models rather than of parsing, and is not in scope
        here.
        """
        return cls._from_document(yaml.load(text, Loader=UniqueKeyLoader))

    @classmethod
    def from_ossie_json(cls, text: str) -> Self:
        """Parse an Ossie JSON document, checking what pydantic alone cannot.

        The counterpart of :meth:`to_ossie_json`; see :meth:`from_ossie_yaml` for what
        is and is not checked.
        """
        return cls._from_document(json.loads(text, object_pairs_hook=unique_json_object))

    @classmethod
    def _embedded_documents(cls, data: dict) -> Iterator[tuple[str, Any]]:
        """Yield the path and value of each whole document nested in `data`.

        Each carries its own required ``version``. A core document embeds none.
        """
        return iter(())

    @classmethod
    def _from_document(cls, data: Any) -> Self:
        invalid = _invalid_properties(data)
        for reason in (_NULL, _NON_FINITE, _NOT_JSON, _NOT_STRING_KEY):
            paths = [path for path, why in invalid if why == reason]
            if paths:
                raise ValueError(f"{reason}: {_listed(paths)}")
        if isinstance(data, dict):
            # The field carries a default so Python construction needs no version, but
            # the schema lists it as required, so a document without one is invalid --
            # and so is an embedded one, which would otherwise be written back with
            # the default filled in.
            documents = [("", data), *cls._embedded_documents(data)]
            missing = [
                f"{path}.version" if path else "version"
                for path, document in documents
                if isinstance(document, dict) and "version" not in document
            ]
            if missing:
                raise ValueError(f"document is missing the required property: {_listed(missing)}")
        return cls.model_validate(data)


class OssieSemanticModel(OssieDocument):
    """Semantic model: the datasets, relationships, and metrics for one business domain."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    ai_context: OssieAIContext | None = None
    datasets: list[OssieDataset] = Field(..., min_length=1)
    relationships: list[OssieRelationship] | None = None
    # Model-scoped: expressions reference fields by multi-part name (dataset.field) and may span datasets.
    metrics: list[OssieMetric] | None = None
    nonportable_extensions: list[OssieNonportableExtension] | None = None

    @classmethod
    def from_ossie_path(cls, path: str | os.PathLike[str]) -> OssieSemanticModel:
        """Read a semantic model file, resolving a split model document from the dataset documents in its folder."""
        path = Path(path)
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        if isinstance(data, dict) and "dataset_refs" in data:
            return OssieSplitSemanticModel._from_document(data).resolve(_folder_loader(path.parent), model_label=str(path))
        return cls._from_document(data)

    def split(
        self, file_name: Callable[[OssieDataset], str] | None = None
    ) -> tuple[OssieSplitSemanticModel, list[OssieDatasetDocument]]:
        """Split into a split model document and one dataset document per dataset, in `datasets` order.

        `file_name` names each dataset's file, by default ``<dataset name>.dataset.yaml``.
        Raises `ValueError` if a file name is not one path segment, or two differ only in case.
        """
        document, datasets, _ = self._split(file_name)
        return document, datasets

    def to_ossie_folder(
        self,
        folder: str | os.PathLike[str],
        model_file_name: str,
        file_name: Callable[[OssieDataset], str] | None = None,
        overwrite: bool = False,
    ) -> None:
        """Write the split of this model into `folder` as YAML files.

        Every name is checked before anything is written. Raises `FileExistsError` if a
        target exists, unless `overwrite`.
        """
        document, datasets, names = self._split(file_name)
        _check_file_names([*names, model_file_name])
        folder = Path(folder)
        targets = [folder / name for name in names]
        model_path = folder / model_file_name
        existing = [str(target) for target in (*targets, model_path) if os.path.lexists(target)]
        if existing and not overwrite:
            raise FileExistsError(f"refusing to overwrite {', '.join(existing)}")
        for target, dataset in zip(targets, datasets, strict=True):
            target.write_text(dataset.to_ossie_yaml(), encoding="utf-8")
        model_path.write_text(document.to_ossie_yaml(), encoding="utf-8")

    def _split(
        self, file_name: Callable[[OssieDataset], str] | None
    ) -> tuple[OssieSplitSemanticModel, list[OssieDatasetDocument], list[str]]:
        names = [file_name(d) if file_name else f"{d.name}.dataset.yaml" for d in self.datasets]
        _check_file_names(names)
        references = [
            OssieDatasetReference(name=d.name, iri=quote(name, safe="")) for d, name in zip(self.datasets, names, strict=True)
        ]
        properties = {key: getattr(self, key) for key in type(self).model_fields if key != "datasets"}
        document = OssieSplitSemanticModel.model_validate({**properties, "dataset_refs": references})
        return document, [OssieDatasetDocument(version=self.version, dataset=d) for d in self.datasets], names


# ---------------------------------------------------------------------------
# Multi-file models (core-spec/ossie-split-model-schema.json, ossie-dataset-schema.json)
# ---------------------------------------------------------------------------


class OssieDatasetReference(BaseModel):
    """A split model document's reference to a dataset document in its folder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    # One path segment, percent-encoded where needed.
    iri: str = Field(..., min_length=1)


class OssieDatasetDocument(OssieDocument):
    """A dataset document: one dataset of a split semantic model, in its own file."""

    dataset: OssieDataset


class OssieSplitSemanticModel(OssieDocument):
    """A semantic model whose datasets are stored, wholly or partly, in the dataset documents `dataset_refs` lists."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    ai_context: OssieAIContext | None = None
    dataset_refs: list[OssieDatasetReference] = Field(..., min_length=1)
    datasets: list[OssieDataset] | None = Field(default=None, min_length=1)
    relationships: list[OssieRelationship] | None = None
    metrics: list[OssieMetric] | None = None
    nonportable_extensions: list[OssieNonportableExtension] | None = None

    def resolve(self, load: Callable[[str], str], model_label: str = "<model document>") -> OssieSemanticModel:
        """Resolve into one semantic model; `load` returns the text of the document an iri names.

        Referenced datasets follow the inline ones, in `dataset_refs` order. Raises
        `ValueError` listing every resolution error, naming this document by
        `model_label` and each dataset document by its iri.
        """
        errors: list[str] = []
        referenced: list[tuple[OssieDataset, str]] = []
        for index, reference in enumerate(self.dataset_refs):
            label = f"{model_label}: dataset_refs -> {index}"
            dataset, diagnostics = _referenced_dataset(reference, label, model_label, self.version, load)
            errors += diagnostics
            if dataset is not None:
                referenced.append((dataset, reference.iri))
        if not errors:
            errors = _duplicate_dataset_names(self.datasets or [], referenced, model_label)
        if errors:
            raise ValueError("\n".join(errors))
        properties = {key: getattr(self, key) for key in OssieSemanticModel.model_fields if key != "datasets"}
        datasets = [*(self.datasets or []), *(dataset for dataset, _ in referenced)]
        return OssieSemanticModel.model_validate({**properties, "datasets": datasets})


# A % that does not begin a hex triplet.
_MALFORMED_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_IRI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _dataset_file_name(iri: str) -> str | None:
    """Return the file name a `dataset_refs` iri names in the model document's folder, or None if it names none.

    The iri must be a relative reference of one path segment: no scheme, query or
    fragment, and once percent-decoded (strictly, as UTF-8) not empty, ``.`` or ``..``
    and free of NUL, ``/`` and ``\\``.
    """
    if any(c in iri for c in "/\\?#") or _IRI_SCHEME.match(iri) or _MALFORMED_PERCENT.search(iri):
        return None
    try:
        name = unquote_to_bytes(iri).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if name in ("", ".", "..") or any(c in name for c in "\0/\\"):
        return None
    return name


def _check_file_names(names: list[str]) -> None:
    """Raise `ValueError` unless each name is one path segment and no two differ only in case."""
    for name in names:
        if name in ("", ".", "..") or any(c in name for c in "\0/\\"):
            raise ValueError(f"file name {name!r} is not a file name in the model document's folder")
    folded = Counter(name.casefold() for name in names)
    clashes = [name for name in names if folded[name.casefold()] > 1]
    if clashes:
        raise ValueError(f"file names {', '.join(map(repr, clashes))} name one file on a case-insensitive file system")


class _UnreadableFile(Exception):
    """A dataset document's file cannot be read; the message completes "iri '<iri>' ..."."""


def _folder_loader(folder: Path) -> Callable[[str], str]:
    """Return a `load` reading the file an iri names in folder, matching directory entries exactly."""

    def load(iri: str) -> str:
        name = _dataset_file_name(iri)
        if name is None:
            raise _UnreadableFile("is not a file name in the model document's folder")
        try:
            if name not in os.listdir(folder):
                raise _UnreadableFile("does not resolve: no such file in the model document's folder")
            path = folder / name
            if path.resolve().parent != folder.resolve():
                raise _UnreadableFile("resolves outside the model document's folder")
            if not stat.S_ISREG(path.stat().st_mode):
                raise _UnreadableFile("does not name a regular file")
            return path.read_text(encoding="utf-8")
        except OSError as e:
            raise _UnreadableFile(f"does not resolve: {e.strerror or e}") from e
        except RuntimeError as e:
            # A symlink loop, before Python 3.13.
            raise _UnreadableFile(f"does not resolve: {e}") from e
        except UnicodeDecodeError as e:
            raise _UnreadableFile("is not valid UTF-8 text") from e

    return load


def _validation_reason(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in error.errors())
    return str(error)


def _referenced_dataset(
    reference: OssieDatasetReference, label: str, model_label: str, version: str, load: Callable[[str], str]
) -> tuple[OssieDataset | None, list[str]]:
    """Load the dataset one reference names, or return None and the errors."""
    iri = reference.iri
    where = f"{label} iri '{iri}'"
    if _dataset_file_name(iri) is None:
        return None, [f"{where} is not a file name in the model document's folder"]
    try:
        text = load(iri)
    except _UnreadableFile as e:
        return None, [f"{where} {e}"]
    except Exception as e:  # noqa: BLE001 - whatever the caller's loader raises is reported
        return None, [f"{where} could not be loaded: {e}"]
    try:
        data = yaml.load(text, Loader=UniqueKeyLoader)
    except yaml.YAMLError as e:
        return None, [f"{where} is not valid YAML: {str(e).splitlines()[0]}"]
    except RecursionError:
        return None, [f"{where} is too deeply nested to parse"]
    if not isinstance(data, dict):
        return None, [f"{where} does not hold a document"]
    if "dataset" not in data:
        return None, [f"{where} is not a dataset document"]

    errors = []
    document = None
    # A mismatch is reported once, naming both documents, not also as a schema error.
    mismatch = "version" in data and data["version"] != version
    try:
        document = OssieDatasetDocument._from_document({**data, "version": version} if mismatch else data)
    except ValueError as e:
        errors.append(f"{where} is not a valid dataset document: {_validation_reason(e)}")
    if mismatch:
        errors.append(f"{where} declares version '{data['version']}' but {model_label} declares version '{version}'")
    dataset = data["dataset"]
    if isinstance(dataset, dict) and "name" in dataset and dataset["name"] != reference.name:
        errors.append(f"{label} names '{reference.name}' but iri '{iri}' declares dataset '{dataset['name']}'")
    if errors or document is None:
        return None, errors
    return document.dataset, []


def _duplicate_dataset_names(
    inline: list[OssieDataset], referenced: list[tuple[OssieDataset, str]], model_label: str
) -> list[str]:
    """Report each referenced dataset name held more than once, with every document holding it."""
    counts: Counter[str] = Counter()
    holders: dict[str, list[str]] = {}
    for dataset in inline:
        counts[dataset.name] += 1
        holders.setdefault(dataset.name, [model_label])
    for dataset, iri in referenced:
        counts[dataset.name] += 1
        files = holders.setdefault(dataset.name, [])
        if f"iri '{iri}'" not in files:
            files.append(f"iri '{iri}'")
    # Names repeated among the inline datasets alone are left to the validator.
    repeated = [name for name in dict.fromkeys(dataset.name for dataset, _ in referenced) if counts[name] > 1]
    return [f"Duplicate dataset name '{name}' in {', '.join(holders[name])}" for name in repeated]


def _require_any(model: BaseModel, *names: str) -> None:
    """Mirror a schema `anyOf` of `required` lists: at least one of `names` is set."""
    if all(getattr(model, name) is None for name in names):
        listed = " or ".join(f"'{name}'" for name in names)
        raise ValueError(f"{type(model).__name__} requires at least one of {listed}")


# ---------------------------------------------------------------------------
# Ontology (ontology/ontology.json)
# ---------------------------------------------------------------------------


NonEmptyStr = Annotated[str, Field(min_length=1)]
# The schema marks these lists `uniqueItems`.
DistinctNames = Annotated[list[NonEmptyStr], AfterValidator(_distinct)]


class OssieConceptType(str, Enum):  # noqa: UP042
    """Whether a concept is an entity type or a value type."""

    ENTITY_TYPE = "EntityType"
    VALUE_TYPE = "ValueType"


class OssieMultiplicity(str, Enum):  # noqa: UP042
    """Multiplicity constraint on the last role of an ontology relationship."""

    MANY_TO_ONE = "ManyToOne"
    ONE_TO_ONE = "OneToOne"


class OssieRole(BaseModel):
    """An additional role in an ontology relationship.

    The first role is implicit: it is played by the concept the relationship is
    declared under.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: NonEmptyStr
    # Needed only when the same concept plays more than one role in the relationship.
    name: NonEmptyStr | None = None
    annotations: dict[str, Any] | None = None


class OssieOntologyRelationship(BaseModel):
    """Relationship between concepts, declared under the concept playing its first role.

    Not to be confused with `OssieRelationship`, the join between two datasets of a
    semantic model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyStr
    description: str | None = None
    roles: list[OssieRole] | None = None
    multiplicity: OssieMultiplicity | None = None
    derived_by: DistinctNames | None = None
    requires: DistinctNames | None = None
    verbalizes: DistinctNames
    annotations: dict[str, Any] | None = None
    iri: NonEmptyStr | None = None


class OssieConceptComponent(BaseModel):
    """A concept and the relationships where it plays the first role.

    The schema calls this node `OntologyComponent`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: NonEmptyStr
    type: OssieConceptType
    description: str | None = None
    extends: DistinctNames | None = None
    derived_by: DistinctNames | None = None
    identify_by: DistinctNames | None = None
    requires: DistinctNames | None = None
    relationships: list[OssieOntologyRelationship] | None = None
    annotations: dict[str, Any] | None = None
    iri: NonEmptyStr | None = None


# ---------------------------------------------------------------------------
# Ontology mappings (ontology/ontology.json)
# ---------------------------------------------------------------------------


class OssieReferentMapping(BaseModel):
    """Maps logical constructs to an identifying relationship of an entity type.

    A leaf carries an `expression`; a referent whose player is itself an entity
    nests further `referent_mappings` instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship: NonEmptyStr
    expression: NonEmptyStr | None = None
    referent_mappings: list[OssieReferentMapping] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_expression_or_referents(self) -> OssieReferentMapping:
        _require_any(self, "expression", "referent_mappings")
        return self


class OssieObjectMapping(BaseModel):
    """Identifies objects of a concept from the values of one or more fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: NonEmptyStr | None = None
    referent_mappings: list[OssieReferentMapping] | None = Field(default=None, min_length=1)
    expression: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _require_expression_or_referents(self) -> OssieObjectMapping:
        _require_any(self, "expression", "referent_mappings")
        return self


class OssieLinkMapping(BaseModel):
    """A node in a tree of mappings to the links of ontology relationships.

    A node's depth equals the arity of the relationship it names; each child extends
    the tuple mapped by its parent by one role.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship: NonEmptyStr | None = None
    object_mapping: OssieObjectMapping
    children: list[OssieLinkMapping] | None = None


class OssieConceptMapping(BaseModel):
    """Mappings that populate one concept and the relationships grouped under it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: NonEmptyStr
    object_mappings: list[OssieObjectMapping] | None = Field(default=None, min_length=1)
    link_mappings: list[OssieLinkMapping] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_object_or_link_mappings(self) -> OssieConceptMapping:
        _require_any(self, "object_mappings", "link_mappings")
        return self


class OssieOntologyMapping(BaseModel):
    """A semantic model embedded in an ontology, with the mappings onto that ontology.

    Deprecated by the schema, which accepts it only so existing documents continue
    to validate. New mappings are standalone documents validated against
    `ontology/mapping.json`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyStr | None = None
    description: str | None = None
    # The schema embeds the whole core document here, `version` included.
    semantic_model: OssieSemanticModel
    concept_mappings: list[OssieConceptMapping]


class OssieOntologyModel(OssieDocument):
    """An ontology: concepts, the relationships among them, and their rules."""

    name: NonEmptyStr
    description: str | None = None
    ai_context: OssieAIContext | None = None
    requires: DistinctNames | None = None
    ontology: list[OssieConceptComponent] = Field(..., min_length=1)
    # Deprecated by the schema; see `OssieOntologyMapping`.
    ontology_mappings: list[OssieOntologyMapping] | None = None
    annotations: dict[str, Any] | None = None
    # Namespace prefix -> the IRI it abbreviates, for `iri` values written as QNames.
    prefixes: dict[str, NonEmptyStr] | None = None

    @classmethod
    def _embedded_documents(cls, data: dict) -> Iterator[tuple[str, Any]]:
        """Each ontology mapping embeds a whole core document as its ``semantic_model``."""
        mappings = data.get("ontology_mappings")
        if not isinstance(mappings, list):
            return
        for index, mapping in enumerate(mappings):
            if isinstance(mapping, dict) and "semantic_model" in mapping:
                yield f"ontology_mappings[{index}].semantic_model", mapping["semantic_model"]
