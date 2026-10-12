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

"""Backend-only materialization; no HTTP query parser or semantic compiler."""

import copy
import io
import json
import sys
from contextlib import ExitStack, suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pyarrow as pa

EXECUTION_KEY = b"org.apache.ossie.execution"
COLUMN_KEY = b"org.apache.ossie.column"
MAX_COUNTER = 9007199254740991
MESSAGES = {
    "invalid_input": "Invalid materialization configuration.",
    "unsupported_type": "Backend schema does not match the supported result profile.",
    "resource_limit": "Materialization exceeded its configured resource limit.",
    "materialization_failed": "Backend result could not be materialized.",
}


class MaterializationError(Exception):
    """Local adapter category, deliberately not a Foundation QueryError code."""

    def __init__(self, category):
        self.category = category
        super().__init__(MESSAGES[category])

    def public_detail(self):
        return {"category": self.category, "message": str(self)}


def _require(condition, category="invalid_input"):
    if not condition:
        raise MaterializationError(category)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _counter(value, minimum=0):
    _require(type(value) is int and minimum <= value <= MAX_COUNTER)


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def _unexpired(value):
    _require(isinstance(value, str))
    # Require an explicit RFC 3339 offset; emit canonical UTC rather than a local time.
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MaterializationError("invalid_input") from None
    _require("T" in value and parsed.tzinfo is not None)
    _require(parsed > datetime.now(timezone.utc))
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _discard(paths):
    failed = False
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed = True
    if failed:
        raise MaterializationError("materialization_failed") from None


def _logical_type(t):
    if pa.types.is_string(t):
        return "String"
    if pa.types.is_integer(t):
        return "Integer"
    if t in (pa.float32(), pa.float64()):
        return "Float"
    if pa.types.is_boolean(t):
        return "Boolean"
    if pa.types.is_decimal128(t) or pa.types.is_decimal256(t):
        return "Decimal"
    if pa.types.is_date32(t):
        return "Date"
    if pa.types.is_time32(t) or pa.types.is_time64(t):
        return "Time"
    if pa.types.is_timestamp(t):
        _require(t.tz is None or _text(t.tz), "unsupported_type")
        return "DateTimeTz" if t.tz else "DateTime"
    raise MaterializationError("unsupported_type")


def _fields(schema, columns):
    required = {"column_id", "name", "logical_type", "nullable"}
    optional = {"role", "model_ref", "label", "description", "unit", "currency"}
    _require(isinstance(columns, list) and len(columns) == len(schema) > 0)
    ids, fields = set(), []
    for field, column in zip(schema, columns):
        _require(isinstance(column, dict) and required <= column.keys())
        _require(column.keys() <= required | optional)
        _require(all(_text(v) for k, v in column.items() if k != "nullable"))
        _require(type(column["nullable"]) is bool and column["column_id"] not in ids)
        _require("role" not in column or column["role"] in ("field", "dimension", "measure"))
        ids.add(column["column_id"])
        _require(not any(k.startswith(b"ARROW:extension:") for k in (field.metadata or {})),
                 "unsupported_type")
        _require(field.name == column["name"] and field.nullable == column["nullable"]
                 and _logical_type(field.type) == column["logical_type"], "unsupported_type")
        fields.append(field.with_metadata({COLUMN_KEY: _json({"column_id": column["column_id"]})}))
    return fields


class _BoundedSink(io.RawIOBase):
    def __init__(self, raw, remaining):
        super().__init__()
        self.raw, self.remaining = raw, remaining

    def writable(self):
        return True

    def tell(self):
        return self.raw.tell()

    def write(self, data):
        _require(len(data) <= self.remaining[0], "resource_limit")
        count = self.raw.write(data)
        self.remaining[0] -= count
        return count


def materialize(reader, *, output, execution_id, result_id, data_source_id, model,
                columns, expires_at, page_rows=1000, max_rows=100000, max_bytes=64 * 1024 * 1024):
    """Consume and close an owned Arrow reader, returning a complete capture.

    Inputs are supplied by an already-authorized planner/backend integration.
    `output` must be an existing private directory, not derived from a wire id.
    `page_rows=None` selects single-stream delivery. No partial success is returned.
    Only generated files owned by this call are deleted on failure.
    """
    created, complete = [], False
    try:
        with ExitStack() as stack:
            stack.callback(reader.close)
            _require(sys.byteorder == "little", "unsupported_type")
            for value in (execution_id, result_id, data_source_id):
                _require(_text(value) and value not in (".", ".."))
            _require(isinstance(model, dict) and model.keys() == {"id", "revision"})
            _require(all(_text(v) and v not in (".", "..") for v in model.values()))
            _counter(max_rows)
            _counter(max_bytes, 1)
            if page_rows is not None:
                _counter(page_rows, 1)
            expiry = _unexpired(expires_at)
            columns, model = copy.deepcopy(columns), copy.deepcopy(model)
            source_schema = reader.schema
            fields = _fields(source_schema, columns)
            output = Path(output)
            _require(output.is_dir())
            identity = dict(contract_version="0.1-draft", execution_id=execution_id,
                            result_id=result_id, data_source_id=data_source_id, model=model)
            rows, pages, resources, remaining = 0, [], [], [max_bytes]

            def batches():
                received = 0
                for batch in reader:
                    _require(batch.schema.equals(source_schema, check_metadata=True), "unsupported_type")
                    batch.validate(full=True)
                    _require(all(f.nullable or a.null_count == 0
                                 for f, a in zip(source_schema, batch.columns)), "unsupported_type")
                    received += batch.num_rows
                    _require(received <= max_rows, "resource_limit")
                    if batch.num_rows:
                        yield batch

            iterator = batches()
            pending = next(iterator, None)
            while pending is not None or not pages:
                index, offset, page_count = len(pages), rows, 0
                metadata = dict(identity)
                if page_rows is not None:
                    metadata.update(page_index=index, row_offset=offset)
                schema = pa.schema(fields, metadata={EXECUTION_KEY: _json(metadata)})
                name = f"page-{uuid4().hex}.arrow"
                path = output / name
                with path.open("xb") as raw:
                    created.append(path)
                    with _BoundedSink(raw, remaining) as sink:
                        options = pa.ipc.IpcWriteOptions(compression=None, metadata_version=pa.ipc.MetadataVersion.V5)
                        with pa.ipc.new_stream(sink, schema, options=options) as writer:
                            while pending is not None:
                                take = pending.num_rows if page_rows is None else min(
                                    pending.num_rows, page_rows - page_count)
                                part = pending.slice(0, take)
                                writer.write_batch(pa.RecordBatch.from_arrays(part.columns, schema=schema))
                                rows += take
                                page_count += take
                                pending = (pending.slice(take) if take < pending.num_rows
                                           else next(iterator, None))
                                if page_rows is not None and page_count == page_rows:
                                    break
                    byte_count = raw.tell()
                url = f"https://example.test/results/{name}"
                pages.append(dict(page_index=index, row_offset=offset, row_count=page_count,
                                  byte_count=byte_count, url=url))
                resources.append(dict(url=url, file=name))

            result = dict(result_id=result_id, format="arrow-stream", row_count=rows,
                          byte_count=max_bytes - remaining[0], completeness="complete",
                          expires_at=expiry, columns=columns)
            if page_rows is None:
                result["url"] = pages[0]["url"]
            else:
                result["pages"] = pages
            execution = {k: v for k, v in identity.items() if k != "result_id"}
            execution.update(state="succeeded", warnings=[], result=result)
            # The current TS client admits at most 1 MiB of control JSON.
            _require(len(_json(execution)) <= 1024 * 1024, "resource_limit")
            capture = dict(execution=execution, resources=resources)
        _unexpired(expiry)
        complete = True
        return capture
    except MaterializationError:
        raise
    except Exception:
        raise MaterializationError("materialization_failed") from None
    finally:
        if not complete:
            _discard(created)


def materialize_sqlite(database, trusted_sql, **options):
    """Execute trusted, already-authorized/compiled SQL on an existing read-only DB.

    No public HTTP request is accepted here. Connections/cursors are always closed.
    The reader-only materialize() entry point also works with an existing ADBC cursor.
    """
    from adbc_driver_sqlite import dbapi

    capture, complete = None, False
    try:
        _require(_text(trusted_sql))
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with dbapi.connect(uri=uri) as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute("PRAGMA query_only = ON")
                    cursor.execute(trusted_sql)
                    capture = materialize(cursor.fetch_record_batch(), **options)
                except Exception:
                    # SQLite finalize repeats a failed step's error. Clear it before
                    # DBAPI close (ADBC 1.12); prepare does not execute/retry the query.
                    with suppress(dbapi.Error):
                        cursor.adbc_statement.set_sql_query("")
                        cursor.adbc_statement.prepare()
                    raise
        _unexpired(capture["execution"]["result"]["expires_at"])
        complete = True
        return capture
    except MaterializationError:
        raise
    except Exception:
        raise MaterializationError("materialization_failed") from None
    finally:
        if capture is not None and not complete:
            _discard(Path(options["output"]) / r["file"] for r in capture["resources"])
