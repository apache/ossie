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

"""Converter between Microsoft Power BI / Fabric semantic models (TMSL ``model.bim``)
and Apache Ossie semantic models. Pure offline transforms; no Power BI connection needed.

    from ossie_microsoft import (
        convert_semantic_model_to_ossie,
        convert_ossie_to_semantic_model,
    )
"""

from ._common import ConversionError
from .engine import (
    EngineFinding,
    EngineUnavailableError,
    EngineValidationError,
    EngineValidationResult,
    validate_with_engine,
)
from .ossie_to_semantic_model import convert_ossie_to_semantic_model
from .semantic_model_to_ossie import build_ossie_document, convert_semantic_model_to_ossie
from .tom import (
    TomUnavailableError,
    TomValidationError,
    TomValidationIssue,
    TomValidationResult,
    validate_bim,
    validate_tmsl,
)

__all__ = [
    "ConversionError",
    "EngineFinding",
    "EngineUnavailableError",
    "EngineValidationError",
    "EngineValidationResult",
    "TomUnavailableError",
    "TomValidationError",
    "TomValidationIssue",
    "TomValidationResult",
    "build_ossie_document",
    "convert_ossie_to_semantic_model",
    "convert_semantic_model_to_ossie",
    "validate_bim",
    "validate_tmsl",
    "validate_with_engine",
]
