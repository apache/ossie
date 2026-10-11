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

import json
import math
from enum import Enum
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, StrictBool, model_serializer


_AI_CONTEXT_FIELDS = frozenset({"instructions", "synonyms", "examples"})

#: Paths listed in a diagnostic before the rest are counted instead.
_MAX_REPORTED = 20

_NULL = "set to null, which the schema does not allow; omit it instead"
_NON_FINITE = "not a number JSON can represent, so it cannot round-trip"


def _cyclic(path: str) -> ValueError:
    return ValueError(
        f"document contains a cyclic alias at {path or 'the root'}, "
        f"which cannot be represented as an Ossie document"
    )


def _invalid_below(
    node: Any,
    path: str,
    active: tuple[int, ...],
    walked: set[tuple[int, bool, bool]],
    free_form: bool,
    ai_context: bool,
) -> list[tuple[str, str]]:
    """Paths below `node` holding a value an Ossie document cannot carry.

    A null is one, because no property in `ossie-schema.json` is nullable. So is a
    non-finite number: `yaml.safe_load` reads `.nan` and `.inf`, and `json.loads`
    accepts the non-standard `NaN`, `Infinity` and `-Infinity`, none of which JSON can
    express -- a `NaN` reaching `to_ossie_json` comes back out as `null`.

    `free_form` marks the parts of `ai_context` where a null is legal: its object form
    sets `additionalProperties: true`, so an extra key may hold any value. A non-finite
    number is rejected there too, since it still cannot be serialized.

    `active` holds the containers on the current branch, so a self-reference raises
    while an anchor reused on a sibling branch still walks. `walked` holds containers
    already covered: one reached again through another alias is not reported twice,
    because an anchor is one property in the source, and because a chain of anchors
    shaped `[*previous, *previous]` expands to 2^n copies of the anchored subtree.
    """
    if isinstance(node, float) and not math.isfinite(node):
        return [(path, _NON_FINITE)]
    if not isinstance(node, (dict, list)):
        return []
    if id(node) in active:
        raise _cyclic(path)
    seen = (id(node), free_form, ai_context)
    if seen in walked:
        return []
    walked.add(seen)
    inner = (*active, id(node))
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}" if path else str(key)
            legal_null = free_form or (ai_context and key not in _AI_CONTEXT_FIELDS)
            if value is None:
                if not legal_null:
                    found.append((where, _NULL))
            else:
                found += _invalid_below(
                    value, where, inner, walked, legal_null, key == "ai_context"
                )
    else:
        for index, item in enumerate(node):
            found += _invalid_below(
                item, f"{path}[{index}]", inner, walked, free_form, False
            )
    return found


def _invalid_properties(node: Any) -> list[tuple[str, str]]:
    """Return every property holding a value an Ossie document cannot carry.

    Raises `ValueError` if the document holds a cyclic alias.
    """
    return _invalid_below(node, "", (), set(), False, False)


class OssieDialect(str, Enum):
    """Supported SQL and expression language dialects."""

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


class OssieDataType(str, Enum):
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


class OssieVendor(str, Enum):
    """Well-known vendor names for custom extensions."""

    COMMON = "COMMON"
    SNOWFLAKE = "SNOWFLAKE"
    SALESFORCE = "SALESFORCE"
    DBT = "DBT"
    DATABRICKS = "DATABRICKS"
    GOODDATA = "GOODDATA"
    SEMANTIDO = "SEMANTIDO"
    WISDOM = "WISDOM"
    SIGMA = "SIGMA"
    HEX = "HEX"


class OssieAIContextObject(BaseModel):
    """Structured AI context with instructions, synonyms, and examples."""

    model_config = ConfigDict(frozen=True, extra="allow")

    instructions: Optional[str] = None
    synonyms: Optional[tuple[str, ...]] = None
    examples: Optional[tuple[str, ...]] = None


OssieAIContext = Union[str, OssieAIContextObject]


class OssieCustomExtension(BaseModel):
    """Vendor-specific metadata as a serialized JSON string."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_name: str
    data: str


class OssieDialectExpression(BaseModel):
    """Expression in a specific dialect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dialect: OssieDialect
    expression: str


class OssieExpression(BaseModel):
    """Expression definition with multi-dialect support."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dialects: list[OssieDialectExpression] = Field(..., min_length=1)


class OssieDimension(BaseModel):
    """Dimension metadata on a field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The schema types this `boolean`, which admits only true and false. A plain
    # `bool` would let pydantic's lax mode accept "yes", "1", 1 or 1.0 and store a
    # real boolean, so input the schema forbids is silently rewritten into output it
    # allows, and the mistake is never reported.
    is_time: Optional[StrictBool] = None


class OssieField(BaseModel):
    """Row-level attribute for grouping, filtering, and metric expressions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    expression: OssieExpression
    dimension: Optional[OssieDimension] = None
    label: Optional[str] = None
    description: Optional[str] = None
    datatype: Optional[OssieDataType] = None
    ai_context: Optional[OssieAIContext] = None
    custom_extensions: Optional[list[OssieCustomExtension]] = None

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


class OssieDataset(BaseModel):
    """Logical dataset representing a business entity (fact or dimension table)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    primary_key: Optional[list[str]] = None
    unique_keys: Optional[list[list[str]]] = None
    description: Optional[str] = None
    ai_context: Optional[OssieAIContext] = None
    fields: Optional[list[OssieField]] = None
    custom_extensions: Optional[list[OssieCustomExtension]] = None


class OssieRelationship(BaseModel):
    """Foreign key relationship between datasets."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str = Field(..., min_length=1)
    from_dataset: str = Field(..., alias="from", min_length=1)
    to: str = Field(..., min_length=1)
    from_columns: list[str] = Field(..., min_length=1)
    to_columns: list[str] = Field(..., min_length=1)
    ai_context: Optional[OssieAIContext] = None
    custom_extensions: Optional[list[OssieCustomExtension]] = None


class OssieMetric(BaseModel):
    """Quantitative measure defined on business data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    expression: OssieExpression
    description: Optional[str] = None
    datatype: Optional[OssieDataType] = None
    ai_context: Optional[OssieAIContext] = None
    custom_extensions: Optional[list[OssieCustomExtension]] = None


class OssieSemanticModel(BaseModel):
    """Semantic model: the datasets, relationships, and metrics for one business domain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    ai_context: Optional[OssieAIContext] = None
    datasets: list[OssieDataset] = Field(..., min_length=1)
    relationships: Optional[list[OssieRelationship]] = None
    metrics: Optional[list[OssieMetric]] = None
    custom_extensions: Optional[list[OssieCustomExtension]] = None


class OssieDocument(OssieSemanticModel):
    """A single semantic model with document metadata at the root."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The schema pins this with `const`, so a document may not declare any other
    # version. Mirrored as a Literal rather than a plain str, which would let the
    # package serialize a version the schema rejects.
    version: Literal["0.2.0.dev0"] = "0.2.0.dev0"

    @model_serializer(mode="wrap")
    def _serialize_document(self, handler: SerializerFunctionWrapHandler):
        # Omit a custom return type to retain Pydantic's serialization schema.
        data = handler(self)
        if "version" in data:
            return {"version": data.pop("version"), **data}
        return data

    def to_ossie_yaml(self, **kwargs: Any) -> str:
        """Serialize to Ossie-compliant YAML (uses field aliases and excludes None values).

        Both defaults can be overridden through kwargs. ``mode`` cannot: YAML output
        needs the plain scalars only the JSON mode produces.
        """
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("exclude_none", True)
        data = self.model_dump(mode="json", **kwargs)
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def to_ossie_json(self, **kwargs: Any) -> str:
        """Serialize to Ossie-compliant JSON (uses field aliases and excludes None values).

        Both defaults can be overridden through kwargs.
        """
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("exclude_none", True)
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_ossie_yaml(cls, text: str) -> "OssieDocument":
        """Parse an Ossie YAML document, checking what pydantic alone cannot.

        The counterpart of :meth:`to_ossie_yaml`. ``model_validate`` stays lenient,
        because a caller building a model in Python routinely passes a value that may
        be ``None`` -- ``label=dim.label`` -- and means "omit it". A document is
        different: it is the artefact the schema governs, so these rules apply to it
        and not to the models.

        Rejected here: a property set to null, a missing ``version``, a non-finite
        number, and a cyclic alias. The field types reject the rest of the schema.

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
        return cls._from_document(yaml.safe_load(text))

    @classmethod
    def from_ossie_json(cls, text: str) -> "OssieDocument":
        """Parse an Ossie JSON document, checking what pydantic alone cannot.

        The counterpart of :meth:`to_ossie_json`; see :meth:`from_ossie_yaml` for what
        is and is not checked.
        """
        return cls._from_document(json.loads(text))

    @classmethod
    def _from_document(cls, data: Any) -> "OssieDocument":
        for reason in (_NULL, _NON_FINITE):
            paths = [path for path, why in _invalid_properties(data) if why == reason]
            if paths:
                # A document may hold many; list enough to act on and count the rest.
                listed = ", ".join(paths[:_MAX_REPORTED])
                if len(paths) > _MAX_REPORTED:
                    listed += f", and {len(paths) - _MAX_REPORTED} more"
                raise ValueError(f"property {reason}: {listed}")
        if isinstance(data, dict) and "version" in cls.model_fields and "version" not in data:
            # The field carries a default so Python construction needs no version, but
            # the schema lists it as required, so a document without one is invalid.
            raise ValueError("document is missing the required property: version")
        return cls.model_validate(data)
