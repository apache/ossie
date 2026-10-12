# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

import copy
import json
import sqlite3
import traceback
from contextlib import closing
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import jsonschema
import pyarrow as pa
import pytest
from adbc_driver_sqlite import dbapi

from demo import IDENTITY, SQL, seed_database
from materialize import COLUMN_KEY, EXECUTION_KEY, MaterializationError, materialize, materialize_sqlite

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "docs" / "proposals" /
                     "execution-http" / "execution.schema.json").read_text())


def validate(value, definition):
    schema = {**SCHEMA, "$ref": f"#/$defs/{definition}"}
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "database.sqlite"
    seed_database(path)
    return path


def read_capture(capture, output):
    execution = capture["execution"]
    validate(execution, "Execution")
    manifest = execution["result"]
    validate(manifest, "ResultManifest")
    pages = manifest.get("pages")
    tables = []
    total_rows = total_bytes = 0
    for i, resource in enumerate(capture["resources"]):
        path = output / resource["file"]
        assert path.name == resource["file"]
        with pa.ipc.open_stream(path) as reader:
            table = reader.read_all()
        tables.append(table)
        metadata = json.loads(table.schema.metadata[EXECUTION_KEY])
        validate(metadata, "ExecutionMetadata")
        expected = {key: execution[key] for key in
                    ("contract_version", "execution_id", "data_source_id", "model")}
        expected["result_id"] = manifest["result_id"]
        if pages is not None:
            expected.update(page_index=i, row_offset=total_rows)
            assert pages[i] == dict(page_index=i, row_offset=total_rows,
                                    row_count=table.num_rows, byte_count=path.stat().st_size,
                                    url=resource["url"])
        else:
            assert len(capture["resources"]) == 1 and resource["url"] == manifest["url"]
        assert metadata == expected
        for field, column in zip(table.schema, manifest["columns"]):
            assert field.name == column["name"] and field.nullable == column["nullable"]
            field_metadata = json.loads(field.metadata[COLUMN_KEY])
            validate(field_metadata, "ColumnMetadata")
            assert field_metadata == {"column_id": column["column_id"]}
        total_rows += table.num_rows
        total_bytes += path.stat().st_size
    assert total_rows == manifest["row_count"]
    assert total_bytes == manifest["byte_count"] > 0
    assert manifest["completeness"] == "complete"
    return tables


@pytest.mark.parametrize("page_rows", [None, 1, 2, 3, 20])
def test_real_adbc_sqlite_reader(database, tmp_path, page_rows):
    # Use the actual ADBC cursor reader, not a Python SQLite row-list conversion.
    with dbapi.connect(uri=database.resolve().as_uri() + "?mode=ro") as connection:
        with connection.cursor() as cursor:
            cursor.execute(SQL)
            capture = materialize(cursor.fetch_record_batch(), output=tmp_path,
                                  page_rows=page_rows, **IDENTITY)
    tables = read_capture(capture, tmp_path)
    assert [value for table in tables for value in table.column(0).to_pylist()] == [1, 2, 3]
    assert [value for table in tables for value in table.column(1).to_pylist()] == [9007199254740993, None, -7]
    assert all(table.schema.field(1).type == pa.int64() for table in tables)
    assert capture["execution"]["result"]["columns"] == IDENTITY["columns"]


@pytest.mark.parametrize("page_rows", [None, 2])
def test_zero_rows_keep_real_adbc_schema(database, tmp_path, page_rows):
    capture = materialize_sqlite(database, SQL.replace("ORDER BY", "WHERE 0 ORDER BY"),
                                 output=tmp_path, page_rows=page_rows, **IDENTITY)
    tables = read_capture(capture, tmp_path)
    assert len(tables) == 1 and tables[0].num_rows == 0
    assert tables[0].schema.field(1).type == pa.int64()


def test_read_only_backend_rejects_writes_and_sanitizes_errors(database, tmp_path):
    sentinel = tmp_path / "unrelated.arrow"
    sentinel.write_bytes(b"leave me alone")
    for sql in ("INSERT INTO sample VALUES (4, 10)",
                "SELECT secret_provider_token FROM private_customer_table"):
        with pytest.raises(MaterializationError) as caught:
            materialize_sqlite(database, sql, output=tmp_path, **IDENTITY)
        assert caught.value.category == "materialization_failed"
        assert "private_customer" not in json.dumps(caught.value.public_detail())
        assert "secret_provider_token" not in "".join(traceback.format_exception(caught.value))
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM sample").fetchone()[0] == 3
    assert sentinel.read_bytes() == b"leave me alone"
    assert set(p.name for p in tmp_path.iterdir()) == {"database.sqlite", "unrelated.arrow"}


def test_exact_budgets_and_no_success_shaped_truncation(database, tmp_path):
    reference = materialize_sqlite(database, SQL, output=tmp_path, page_rows=1, **IDENTITY)
    exact_bytes = reference["execution"]["result"]["byte_count"]
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    for limits in ({"max_rows": 2}, {"max_bytes": 1}, {"max_bytes": exact_bytes - 1}):
        with pytest.raises(MaterializationError, match="resource limit") as caught:
            materialize_sqlite(database, SQL, output=tmp_path, page_rows=1, **limits, **IDENTITY)
        assert caught.value.category == "resource_limit"
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == original
    capture = materialize_sqlite(database, SQL, output=tmp_path, page_rows=1,
                                 max_rows=3, max_bytes=exact_bytes, **IDENTITY)
    read_capture(capture, tmp_path)


class ObservedReader:
    def __init__(self, schema, batches, failure=False):
        self.schema, self.batches, self.failure = schema, batches, failure
        self.closed = self.exhausted = False

    def __iter__(self):
        yield from self.batches
        if self.failure:
            raise RuntimeError("provider password=secret db=private_customer")
        self.exhausted = True

    def close(self):
        self.closed = True


def reader_for(table, **kwargs):
    return ObservedReader(table.schema, table.to_batches(max_chunksize=1), **kwargs)


def test_exhaustion_cleanup_and_input_snapshots(tmp_path):
    table = pa.table({"sequence": pa.array([1, 2, 3]), "amount": pa.array([9, None, -7])})
    reader = reader_for(table)
    identity = copy.deepcopy(IDENTITY)
    capture = materialize(reader, output=tmp_path, page_rows=1, **identity)
    assert reader.closed and reader.exhausted
    identity["model"]["revision"] = "changed"
    identity["columns"][0]["label"] = "changed"
    assert capture["execution"]["model"]["revision"] == "1"
    assert "label" not in capture["execution"]["result"]["columns"][0]
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    reader = reader_for(table, failure=True)
    with pytest.raises(MaterializationError) as caught:
        materialize(reader, output=tmp_path, page_rows=1, **IDENTITY)
    assert reader.closed and not reader.exhausted
    assert "password" not in json.dumps(caught.value.public_detail())
    assert "password" not in "".join(traceback.format_exception(caught.value))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == original


@pytest.mark.parametrize("arrow_type,logical,value", [
    (pa.string(), "String", "text"),
    (pa.int8(), "Integer", -3),
    (pa.uint64(), "Integer", 18446744073709551615),
    (pa.float32(), "Float", 1.5),
    (pa.float64(), "Float", 2.5),
    (pa.bool_(), "Boolean", True),
    (pa.decimal128(20, 3), "Decimal", Decimal("123.456")),
    (pa.decimal256(50, 6), "Decimal", Decimal("123.456789")),
    (pa.date32(), "Date", date(2025, 1, 1)),
    (pa.time32("s"), "Time", time(12, 30)),
    (pa.time64("us"), "Time", time(12, 30, 1, 123456)),
    (pa.timestamp("ns"), "DateTime", datetime(2025, 1, 1)),
    (pa.timestamp("us", "UTC"), "DateTimeTz", datetime(2025, 1, 1, tzinfo=timezone.utc)),
])
def test_primitive_preservation_and_metadata_mapping(tmp_path, arrow_type, logical, value):
    field = pa.field("value", arrow_type, metadata={b"provider.secret": b"do not forward"})
    schema = pa.schema([field], metadata={b"provider.secret": b"do not forward"})
    table = pa.Table.from_arrays([pa.array([value, None], type=arrow_type)], schema=schema)
    column = dict(column_id="c", name="value", logical_type=logical, nullable=True, role="field",
                  model_ref="model.value", label="Value", description="Example", unit="units", currency="USD")
    capture = materialize(reader_for(table), output=tmp_path, page_rows=1,
                          **{**IDENTITY, "columns": [column]})
    tables = read_capture(capture, tmp_path)
    assert pa.concat_tables(tables).column(0).equals(table.column(0))
    assert all(t.schema.field(0).type == arrow_type for t in tables)
    assert all(set(t.schema.metadata) == {EXECUTION_KEY} for t in tables)
    assert all(set(t.schema.field(0).metadata) == {COLUMN_KEY} for t in tables)
    assert capture["execution"]["result"]["columns"] == [column]


@pytest.mark.parametrize("arrow_type", [
    pa.binary(), pa.large_string(), pa.list_(pa.int64()), pa.null(), pa.date64(),
    pa.float16(), pa.dictionary(pa.int8(), pa.string()),
])
def test_unsupported_types_close_reader_without_output(tmp_path, arrow_type):
    schema = pa.schema([pa.field("value", arrow_type)])
    reader = ObservedReader(schema, [])
    column = dict(column_id="c", name="value", logical_type="String", nullable=True)
    with pytest.raises(MaterializationError) as caught:
        materialize(reader, output=tmp_path, **{**IDENTITY, "columns": [column]})
    assert caught.value.category == "unsupported_type"
    assert reader.closed and not list(tmp_path.iterdir())


def test_extension_metadata_and_schema_drift_are_refused(tmp_path):
    table = pa.table({"sequence": [1, 2], "amount": [3, 4]})
    field = table.schema.field(0).with_metadata({b"ARROW:extension:name": b"opaque"})
    schema = table.schema.set(0, field)
    reader = ObservedReader(schema, [])
    with pytest.raises(MaterializationError):
        materialize(reader, output=tmp_path, **IDENTITY)
    assert reader.closed
    batches = table.to_batches(max_chunksize=1)
    reader = ObservedReader(table.schema, [batches[0], batches[1].replace_schema_metadata({b"x": b"y"})])
    with pytest.raises(MaterializationError):
        materialize(reader, output=tmp_path, page_rows=1, **IDENTITY)
    assert reader.closed and not list(tmp_path.iterdir())


@pytest.mark.parametrize("options", [
    {"expires_at": "2000-01-01T00:00:00Z"},
    {"max_rows": -1}, {"max_bytes": 0}, {"page_rows": 0},
    {"max_rows": 9007199254740992}, {"max_rows": True},
    {"model": {"id": "model", "revision": " "}},
    {"columns": [{**c, "column_id": "duplicate"} for c in IDENTITY["columns"]]},
])
def test_invalid_input_closes_reader(tmp_path, options):
    reader = reader_for(pa.table({"sequence": [1], "amount": [2]}))
    with pytest.raises(MaterializationError):
        materialize(reader, output=tmp_path, **{**IDENTITY, **options})
    assert reader.closed and not list(tmp_path.iterdir())


def test_ids_never_choose_files_and_existing_files_are_not_overwritten(database, tmp_path):
    existing = tmp_path / "capture.json"
    existing.write_text("unrelated")
    capture = materialize_sqlite(database, SQL, output=tmp_path, **{
        **IDENTITY, "execution_id": "../../query", "result_id": r"..\capture.json"})
    read_capture(capture, tmp_path)
    assert existing.read_text() == "unrelated"
    assert all(r["file"].startswith("page-") for r in capture["resources"])


def test_real_adbc_error_during_fetch_discards_partial_pages(tmp_path):
    database = tmp_path / "mixed.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE mixed (value)")
        connection.executemany("INSERT INTO mixed VALUES (?)", [(1,), (2,), ("private-value",)])
    with dbapi.connect(uri=database.resolve().as_uri() + "?mode=ro") as connection:
        with connection.cursor(adbc_stmt_kwargs={"adbc.sqlite.query.batch_rows": "1"}) as cursor:
            cursor.execute("SELECT value FROM mixed ORDER BY rowid")
            reader = cursor.fetch_record_batch()
            column = dict(column_id="c", name="value", logical_type="Integer", nullable=True)
            with pytest.raises(MaterializationError) as caught:
                materialize(reader, output=tmp_path, page_rows=1, **{**IDENTITY, "columns": [column]})
    assert caught.value.category == "materialization_failed"
    assert "private-value" not in json.dumps(caught.value.public_detail())
    assert list(tmp_path.iterdir()) == [database]


def test_exclusive_creation_never_removes_colliding_file(tmp_path, monkeypatch):
    import materialize as adapter

    class FixedName:
        hex = "a" * 32

    monkeypatch.setattr(adapter, "uuid4", FixedName)
    path = tmp_path / ("page-" + "a" * 32 + ".arrow")
    path.write_bytes(b"unrelated")
    reader = reader_for(pa.table({"sequence": [1], "amount": [2]}))
    with pytest.raises(MaterializationError):
        materialize(reader, output=tmp_path, **IDENTITY)
    assert reader.closed
    assert path.read_bytes() == b"unrelated"


def test_duplicate_names_use_column_identity_not_name(tmp_path):
    schema = pa.schema([pa.field("value", pa.int64()), pa.field("value", pa.int64())])
    table = pa.Table.from_arrays([pa.array([1]), pa.array([2])], schema=schema)
    columns = [dict(column_id=f"c{i}", name="value", nullable=True, logical_type="Integer")
               for i in range(2)]
    capture = materialize(reader_for(table), output=tmp_path, **{**IDENTITY, "columns": columns})
    tables = read_capture(capture, tmp_path)
    assert tables[0].column(0).to_pylist() == [1] and tables[0].column(1).to_pylist() == [2]


def test_expiry_rechecked_after_reader_closes(tmp_path, monkeypatch):
    import materialize as adapter

    now = [datetime(2098, 1, 1, tzinfo=timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    class ExpiringReader(ObservedReader):
        def close(self):
            super().close()
            now[0] = datetime(2100, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(adapter, "datetime", Clock)
    table = pa.table({"sequence": [1], "amount": [2]})
    reader = ExpiringReader(table.schema, table.to_batches())
    with pytest.raises(MaterializationError):
        materialize(reader, output=tmp_path, **IDENTITY)
    assert reader.closed and not list(tmp_path.iterdir())
