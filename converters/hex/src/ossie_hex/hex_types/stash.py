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
from typing import Literal, TypeVar

from ossie import OSICustomExtension, OSIVendor
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ..util.errors import ConversionError
from .datatype_mapping import is_lossless_hex_type
from .hex_common import HexVisibility, is_default_hex_visibility
from .hex_datatype import HexDataType
from .hex_expression import (
    HexScalarExpressionDefaultBoolean,
    HexScalarExpressionDefaultNumber,
)
from .hex_measure import HexMeasure, HexMeasureFuncName, HexSemiAdditive
from .hex_relation import HexRelation, HexRelationType
from .hex_view import HexView

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


class HexViewStash(_BaseHexStash):
    """Preserves Hex view semantics that Ossie does not model."""

    resource: HexView


class HexProjectStash(_BaseHexStash):
    """Preserves Hex project semantics that Ossie does not model."""

    hex_dialect: str
    views: list[HexViewStash] | None = None


class HexRelationStash(_VisibilityMixin, _BaseHexStash):
    """Preserves Hex relation semantics that Ossie does not model."""

    relation_type: HexRelationType | None = None

    @field_validator("relation_type")
    @classmethod
    def _prune_relation_type(
        cls, value: HexRelationType | None
    ) -> HexRelationType | None:
        if value is None or value == HexRelationType.MANY_TO_ONE:
            return None
        return value


class HexModelStash(_VisibilityMixin, _BaseHexStash):
    """Preserves Hex model semantics that Ossie does not model."""

    display_name: str
    source_kind: Literal["table", "query"]
    measures: list[HexMeasure] | None = None
    undecomposable_relations: list[HexRelation] | None = None


class HexDimensionStash(_TypeMixin, _VisibilityMixin, _BaseHexStash):
    """Preserves Hex dimension semantics that Ossie does not model."""

    expr_calc: str | None = None
    expr_sql: str | None = None


class HexMeasureStash(_TypeMixin, _VisibilityMixin, _BaseHexStash):
    """Preserves Hex measure semantics that Ossie does not model."""

    model_id: str
    measure_id: str | None = None
    display_name: str
    semi_additive: HexSemiAdditive | None = None
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
