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

"""Import a DuckDB database schema as an Ossie semantic model.

Tables and user views become datasets; primary key, unique, and foreign key
constraints become ``primary_key``, ``unique_keys``, and relationships; table
and column comments become descriptions. Works against any database DuckDB
can open, including MotherDuck (``md:``) connection strings.
"""

import duckdb
import yaml

from ossie_duckdb._common import SPEC_VERSION, ConversionError


def convert_duckdb_to_osi(
    database,
    schema: str = "main",
    model_name: str | None = None,
) -> dict:
    """Build an Ossie document (as a dict) from a DuckDB database.

    ``database`` is either an open DuckDB connection or a path / connection
    string accepted by ``duckdb.connect``.
    """
    conn = database if hasattr(database, "execute") else duckdb.connect(database)

    db_name = conn.execute("SELECT current_database()").fetchone()[0]

    tables = conn.execute(
        """
        SELECT table_name, comment
        FROM duckdb_tables()
        WHERE NOT internal AND database_name = ? AND schema_name = ?
        ORDER BY table_name
        """,
        [db_name, schema],
    ).fetchall()
    views = conn.execute(
        """
        SELECT view_name, comment
        FROM duckdb_views()
        WHERE NOT internal AND database_name = ? AND schema_name = ?
        ORDER BY view_name
        """,
        [db_name, schema],
    ).fetchall()

    entities = list(tables) + list(views)
    if not entities:
        raise ConversionError(f"No tables or views found in {db_name}.{schema}")
    entity_names = {name for name, _ in entities}

    columns: dict[str, list[tuple[str, str | None]]] = {}
    for table_name, column_name, comment in conn.execute(
        """
        SELECT table_name, column_name, comment
        FROM duckdb_columns()
        WHERE NOT internal AND database_name = ? AND schema_name = ?
        ORDER BY table_name, column_index
        """,
        [db_name, schema],
    ).fetchall():
        columns.setdefault(table_name, []).append((column_name, comment))

    primary_keys: dict[str, list[str]] = {}
    unique_keys: dict[str, list[list[str]]] = {}
    relationships: list[dict] = []
    for table_name, ctype, cols, ref_table, ref_cols in conn.execute(
        """
        SELECT table_name, constraint_type, constraint_column_names,
               referenced_table, referenced_column_names
        FROM duckdb_constraints()
        WHERE database_name = ? AND schema_name = ?
        ORDER BY table_name, constraint_index
        """,
        [db_name, schema],
    ).fetchall():
        if ctype == "PRIMARY KEY":
            primary_keys[table_name] = list(cols)
        elif ctype == "UNIQUE":
            unique_keys.setdefault(table_name, []).append(list(cols))
        elif ctype == "FOREIGN KEY" and ref_table in entity_names:
            relationships.append(
                {
                    "name": f"{table_name}_to_{ref_table}",
                    "from": table_name,
                    "to": ref_table,
                    "from_columns": list(cols),
                    "to_columns": list(ref_cols),
                }
            )

    # Disambiguate duplicate relationship names (e.g. two FKs to the same table).
    seen_names: dict[str, int] = {}
    for rel in relationships:
        count = seen_names.get(rel["name"], 0)
        seen_names[rel["name"]] = count + 1
        if count:
            rel["name"] = f"{rel['name']}_{count + 1}"

    datasets = []
    for entity_name, comment in entities:
        dataset: dict = {
            "name": entity_name,
            "source": f"{db_name}.{schema}.{entity_name}",
        }
        if entity_name in primary_keys:
            dataset["primary_key"] = primary_keys[entity_name]
        if entity_name in unique_keys:
            dataset["unique_keys"] = unique_keys[entity_name]
        if comment:
            dataset["description"] = comment
        fields = []
        for column_name, column_comment in columns.get(entity_name, []):
            field: dict = {
                "name": column_name,
                "expression": {"dialects": [{"dialect": "DUCKDB", "expression": column_name}]},
            }
            if column_comment:
                field["description"] = column_comment
            fields.append(field)
        if fields:
            dataset["fields"] = fields
        datasets.append(dataset)

    model: dict = {
        "name": model_name or f"{db_name}_{schema}",
        "datasets": datasets,
    }
    if relationships:
        model["relationships"] = relationships

    return {"version": SPEC_VERSION, "semantic_model": [model]}


def convert_duckdb_to_osi_yaml(
    database,
    schema: str = "main",
    model_name: str | None = None,
) -> str:
    """Same as convert_duckdb_to_osi, rendered as a YAML document."""
    document = convert_duckdb_to_osi(database, schema=schema, model_name=model_name)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
