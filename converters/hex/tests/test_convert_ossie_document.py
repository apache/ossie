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

import pytest
from ossie import (
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSISemanticModel,
)

from ossie_hex.hex_types import HexModel
from ossie_hex.ossie_to_hex.convert_ossie_document import convert_ossie_document
from ossie_hex.ossie_types import OSSIE_VERSION
from ossie_hex.util.errors import ConversionError

# region Ossie Semantic Model selection

_TWO_MODELS = OSIDocument(
    version=OSSIE_VERSION,
    dialects=[OSIDialect.SNOWFLAKE],
    semantic_model=[
        OSISemanticModel(
            name="first", datasets=[OSIDataset(name="orders", source="s.orders")]
        ),
        OSISemanticModel(
            name="second", datasets=[OSIDataset(name="events", source="s.events")]
        ),
    ],
)


def test_semantic_models_not_found() -> None:
    with pytest.raises(ConversionError, match="no semantic_model entries"):
        convert_ossie_document(
            OSIDocument(version=OSSIE_VERSION, semantic_model=[]), warnings=[]
        )


def test_semantic_model_default() -> None:
    project, warnings = convert_ossie_document(_TWO_MODELS, warnings=[])

    assert project.name == "first"
    assert [resource.id for resource in project.resources] == ["orders"]
    assert [str(warning) for warning in warnings] == [
        (
            "Ossie document has 2 semantic models; exporting 'first' "
            "(pass --model to select another)"
        )
    ]


def test_requested_semantic_model() -> None:
    # model name should be used to select the semantic model; no warnings
    project, warnings = convert_ossie_document(
        _TWO_MODELS, model_name="second", warnings=[]
    )

    assert project.name == "second"
    assert [resource.id for resource in project.resources] == ["events"]
    assert warnings == []


def test_semantic_model_not_found() -> None:
    # model name not found in the document should be rejected
    with pytest.raises(ConversionError, match="semantic model 'third' not found"):
        convert_ossie_document(_TWO_MODELS, model_name="third", warnings=[])


# endregion Ossie Semantic Model selection

# region Ossie Dialect selection


def _one_multi_dialect_field(*declared: OSIDialect) -> OSIDocument:
    """A document whose only field spells its column differently per dialect.

    Which spelling comes out the other side is what the selected dialect decides,
    so it stands in for the choice itself.
    """
    return OSIDocument(
        version=OSSIE_VERSION,
        dialects=list(declared) or None,
        semantic_model=[
            OSISemanticModel(
                name="m",
                datasets=[
                    OSIDataset(
                        name="orders",
                        source="s.orders",
                        fields=[
                            OSIField(
                                name="amount",
                                expression=OSIExpression(
                                    dialects=[
                                        OSIDialectExpression(
                                            dialect=dialect,
                                            expression=f"{dialect.value.lower()}_amount",
                                        )
                                        for dialect in (
                                            OSIDialect.ANSI_SQL,
                                            OSIDialect.SNOWFLAKE,
                                            OSIDialect.BIGQUERY,
                                        )
                                    ]
                                ),
                                dimension=OSIDimension(),
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _exported_expression(
    document: OSIDocument, dialect: OSIDialect | None = None
) -> str | None:
    project, _ = convert_ossie_document(document, dialect=dialect, warnings=[])
    model = project.resources[0]
    assert isinstance(model, HexModel)
    return model.dimensions[0].expr_sql


def test_document_dialect() -> None:
    # the dialect the document declares should be used when none is requested
    document = _one_multi_dialect_field(OSIDialect.SNOWFLAKE)

    assert _exported_expression(document) == "snowflake_amount"


def test_requested_dialect() -> None:
    # requested dialect should win over the document dialect
    document = _one_multi_dialect_field(OSIDialect.SNOWFLAKE)

    assert (
        _exported_expression(document, dialect=OSIDialect.BIGQUERY) == "bigquery_amount"
    )


def test_document_without_dialect() -> None:
    # document declaring no dialect should fall back to ANSI SQL
    document = _one_multi_dialect_field()

    assert _exported_expression(document) == "ansi_sql_amount"


# endregion Ossie Dialect selection
