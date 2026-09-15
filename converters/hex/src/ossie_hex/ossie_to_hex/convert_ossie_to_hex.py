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

from pathlib import Path

from ossie import OssieDialect

from ..hex import HexProject
from ..util.problem import Problem
from .context import ExportContext
from .convert_ossie_dialect import convert_ossie_dialect
from .convert_ossie_document import convert_ossie_document
from .dump_hex_project import dump_hex_project
from .load_ossie_dialect import load_ossie_dialect
from .load_ossie_document import load_ossie_document


def convert_ossie_to_hex(
    input: Path | str,
    output: Path | str | None = None,
    *,
    dialect: OssieDialect | str | None = None,
) -> tuple[list[HexProject], list[Problem]]:
    """Convert an Ossie document to a Hex semantic project.

    Args:
        - `input`: A path to the Ossie document file.
        - `output`: Optional. A path to the desired output directory. If not provided,
          the output is not written to a file.
        - `dialect`: Optional. An Ossie dialect to prefer to pick expressions from (only
          a single dialect is preserved in Hex expressions). If not provided, the `ANSI_SQL`
          dialect will be used when available. Otherwise, the first dialect expression is used.

    Returns: a tuple of:
        - `hex_projects`: A list of Hex semantic project(s).
        - `problems`: A list of problems encountered.
    """
    ctx = ExportContext()

    with ctx.phase_scope("load"):
        ossie_document = load_ossie_document(document_path=input, ctx=ctx)
        ossie_dialect = load_ossie_dialect(dialect, ctx=ctx)

    with ctx.phase_scope("convert"):
        hex_dialect = convert_ossie_dialect(ossie_dialect, ctx=ctx)
        ctx.set_dialects(ossie_dialect, hex_dialect)
        hex_projects = convert_ossie_document(
            ossie_document,
            ctx=ctx,
        )

    with ctx.phase_scope("dump"):
        if output is not None:
            output = Path(output).resolve()
            output.mkdir(parents=True, exist_ok=True)
            for hex_project in hex_projects:
                dump_hex_project(hex_project, dir=output, ctx=ctx)

    return hex_projects, ctx.problems
