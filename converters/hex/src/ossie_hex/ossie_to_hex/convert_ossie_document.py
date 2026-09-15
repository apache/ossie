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

from ossie import OssieDocument

from ..hex import HexProject
from .context import ExportContext
from .convert_ossie_semantic_model import convert_ossie_semantic_model


def convert_ossie_document(
    ossie_document: OssieDocument | None,
    *,
    ctx: ExportContext,
) -> list[HexProject]:
    """Convert an Ossie document to Hex semantic projects.

    A Hex semantic project is created for each Ossie semantic model
    defined in the document.

    Returns a list of Hex semantic projects.
    """
    hex_projects: list[HexProject] = []
    if ossie_document is not None:
        for ossie_semantic_model in ossie_document.semantic_model:
            with ctx.problem_scope("semantic_model"):
                hex_project = convert_ossie_semantic_model(
                    ossie_semantic_model, ctx=ctx
                )
                hex_projects.append(hex_project)

    return hex_projects
