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

from ossie_hex.hex import HexModel
from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.dump_hex_resource import dump_hex_resource
from tests.utils import problems_snapshot


@pytest.fixture
def foo() -> HexModel:
    return HexModel(id="foo", base_sql_table="public.foo")


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path.resolve()


def test_writes_yaml_file(ctx: ExportContext, foo: HexModel, project_dir: Path) -> None:
    dump_hex_resource(foo, project_dir, ctx=ctx)
    file_path = project_dir / "foo.yml"
    assert file_path.read_text(encoding="utf-8") == snapshot(
        """\
id: foo
base_sql_table: public.foo
"""
    )
    assert not ctx.problems


def test_errors_when_write_fails(
    ctx: ExportContext,
    foo: HexModel,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: Path, *_args: object, **_kwargs: object) -> int:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "write_text", boom)
    dump_hex_resource(foo, project_dir, ctx=ctx)
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Failed to write to file: permission denied"
    )


@pytest.mark.parametrize(
    "resource_id",
    [
        "../secret",
        "../../secret",
        "foo/../../secret",
        "/tmp/secret",
    ],
)
def test_rejects_path_traversal(
    ctx: ExportContext, project_dir: Path, resource_id: str
) -> None:
    foo = HexModel.model_construct(id=resource_id, base_sql_table="public.foo")
    dump_hex_resource(foo, project_dir, ctx=ctx)
    escaped = (project_dir / f"{resource_id}.yml").resolve()
    assert not escaped.exists()
    assert not list(project_dir.glob("*.yml"))
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Resource id would write a file outside the project directory"
    )
