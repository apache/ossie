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

"""Fixed trusted backend SQL fixture, not an Ossie/Foundation compiler."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from materialize import MaterializationError, materialize_sqlite

SQL = "SELECT sequence, amount FROM sample ORDER BY sequence"
COLUMNS = [
    dict(column_id="sequence-id", name="sequence", logical_type="Integer",
         nullable=True, role="field", model_ref="sample.sequence"),
    dict(column_id="amount-id", name="amount", logical_type="Integer",
         nullable=True, role="field", model_ref="sample.amount",
         label="Exact amount", description="Synthetic integer fixture", unit="units"),
]
IDENTITY = dict(execution_id="adbc-demo-query", result_id="adbc-demo-result",
                data_source_id="sqlite-demo-v1", model={"id": "sqlite-demo", "revision": "1"},
                columns=COLUMNS, expires_at="2099-01-01T00:00:00Z")


def seed_database(path):
    # Fixture setup alone writes SQLite. The adapter subsequently opens mode=ro.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE sample (sequence INTEGER, amount INTEGER)")
        connection.executemany("INSERT INTO sample VALUES (?, ?)",
                               [(3, -7), (1, 9007199254740993), (2, None)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New private output directory (must not already exist)")
    parser.add_argument("--single-stream", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / f"fixture-{uuid4().hex}.sqlite"
    try:
        seed_database(database)
        capture = materialize_sqlite(database, SQL, output=args.output,
                                     page_rows=None if args.single_stream else 2, **IDENTITY)
        with (args.output / "capture.json").open("x", encoding="utf-8") as stream:
            json.dump(capture, stream, indent=2)
            stream.write("\n")
    except Exception:
        # This CLI created the directory exclusively; no pre-existing caller data.
        for path in args.output.iterdir():
            path.unlink()
        args.output.rmdir()
        raise
    finally:
        database.unlink(missing_ok=True)
    print(f"Wrote {len(capture['resources'])} immutable IPC resource(s) to {args.output}")


if __name__ == "__main__":
    try:
        main()
    except MaterializationError as error:
        raise SystemExit(json.dumps(error.public_detail())) from None
