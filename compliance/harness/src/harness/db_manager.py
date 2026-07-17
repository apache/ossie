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

"""DuckDB database management for the test harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


class DBManager:
    """Manages DuckDB connections and dataset loading."""

    def __init__(self) -> None:
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._loaded_datasets: set[str] = set()

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Create a fresh in-memory DuckDB connection."""
        self.close()
        self._conn = duckdb.connect(":memory:")
        self._loaded_datasets.clear()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._loaded_datasets.clear()

    def load_dataset(self, dataset_name: str, datasets_dir: Path) -> None:
        """Load a dataset's schema.sql into the current connection."""
        if self._conn is None:
            raise RuntimeError("No active database connection")

        if dataset_name in self._loaded_datasets:
            return

        schema_path = datasets_dir / dataset_name / "schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Dataset schema not found: {schema_path}")

        sql_text = schema_path.read_text()
        for statement in sql_text.split(";"):
            lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
            stmt = "\n".join(lines).strip()
            if stmt:
                self._conn.execute(stmt)

        self._loaded_datasets.add(dataset_name)

    def execute_sql(self, sql: str) -> list[dict[str, Any]]:
        """Execute SQL and return results as a list of dicts."""
        if self._conn is None:
            raise RuntimeError("No active database connection")

        result = self._conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        return [dict(zip(columns, row)) for row in rows]

    def reset(self) -> None:
        """Drop all tables and reset the connection."""
        self.connect()
