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

"""Typed Hex payloads stored in Ossie custom extensions."""

from __future__ import annotations

import json
from typing import Literal, TypeVar

from ossie import OSICustomExtension, OSIVendor
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ossie_hex.datatype_mapping import is_lossless_hex_type

from ._common import ConversionError
from .hex_models import (
    HexDataType,
    HexMeasureFuncName,
    HexRelationType,
    HexScalarExpressionDefaultBoolean,
    HexScalarExpressionDefaultNumber,
    HexSemiAdditive,
    HexView,
    HexVisibility,
    is_default_hex_visibility,
)

HEX_VENDOR = OSIVendor.HEX.value
HEX_EXTENSION_VERSION = 1
HEX_EXTENSION_VERSION_KEY = "extension_version"


class _BaseHexStash(BaseModel):
    """Base class for Hex custom-extension payloads.

    A payload carries only what Ossie cannot."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _VisibilityMixin(BaseModel):
    visibility: HexVisibility | None = None

    @field_validator("visibility")
    @classmethod
    def _prune_visibility(cls, value: HexVisibility | None) -> HexVisibility | None:
        if value is None or is_default_hex_visibility(value):
            return None
        return value


class _TypeMixin(BaseModel):
    type: HexDataType | None = None

    @field_validator("type")
    @classmethod
    def _prune_type(cls, value: HexDataType | None) -> HexDataType | None:
        if value is None or is_lossless_hex_type(value):
            return None
        return value


class HexResourceOrderStash(_BaseHexStash):
    """Preserves resource order and file placement, which Ossie does not model."""

    id: str
    source_file: str


class HexViewStash(_BaseHexStash):
    """Preserves a Hex view because Ossie has no equivalent resource."""

    resource: HexView


class HexProjectStash(_BaseHexStash):
    """Preserves project-level Hex information absent from the Ossie model.

    The Hex dialect may be lost during dialect mapping, while resource ordering,
    file placement, and views have no Ossie representation.
    """

    hex_dialect: str
    resource_order: list[HexResourceOrderStash]
    views: list[HexViewStash] | None = None


class HexRelationStash(_VisibilityMixin, _BaseHexStash):
    """Preserves Hex relationship semantics that Ossie cannot fully represent.

    Ossie stores decomposed column pairs but not the original join expression,
    Hex cardinality, declaration direction, named-target behavior, or visibility.
    """

    join_sql: str
    relation_type: HexRelationType
    target: str
    source_model_id: str
    relation_id: str


class HexModelStash(_VisibilityMixin, _BaseHexStash):
    """Preserves Hex model metadata that has no direct Ossie representation.

    This includes table-versus-query source kind, display name, visibility, and
    relations whose join expressions could not be decomposed.
    """

    display_name: str
    source_kind: Literal["table", "query"]
    undecomposable_relations: list[HexRelationStash] | None = None


class HexDimensionStash(_TypeMixin, _VisibilityMixin, _BaseHexStash):
    """Preserves the original Hex dimension representation for round-tripping.

    Ossie cannot represent Hex visibility or formula expressions, and its
    normalized datatype and SQL expression may not reproduce the authored Hex
    definition exactly.
    """

    expr_calc: str | None = None
    expr_sql: str | None = None


class HexMeasureStash(_TypeMixin, _VisibilityMixin, _BaseHexStash):
    """Preserves Hex measure semantics lost when compiling to an Ossie metric.

    Ossie metrics are top-level SQL expressions, so they do not retain the
    owning model, original aggregate structure, formula expression,
    semi-additive behavior, Hex datatype, name, or visibility.
    """

    model_id: str
    measure_id: str
    display_name: str
    semi_additive: HexSemiAdditive | None = None
    func_calc: str | None = None
    func: HexMeasureFuncName | None = None
    of: str | HexScalarExpressionDefaultNumber | None = None
    filters: list[str | HexScalarExpressionDefaultBoolean] | None = None


HexStash = (
    HexProjectStash
    | HexModelStash
    | HexDimensionStash
    | HexMeasureStash
    | HexRelationStash
)
HexStashT = TypeVar("HexStashT", bound=_BaseHexStash)


def write_stash(data: HexStash) -> OSICustomExtension:
    """Serialize a typed Hex payload as an Ossie custom extension."""

    # Exclude unset fields to keep preserved Hex resources faithful to what was
    # authored. Otherwise, nested models materialize their derived defaults (a
    # view's ``name``, say) and those reappear as noise on the way back.
    payload = data.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    if isinstance(data, HexProjectStash):
        # version key is only needed once for the whole document
        payload = {HEX_EXTENSION_VERSION_KEY: HEX_EXTENSION_VERSION, **payload}
    return OSICustomExtension(vendor_name=HEX_VENDOR, data=json.dumps(payload))


def read_stash(
    extensions: list[OSICustomExtension] | None,
    stash_type: type[HexStashT],
) -> HexStashT | None:
    """Parse a Hex custom extension into its expected payload type."""
    for extension in extensions or []:
        if extension.vendor_name != HEX_VENDOR:
            continue
        try:
            data = json.loads(extension.data or "{}")
        except json.JSONDecodeError as e:
            raise ConversionError(
                f"{HEX_VENDOR} extension is not valid JSON: {e}"
            ) from e
        if not isinstance(data, dict):
            raise ConversionError(f"{HEX_VENDOR} extension must be a JSON object")
        version = data.pop(HEX_EXTENSION_VERSION_KEY, HEX_EXTENSION_VERSION)
        # The payload models forbid unknown keys, so a newer payload would fail
        # with an opaque validation error instead of naming the real problem.
        if version != HEX_EXTENSION_VERSION:
            raise ConversionError(
                f"{HEX_VENDOR} extension declares payload version {version}; "
                f"this converter reads version {HEX_EXTENSION_VERSION}"
            )
        try:
            return stash_type.model_validate(data)
        except ValidationError as e:
            raise ConversionError(f"Malformed {HEX_VENDOR} extension: {e}") from e
    return None


HexExtensionData = HexStash


def maybe_write_extension(data: HexExtensionData) -> list[OSICustomExtension] | None:
    """Seralize a payload as an Ossies ``custom_extensions`` list.

    Empty payloads are omitted.
    """
    if not data.model_dump(mode="json", exclude_none=True, exclude_unset=True):
        return None
    return [write_stash(data)]
