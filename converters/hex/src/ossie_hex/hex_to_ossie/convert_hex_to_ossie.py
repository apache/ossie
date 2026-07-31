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

from ossie import OSIDialect

from ..ossie_types import parse_ossie_dialect
from ..util.errors import ConversionError, ConversionWarning
from ..util.yaml import dump_yaml
from .convert_hex_project import convert_hex_project
from .load_hex_project import load_hex_project


def convert_hex_to_ossie(
    project: str,
    *,
    dialect: OSIDialect | str | None = None,
    model_name: str | None = None,
) -> tuple[str, list[ConversionWarning]]:
    """Convert a Hex project directory to Ossie YAML.

    ``dialect`` is the OSI dialect the project's SQL is written in; the converted
    expressions are tagged with it.

    Returns ``(ossie_yaml, warnings)``.
    """
    if dialect is None:
        raise ConversionError(
            "--dialect is required when importing a Hex project directory"
        )
    hex_project = load_hex_project(project, name=model_name)
    ossie_dialect = parse_ossie_dialect(dialect)
    document, warnings = convert_hex_project(hex_project, ossie_dialect=ossie_dialect)
    data = document.model_dump(by_alias=True, exclude_none=True, mode="json")
    return dump_yaml(data), warnings
