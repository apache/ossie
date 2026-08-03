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

from pydantic import ValidationError

from ..hex_types import HexProject, HexResource, parse_hex_resource
from ..util.errors import ConversionError
from ..util.yaml import load_yaml_all


def load_hex_project(
    files: dict[str, str],
    *,
    project_name: str,
) -> HexProject:
    """Interpret files as a Hex project.

    ``files`` a mapping of file names to contents text.
    ``project_name`` a name for the project.

    Returns a `HexProject`.
    """
    resources: list[HexResource] = []
    seen_ids: set[str] = set()

    for file_name, text in files.items():
        docs = load_yaml_all(text, what=file_name)
        for idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                raise ConversionError(
                    f"{file_name} document {idx}: expected a mapping, "
                    f"got {type(doc).__name__}"
                )
            try:
                resource = parse_hex_resource(doc)
            except ValidationError as e:
                raise ConversionError(
                    f"Invalid Hex resource in {file_name}: {e}"
                ) from e
            if resource.id in seen_ids:
                raise ConversionError(
                    f"Duplicate Hex resource id '{resource.id}' in {file_name}"
                )
            seen_ids.add(resource.id)
            resources.append(resource)

    return HexProject(
        name=project_name,
        resources=resources,
    )
