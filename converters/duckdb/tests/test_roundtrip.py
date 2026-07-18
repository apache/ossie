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

from ossie_duckdb import convert_duckdb_to_osi_yaml, convert_osi_to_duckdb


def test_import_then_export_executes(orders_db):
    """Database -> Ossie YAML -> SQL script, executed back on the same database."""
    osi_yaml = convert_duckdb_to_osi_yaml(orders_db)
    sql = convert_osi_to_duckdb(osi_yaml, view_schema="semantic")
    orders_db.execute(sql)

    assert orders_db.execute("SELECT COUNT(*) FROM semantic.orders").fetchone()[0] == 3
    comment = orders_db.execute(
        "SELECT comment FROM duckdb_views() WHERE view_name = 'orders' AND schema_name = 'semantic'"
    ).fetchone()[0]
    assert comment == "Order transactions"


def test_roundtrip_preserves_structure(orders_db):
    """A second import of the exported views yields the same dataset/field shape."""
    osi_yaml = convert_duckdb_to_osi_yaml(orders_db)
    orders_db.execute(convert_osi_to_duckdb(osi_yaml, view_schema="semantic"))

    reimported = convert_duckdb_to_osi_yaml(orders_db, schema="semantic")
    assert "name: orders" in reimported
    assert "name: customers" in reimported
    assert "expression: amount" in reimported
