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
        name: foo
        datasets:
          - name: bar
            source: public.bar
        """
    )
    path.write_text(data, encoding="utf-8")
    result = load_ossie_document(document_path=str(path), ctx=ctx)
    assert result is not None
    assert result.name == "foo"
    assert result.datasets[0].name == "bar"
    assert result.datasets[0].source == "public.bar"
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
[FATAL] Invalid Ossie document: 3 validation errors for OssieDocument
name
  Field required [type=missing, input_value={'foo': 'bar'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
datasets
  Field required [type=missing, input_value={'foo': 'bar'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
foo
  Extra inputs are not permitted [type=extra_forbidden, input_value='bar', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/extra_forbidden\
"""
    )


def test_omits_invalid_members(ctx: ExportContext, tmp_path: Path) -> None:
    path = tmp_path / "sales.yaml"
    data = dedent("""\
        version: 0.2.0.dev0
        name: sales
        datasets:
          - name: orders
            source: public.orders
            fields:
              - name: amount
                datatype: Integer
                expression:
                  dialects:
                    - dialect: ANSI_SQL
                      expression: amount
              - name: broken
                datatype: Integer
                expression:
                  dialects:
                    - dialect: ANSI_SQL
                      expression: SELECT FROM
        metrics:
          - name: total
            datatype: Integer
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: SUM(orders.amount)
          - name: broken_total
            datatype: Integer
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: SUM(orders.missing)
        relationships:
          - name: broken_relation
            from: orders
            to: missing
            from_columns: [amount]
            to_columns: [id]
        """)
    path.write_text(data, encoding="utf-8")

    document = load_ossie_document(document_path=path, ctx=ctx)

    assert document is not None
    assert document.to_ossie_yaml() == snapshot("""\
version: 0.2.0.dev0
name: sales
datasets:
- name: orders
  source: public.orders
  unique_keys: []
  fields:
  - name: amount
    expression:
      dialects:
      - dialect: ANSI_SQL
        expression: amount
    datatype: Integer
relationships: []
metrics:
- name: total
  expression:
    dialects:
    - dialect: ANSI_SQL
      expression: SUM(orders.amount)
  datatype: Integer
""")
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m
Cause: ['datasets', 'orders', 'fields', 'broken', 'expression', 'dialects', 'ANSI_SQL', 'expression']

[ERROR] Expression must have at least one valid dialect
Cause: ['datasets', 'orders', 'fields', 'broken', 'expression', 'dialects']

[ERROR] Could not resolve dataset name: 'missing'.
Cause: ['relationships', 'broken_relation', 'to']

[ERROR] Field expression references field not in semantic model: orders.missing
Cause: ['metrics', 'broken_total', 'expression', 'dialects', 'ANSI_SQL']

[ERROR] Expression must have at least one valid dialect
Cause: ['metrics', 'broken_total', 'expression', 'dialects']\
""")
