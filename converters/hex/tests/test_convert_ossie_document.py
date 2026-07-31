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
from ossie import OSIDataset, OSIDialect, OSIDocument, OSISemanticModel

from ossie_hex.hex_types import HexDialect
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


def test_document_dialect() -> None:
    # document dialect should be used when no dialect is requested
    document = OSIDocument(
        version=OSSIE_VERSION,
        dialects=[OSIDialect.SNOWFLAKE],
        semantic_model=[OSISemanticModel(name="first", datasets=[])],
    )
    project, _ = convert_ossie_document(document, warnings=[])

    assert project.dialect == HexDialect.SNOWFLAKE.value


def test_requested_dialect() -> None:
    # requested dialect should win over the document dialect
    document = OSIDocument(
        version=OSSIE_VERSION,
        dialects=[OSIDialect.BIGQUERY],
        semantic_model=[OSISemanticModel(name="first", datasets=[])],
    )
    project, _ = convert_ossie_document(
        document, dialect=OSIDialect.BIGQUERY.value, warnings=[]
    )

    assert project.dialect == HexDialect.BIGQUERY.value


def test_invalid_dialect() -> None:
    # invalid dialect should be rejected
    with pytest.raises(ConversionError, match="Unknown OSI dialect 'klingon'"):
        convert_ossie_document(_TWO_MODELS, dialect="klingon", warnings=[])


def test_document_without_dialect() -> None:
    # document without dialect should fall back to ANSI SQL (Hex's DuckDB dialect)
    document = OSIDocument(
        version=OSSIE_VERSION,
        semantic_model=[OSISemanticModel(name="m", datasets=[])],
    )
    project, _ = convert_ossie_document(document, warnings=[])

    assert project.dialect == HexDialect.DUCKDB.value


# endregion Ossie Dialect selection
