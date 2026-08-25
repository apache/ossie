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

from pathlib import Path
from textwrap import dedent

import pytest
from inline_snapshot import snapshot

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_document import load_ossie_document
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def _problems(ctx: ExportContext, path: Path) -> str:
    return problems_snapshot(ctx.problems).replace(str(path.resolve()), "PATH")


def test_loads_valid_document(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "foo.yml"
    data = dedent(
        """\
        semantic_model:
          - name: foo
            datasets:
              - name: bar
                source: public.bar
        """
    )
    path.write_text(data, encoding="utf-8")
    result = load_ossie_document(document_path=str(path), ctx=ctx)
    assert result is not None
    assert result.semantic_model[0].name == "foo"
    assert result.semantic_model[0].datasets[0].name == "bar"
    assert result.semantic_model[0].datasets[0].source == "public.bar"
    assert not ctx.problems


def test_missing_file(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "missing.yml"
    result = load_ossie_document(document_path=path, ctx=ctx)
    assert result is None
    assert _problems(ctx, path) == snapshot("[FATAL] File does not exist: `PATH`")


def test_rejects_directory(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "adir"
    path.mkdir()
    result = load_ossie_document(document_path=path, ctx=ctx)
    assert result is None
    assert _problems(ctx, path) == snapshot("[FATAL] File is not a file: `PATH`")


def test_empty_file(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "empty.yml"
    path.write_text("", encoding="utf-8")
    result = load_ossie_document(document_path=path, ctx=ctx)
    assert result is None
    assert _problems(ctx, path) == snapshot("[FATAL] File is empty: `PATH`")


def test_invalid_yaml(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("invalid: yaml: [", encoding="utf-8")
    result = load_ossie_document(document_path=path, ctx=ctx)
    assert result is None
    assert _problems(ctx, path) == snapshot(
        """\
[FATAL] Invalid YAML in file `PATH`: mapping values are not allowed here
  in "<unicode string>", line 1, column 14:
    invalid: yaml: [
                 ^\
"""
    )


def test_yaml_must_be_a_mapping(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "list.yml"
    path.write_text("- foo\n", encoding="utf-8")
    result = load_ossie_document(document_path=path, ctx=ctx)
    assert result is None
    assert _problems(ctx, path) == snapshot(
        """\
[FATAL] YAML document must be a mapping: `- foo
`\
"""
    )


def test_invalid_ossie_document(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "invalid.yml"
    path.write_text("foo: bar\n", encoding="utf-8")
    result = load_ossie_document(document_path=path, ctx=ctx)
    assert result is None
    assert _problems(ctx, path) == snapshot(
        """\
[FATAL] Invalid Ossie document: 1 validation error for OssieDocument
semantic_model
  Field required [type=missing, input_value={'foo': 'bar'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing\
"""
    )
