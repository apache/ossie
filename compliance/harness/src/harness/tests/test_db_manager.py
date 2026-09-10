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

"""Tests for DuckDB dataset loading, in particular sqlglot-based
statement splitting that a naive ``split(";")`` would get wrong."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.db_manager import DBManager

# A schema that breaks naive splitting: a semicolon inside a string
# literal, a ``--`` comment trailing a statement, a ``/* */`` block
# comment, and a lone comment line between statements.
TRICKY_SCHEMA = """\
-- header comment
CREATE TABLE t (id INTEGER, note VARCHAR); -- trailing comment
/* block comment; with a semicolon */
INSERT INTO t VALUES (1, 'a; not a separator'), (2, 'plain');
-- lone comment between statements
INSERT INTO t VALUES (3, 'c');
"""


@pytest.fixture
def db():
    manager = DBManager()
    manager.connect()
    yield manager
    manager.close()


def _write_dataset(tmp_path: Path, name: str, schema: str) -> Path:
    dataset_dir = tmp_path / name
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "schema.sql").write_text(schema)
    return tmp_path


def test_load_dataset_handles_semicolons_and_comments(
    tmp_path: Path, db: DBManager
) -> None:
    datasets_dir = _write_dataset(tmp_path, "d_tricky", TRICKY_SCHEMA)

    db.load_dataset("d_tricky", datasets_dir)

    rows = db.execute_sql("SELECT id, note FROM t ORDER BY id")
    assert rows == [
        {"id": 1, "note": "a; not a separator"},
        {"id": 2, "note": "plain"},
        {"id": 3, "note": "c"},
    ]


def test_load_dataset_is_idempotent(tmp_path: Path, db: DBManager) -> None:
    datasets_dir = _write_dataset(
        tmp_path,
        "d_once",
        "CREATE TABLE t (id INTEGER); INSERT INTO t VALUES (1);",
    )

    db.load_dataset("d_once", datasets_dir)
    # A second load must not re-run the schema (which would error on the
    # duplicate CREATE) — it's cached per connection.
    db.load_dataset("d_once", datasets_dir)

    assert db.execute_sql("SELECT COUNT(*) AS n FROM t") == [{"n": 1}]


def test_load_dataset_missing_schema_raises(tmp_path: Path, db: DBManager) -> None:
    with pytest.raises(FileNotFoundError):
        db.load_dataset("does_not_exist", tmp_path)
