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

import pytest
from inline_snapshot import snapshot

from ossie_hex.hex import HexDialect, HexModel, HexProject
from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.dump_hex_project import dump_hex_project
from tests.utils import problems_snapshot


@pytest.fixture
def foo() -> HexProject:
    return HexProject(
        name="foo",
        dialect=HexDialect("duckdb"),
        resources=[
            HexModel(id="bar", base_sql_table="public.bar"),
            HexModel(id="baz", base_sql_table="public.baz"),
        ],
    )


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def test_writes_resource_files(
    ctx: ExportContext, foo: HexProject, tmp_path: Path
) -> None:
    output_dir = tmp_path / "out"
    dump_hex_project(foo, dir=output_dir, ctx=ctx)
    project_dir = output_dir / foo.name
    assert (project_dir / "bar.yml").read_text(encoding="utf-8") == snapshot(
        """\
id: bar
base_sql_table: public.bar
"""
    )
    assert (project_dir / "baz.yml").read_text(encoding="utf-8") == snapshot(
        """\
id: baz
base_sql_table: public.baz
"""
    )
    assert not ctx.problems


def test_creates_project_dir_when_missing(ctx: ExportContext, tmp_path: Path) -> None:
    project = HexProject(name="foo", dialect=HexDialect("duckdb"), resources=[])
    output_dir = tmp_path / "nested" / "out"
    dump_hex_project(project, dir=output_dir, ctx=ctx)
    assert output_dir.is_dir()
    project_dir = output_dir / project.name
    assert project_dir.is_dir()
    assert list(project_dir.iterdir()) == []
    assert not ctx.problems


def test_resolves_project_dir(
    ctx: ExportContext, foo: HexProject, tmp_path: Path
) -> None:
    output_dir = tmp_path / "nested" / ".." / "out"
    dump_hex_project(foo, dir=output_dir, ctx=ctx)
    project_dir = (tmp_path / "out" / foo.name).resolve()
    assert (project_dir / "bar.yml").is_file()
    assert not (tmp_path / "nested").exists()
    assert not ctx.problems


@pytest.mark.parametrize(
    "project_name",
    [
        "../secret",
        "../../secret",
        "foo/../../secret",
        "/tmp/secret",
    ],
)
def test_rejects_path_traversal(
    ctx: ExportContext, tmp_path: Path, project_name: str
) -> None:
    project = HexProject.model_construct(
        name=project_name,
        dialect=HexDialect("duckdb"),
        resources=[HexModel.model_construct(id="bar", base_sql_table="public.bar")],
    )
    output_dir = tmp_path / "out"
    dump_hex_project(project, dir=output_dir, ctx=ctx)
    escaped = (output_dir / project_name).resolve()
    assert not escaped.exists()
    assert not list(output_dir.glob("**/*"))
    assert problems_snapshot(ctx.problems) == (
        f"[ERROR] Project name would write files outside the output directory: {project_name}"
    )
