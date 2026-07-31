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

from ossie import OSIDocument
from pydantic import ValidationError

from ..util.errors import ConversionError, ConversionWarning
from ..util.yaml import load_yaml


def load_ossie_document(
    ossie_yaml: str,
) -> tuple[OSIDocument, list[ConversionWarning]]:
    """Parse Ossie YAML into an Ossie document.

    Returns ``(ossie_document, warnings)``.
    """
    warnings: list[ConversionWarning] = []
    raw = load_yaml(ossie_yaml, what="Ossie model")
    if not isinstance(raw, dict):
        raise ConversionError("Ossie document must be a mapping")

    try:
        document = OSIDocument.model_validate(raw)
    except ValidationError as e:
        raise ConversionError(f"Invalid Ossie document: {e}") from e

    return document, warnings
