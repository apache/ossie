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

from ..util.errors import ConversionError


def read_ossie_document(document_path: str | Path) -> str:
    """Read an Ossie file into text.

    ``document_path`` the path to the file.

    Returns the contents of the file.
    """
    path = Path(document_path)
    if not path.is_file():
        raise ConversionError(f"Ossie document path is not a file: {path}")

    return path.read_text(encoding="utf-8")


def write_ossie_document(document_path: str | Path, ossie_text: str) -> None:
    """Write Ossie text to a file, creating the directories it sits in.

    ``document_path`` the path to write to, ``ossie_text`` the contents.
    """
    path = Path(document_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ossie_text, encoding="utf-8")
