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

import pytest
from ossie import OSICustomExtension
from pydantic import ValidationError

from ossie_hex._common import ConversionError
from ossie_hex.custom_extension import (
    HEX_EXTENSION_VERSION,
    HEX_EXTENSION_VERSION_KEY,
    HEX_VENDOR,
    HexDimensionStash,
    HexMeasureStash,
    HexModelStash,
    HexProjectStash,
    HexRelationStash,
    HexResourceOrderStash,
    HexStash,
    HexViewStash,
    read_stash,
    write_stash,
)
from ossie_hex.hex_models import (
    HexDataType,
    HexDialect,
    HexGroup,
    HexMeasureFuncName,
    HexRelationType,
    HexScalarExpressionDefaultBoolean,
    HexScalarExpressionDefaultNumber,
    HexSemiAdditive,
    HexSemiAdditiveOverMember,
    HexView,
    HexVisibility,
)

# region: Read


def test_read_stash_ignores_other_vendors() -> None:
    other_vendor = OSICustomExtension(vendor_name="DBT", data='{"type": "number"}')
    hex_vendor = write_stash(HexDimensionStash(type=HexDataType.NUMBER))

    assert read_stash([other_vendor], HexDimensionStash) is None
    assert read_stash(
        [other_vendor, hex_vendor], HexDimensionStash
    ) == HexDimensionStash(type=HexDataType.NUMBER)


def test_read_stash_rejects_invalid_json() -> None:
    extensions = [OSICustomExtension(vendor_name=HEX_VENDOR, data="{not json}")]

    with pytest.raises(ConversionError) as excinfo:
        read_stash(extensions, HexDimensionStash)

    assert str(excinfo.value) == (
        "HEX extension is not valid JSON: Expecting property name enclosed in "
        "double quotes: line 1 column 2 (char 1)"
    )


def test_read_stash_rejects_incompatible_version() -> None:
    extensions = [
        OSICustomExtension(
            vendor_name=HEX_VENDOR,
            data=json.dumps(
                {
                    HEX_EXTENSION_VERSION_KEY: HEX_EXTENSION_VERSION + 1,
                    "type": "number",
                }
            ),
        )
    ]

    with pytest.raises(ConversionError) as excinfo:
        read_stash(extensions, HexDimensionStash)

    assert str(excinfo.value) == (
        "HEX extension declares payload version 2; this converter reads version 1"
    )


def test_read_stash_rejects_unexpected_fields() -> None:
    extensions = [
        OSICustomExtension(
            vendor_name=HEX_VENDOR,
            data='{"type": "number", "unexpected": true}',
        )
    ]

    with pytest.raises(ConversionError) as excinfo:
        read_stash(extensions, HexDimensionStash)

    message = str(excinfo.value)
    assert message.startswith(
        "Malformed HEX extension: 1 validation error for HexDimensionStash"
    )
    assert "unexpected" in message
    assert "Extra inputs are not permitted" in message


# endregion

# region: Write


def test_stash_extension_omits_none_fields() -> None:
    extension = write_stash(HexDimensionStash(type=HexDataType.NULL))

    assert extension.vendor_name == HEX_VENDOR
    assert json.loads(extension.data) == {"type": "null"}


def test_stash_extension_versions_project_payloads_only() -> None:
    project = write_stash(
        HexProjectStash(hex_dialect=HexDialect.DUCKDB.value, resource_order=[])
    )
    dimension = write_stash(HexDimensionStash(type=HexDataType.STRING))

    assert json.loads(project.data)[HEX_EXTENSION_VERSION_KEY] == HEX_EXTENSION_VERSION
    assert HEX_EXTENSION_VERSION_KEY not in json.loads(dimension.data)


def test_stash_models_are_frozen() -> None:
    stash = HexDimensionStash(type=HexDataType.NUMBER)

    with pytest.raises(ValidationError):
        stash.type = HexDataType.STRING


# endregion

# region: Round-trip


@pytest.mark.parametrize(
    "stash",
    [
        HexProjectStash(
            hex_dialect=HexDialect.DUCKDB.value,
            resource_order=[
                HexResourceOrderStash(id="orders", source_file="models/orders.yml")
            ],
            views=[
                HexViewStash(
                    resource=HexView(
                        id="orders_view",
                        base="orders",
                        contents=[
                            HexGroup(
                                dimensions=["order_id"], measures=["order_revenue"]
                            )
                        ],
                    )
                )
            ],
        ),
        HexModelStash(
            display_name="Orders",
            source_kind="query",
            visibility=HexVisibility.INTERNAL,
            undecomposable_relations=[
                HexRelationStash(
                    join_sql="${user_id} = ${users.id}",
                    relation_type=HexRelationType.MANY_TO_ONE,
                    target="users",
                    source_model_id="orders",
                    relation_id="users",
                    visibility=HexVisibility.PUBLIC,
                )
            ],
        ),
        HexRelationStash(
            join_sql="${user_id} = ${users.id}",
            relation_type=HexRelationType.MANY_TO_ONE,
            target="users",
            source_model_id="orders",
            relation_id="users",
            visibility=HexVisibility.PUBLIC,
        ),
        HexDimensionStash(
            type=HexDataType.NUMBER,
            visibility=HexVisibility.INTERNAL,
            expr_sql="amount_usd",
        ),
        HexDimensionStash(type=HexDataType.STRING, expr_calc="concat(first, last)"),
        HexMeasureStash(
            model_id="orders",
            measure_id="order_revenue",
            display_name="Order Revenue",
            type=HexDataType.NUMBER,
            visibility=HexVisibility.PRIVATE,
            semi_additive=HexSemiAdditive(
                over=[HexSemiAdditiveOverMember(dimension="order_date", pick="min")]
            ),
            func=HexMeasureFuncName.SUM,
            of=HexScalarExpressionDefaultNumber(expr_sql="amount_usd"),
            filters=[
                "status = 'paid'",
                HexScalarExpressionDefaultBoolean(expr_calc="is_active"),
            ],
        ),
    ],
    ids=lambda stash: type(stash).__name__,
)
def test_round_trip(stash: HexStash) -> None:
    assert read_stash([write_stash(stash)], type(stash)) == stash


# endregion
