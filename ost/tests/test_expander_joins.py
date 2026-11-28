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

from collections import Counter
from pathlib import Path

import duckdb
import pytest

from ossie_ost.expander import Catalog, expand

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def catalog():
    catalog = Catalog()
    catalog.add_model(
        "scott_model",
        (ROOT / "compliance/query/scott/scott_model.ossie.yaml").read_text(),
    )
    return catalog


@pytest.fixture
def connection():
    with duckdb.connect() as connection:
        connection.execute((ROOT / "ost/src/ossie_ost/fixtures/scott.sql").read_text())
        yield connection


def test_join_without_measures_executes(catalog, connection):
    sql = expand(
        """
        SELECT emp.ename, dept.dname
        FROM scott_model.emp
        LEFT JOIN scott_model.dept ON emp.deptno = dept.deptno
        WHERE emp.sal > 2900
        GROUP BY emp.ename, dept.dname
        """,
        catalog,
    )

    assert sorted(connection.execute(sql).fetchall()) == [
        ("FORD", "RESEARCH"),
        ("JONES", "RESEARCH"),
        ("KING", "ACCOUNTING"),
        ("SCOTT", "RESEARCH"),
    ]


@pytest.mark.parametrize(
    "query",
    [
        "SELECT e.ename, d.dname FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno WHERE e.sal > 2900 ORDER BY e.ename",
        "SELECT dname, COUNT(*) FROM scott_model.emp "
        "JOIN scott_model.dept USING (deptno) GROUP BY dname",
        "SELECT e.ename, e.sal + 10 FROM scott_model.emp e "
        "LEFT JOIN scott_model.dept d ON e.deptno = d.deptno",
        "WITH staff AS (SELECT e.ename FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno) SELECT COUNT(*) FROM staff",
        "SELECT t.ename FROM (SELECT e.ename FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno) t",
        "SELECT e.sal AS emp_count FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno ORDER BY emp_count",
        "SELECT e.sal AS emp_count FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno ORDER BY EMP_COUNT",
        "SELECT e.deptno, COUNT(*) AS emp_count FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno GROUP BY e.deptno HAVING emp_count > 3",
        "SELECT d.* FROM scott_model.emp e JOIN scott.dept d ON e.deptno = d.deptno",
        "SELECT DISTINCT e.deptno, d.dname FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT e.ename FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno "
        "WHERE EXISTS (SELECT 1 FROM scott_model.bonus b WHERE b.ename = e.ename)",
        "SELECT 'sum_sal', e.ename FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno",
    ],
)
def test_field_only_joins_match_plain_sql(query, catalog, connection):
    reference = query.replace("scott_model.", "scott.")
    actual = connection.execute(expand(query, catalog)).fetchall()
    expected = connection.execute(reference).fetchall()
    if "ORDER BY" in query:
        assert actual == expected
    else:
        assert Counter(actual) == Counter(expected)


def test_measure_evaluation_in_scalar_subquery_is_independent(catalog, connection):
    query = (
        "SELECT e.ename, (SELECT AGG(dept_count) FROM scott_model.dept) AS n "
        "FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno"
    )
    reference = (
        "SELECT e.ename, (SELECT COUNT(*) FROM scott.dept) AS n "
        "FROM scott.emp e JOIN scott.dept d ON e.deptno = d.deptno"
    )
    assert connection.execute(expand(query, catalog)).fetchall() == connection.execute(reference).fetchall()


@pytest.mark.parametrize(
    "subquery",
    [
        "SELECT sum_sal FROM (SELECT 1 AS sum_sal) t",
        "SELECT t.sum_sal FROM (SELECT 1 AS sum_sal) t",
        "SELECT sum_sal FROM (SELECT 1 AS n) t(sum_sal)",
        "SELECT sum_sal FROM (SELECT 0 AS n, 1 AS sum_sal) t(other)",
        "WITH t AS (SELECT 1 AS sum_sal) SELECT sum_sal FROM t",
        "SELECT (SELECT sum_sal) FROM (SELECT 1 AS sum_sal) t",
    ],
)
def test_subquery_local_field_does_not_use_outer_measure(subquery, catalog, connection):
    query = (
        f"SELECT e.ename, ({subquery}) AS n FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno ORDER BY e.ename LIMIT 1"
    )
    assert connection.execute(expand(query, catalog)).fetchall() == [("ADAMS", 1)]


@pytest.mark.parametrize(
    "subquery",
    [
        "SELECT sum_sal",
        "SELECT sum_sal FROM (SELECT 1 AS other) t",
        "SELECT (SELECT sum_sal) FROM (SELECT 1 AS other) t",
    ],
)
def test_correlated_measure_in_subquery_stays_unsupported(subquery, catalog):
    query = (
        f"SELECT e.ename, ({subquery}) FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno"
    )
    with pytest.raises(ValueError, match="does not support measures through a join"):
        expand(query, catalog)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT e.sum_sal FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT sum_sal FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT e.sum_sal AS total FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT e.sum_sal + 1 FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT * FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT e.* FROM scott_model.emp e JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT AGG(sum_sal) FROM (SELECT e.sum_sal FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno) t",
        "SELECT AGG(sum_sal) FROM (SELECT * FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno) t",
        "WITH staff AS (SELECT e.sum_sal FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno) SELECT AGG(sum_sal) FROM staff",
        "SELECT e.ename FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno WHERE e.sum_sal > 0",
        "SELECT e.ename FROM scott_model.emp e JOIN scott_model.dept d ON e.sum_sal = d.deptno",
        "SELECT e.ename FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno ORDER BY e.sum_sal",
        "SELECT e.ename, (SELECT e.sum_sal) FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno",
        "SELECT e.deptno FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno GROUP BY e.deptno HAVING sum_sal > 0",
        "SELECT e.ename FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno QUALIFY sum_sal > 0",
    ],
)
def test_join_using_unevaluated_measures_stays_unsupported(query, catalog):
    with pytest.raises(ValueError, match="does not support measures through a join"):
        expand(query, catalog)


def test_join_that_evaluates_measures_keeps_its_results(catalog, connection):
    query = (
        "SELECT d.dname, AGG(e.sum_sal) FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno GROUP BY d.dname ORDER BY d.dname"
    )
    reference = (
        "SELECT d.dname, SUM(e.sal) FROM scott.emp e "
        "JOIN scott.dept d ON e.deptno = d.deptno GROUP BY d.dname ORDER BY d.dname"
    )
    assert connection.execute(expand(query, catalog)).fetchall() == connection.execute(reference).fetchall()


@pytest.mark.parametrize(
    ("query", "reference"),
    [
        (
            "SELECT e.ename FROM scott_model.emp e WHERE sal > 2900 ORDER BY e.ename",
            "SELECT e.ename FROM scott.emp e WHERE sal > 2900 ORDER BY e.ename",
        ),
        (
            "SELECT COUNT(*) FROM scott_model.emp",
            "SELECT COUNT(*) FROM scott.emp",
        ),
        (
            "SELECT e.sal AS sum_sal FROM scott_model.emp e ORDER BY SUM_SAL",
            "SELECT e.sal AS sum_sal FROM scott.emp e ORDER BY SUM_SAL",
        ),
        (
            "SELECT AGG(total) FROM (SELECT sum_sal AS total FROM scott_model.emp) t",
            "SELECT SUM(sal) FROM scott.emp",
        ),
    ],
)
def test_single_relation_queries_keep_their_results(query, reference, catalog, connection):
    assert connection.execute(expand(query, catalog)).fetchall() == connection.execute(reference).fetchall()


def test_field_only_join_keeps_query_source_inside_derived_table(catalog, connection):
    model = catalog.models["scott_model"]
    datasets = [
        dataset.model_copy(update={"source": "SELECT * FROM scott.emp WHERE sal > 2900"})
        if dataset.name == "emp" else dataset
        for dataset in model.datasets
    ]
    catalog.models["scott_model"] = model.model_copy(update={"datasets": datasets})
    query = (
        "SELECT e.ename, d.dname FROM scott_model.emp e "
        "JOIN scott_model.dept d ON e.deptno = d.deptno ORDER BY e.ename"
    )
    reference = (
        "SELECT e.ename, d.dname FROM (SELECT * FROM scott.emp WHERE sal > 2900) e "
        "JOIN scott.dept d ON e.deptno = d.deptno ORDER BY e.ename"
    )
    assert connection.execute(expand(query, catalog)).fetchall() == connection.execute(reference).fetchall()


@pytest.fixture
def implicit_join_catalog():
    catalog = Catalog()
    catalog.add_model(
        "m",
        """
        version: 0.3.0.dev0
        name: m
        datasets:
          - name: a
            source: SELECT 1 AS k, 10 AS m
            primary_key: [k]
            fields: [{name: k, expression: {sql: k}}]
            metrics: [{name: m, expression: {sql: SUM(k)}}]
          - name: b
            source: SELECT 1 AS k, 20 AS m
            primary_key: [k]
            fields: [{name: k, expression: {sql: k}}]
            metrics: [{name: m, expression: {sql: SUM(k)}}]
          - name: c
            source: SELECT 1 AS k, 20 AS m
            primary_key: [k]
            fields: [{name: k, expression: {sql: k}}]
            metrics: [{name: n, expression: {sql: SUM(k)}}]
        """,
    )
    return catalog


@pytest.mark.parametrize(
    "query",
    [
        "SELECT a.k FROM m.a a JOIN m.b b USING (m)",
        "SELECT a.k FROM m.a a JOIN m.b b USING (M)",
        "SELECT a.k FROM m.a a NATURAL JOIN m.b b",
        "SELECT a.k FROM m.a a NATURAL LEFT JOIN m.b b",
        "SELECT AGG(a.m) FROM m.a a JOIN m.b b USING (m)",
        "SELECT AGG(a.m) FROM m.a a NATURAL JOIN m.b b",
        "SELECT a.k FROM m.a a JOIN (SELECT 10 AS m) b USING (m)",
        "SELECT a.k FROM (SELECT 10 AS m) b JOIN m.a a USING (m)",
        "SELECT a.k FROM m.a a NATURAL JOIN (SELECT 10 AS m) b",
    ],
)
def test_implicit_measure_join_keys_are_rejected(query, implicit_join_catalog):
    with pytest.raises(ValueError, match="measure.*join key"):
        expand(query, implicit_join_catalog)


@pytest.mark.parametrize(
    "join",
    [
        "JOIN m.c c USING (k)",
        "NATURAL JOIN m.c c",
        "NATURAL LEFT JOIN m.c c",
    ],
)
def test_implicit_field_join_keys_use_model_columns(join, implicit_join_catalog, connection):
    # Both physical sources also have an undeclared column m. A NATURAL
    # JOIN of the model relations must compare only the shared field k.
    query = f"SELECT a.k, c.k FROM m.a a {join}"
    assert connection.execute(expand(query, implicit_join_catalog)).fetchall() == [(1, 1)]


@pytest.mark.parametrize(
    "query",
    [
        "SELECT a.k FROM m.a a NATURAL JOIN (SELECT 1 AS k) b",
        "SELECT a.k FROM m.a a NATURAL JOIN (SELECT 1 AS other) b(k)",
        "WITH b AS (SELECT 1 AS k) SELECT a.k FROM m.a a NATURAL JOIN b",
        "WITH b(k) AS (SELECT 1 AS other) SELECT a.k FROM m.a a NATURAL JOIN b",
        "SELECT a.k FROM m.a a NATURAL JOIN (SELECT 1 AS other) b",
        "SELECT a.k FROM (SELECT 1 AS k, 10 AS m) a JOIN (SELECT 1 AS k, 10 AS m) b USING (m) "
        "JOIN m.a c ON a.k = c.k",
        "SELECT AGG(a.m) FROM m.a a NATURAL JOIN m.c c",
    ],
)
def test_implicit_join_keys_respect_each_join_scope(query, implicit_join_catalog, connection):
    assert connection.execute(expand(query, implicit_join_catalog)).fetchall() == [(1,)]


def test_natural_join_with_unknown_source_columns_is_rejected(implicit_join_catalog):
    with pytest.raises(ValueError, match="cannot resolve NATURAL JOIN keys"):
        expand("SELECT a.k FROM m.a a NATURAL JOIN other_table b", implicit_join_catalog)


@pytest.mark.parametrize("join", ["NATURAL JOIN", "NATURAL LEFT JOIN", "NATURAL FULL JOIN"])
@pytest.mark.parametrize("measure_input", [False, True])
def test_natural_join_of_measure_free_inputs_uses_model_fields(join, measure_input, implicit_join_catalog, connection):
    model = implicit_join_catalog.models["m"]
    datasets = [
        dataset.model_copy(update={"metrics": []}) if dataset.name in ("a", "b") else dataset
        for dataset in model.datasets
    ]
    implicit_join_catalog.models["m"] = model.model_copy(update={"datasets": datasets})
    query = f"SELECT a.k, b.k FROM m.a a {join} m.b b"
    if measure_input:
        query += " JOIN m.c c ON a.k = c.k"

    assert connection.execute(expand(query, implicit_join_catalog)).fetchall() == [(1, 1)]


@pytest.mark.parametrize(
    "query",
    [
        "SELECT b.k FROM (SELECT m FROM m.a) t NATURAL JOIN (SELECT 2 AS k) b",
        "WITH t AS (SELECT m FROM m.a) SELECT b.k FROM t NATURAL JOIN (SELECT 2 AS k) b",
        "SELECT b.k FROM (SELECT m AS total FROM m.a) t NATURAL JOIN (SELECT 2 AS k) b",
        "WITH t AS (SELECT m AS total FROM m.a) SELECT b.k FROM t NATURAL JOIN (SELECT 2 AS k) b",
        "SELECT b.k FROM (SELECT m FROM (SELECT m FROM m.a) x) t NATURAL JOIN (SELECT 2 AS k) b",
        "SELECT b.k FROM (SELECT m FROM m.a UNION ALL SELECT m FROM m.b) t "
        "NATURAL JOIN (SELECT 2 AS k) b ORDER BY b.k",
    ],
)
def test_natural_join_does_not_use_measure_backing_columns(query, implicit_join_catalog, connection):
    expected = [(2,), (2,)] if "UNION ALL" in query else [(2,)]
    assert connection.execute(expand(query, implicit_join_catalog)).fetchall() == expected


@pytest.mark.parametrize(
    "query",
    [
        "SELECT b.k FROM (SELECT m FROM m.a) t NATURAL JOIN (SELECT 2 AS m, 2 AS k) b",
        "WITH t AS (SELECT m AS total FROM m.a) SELECT b.k FROM t "
        "NATURAL JOIN (SELECT 2 AS total, 2 AS k) b",
    ],
)
def test_natural_join_still_rejects_logical_measure_keys(query, implicit_join_catalog):
    with pytest.raises(ValueError, match="measure.*join key"):
        expand(query, implicit_join_catalog)


@pytest.mark.parametrize(("key", "expected"), [(1, [(1,)]), (2, [])])
def test_natural_join_keeps_explicit_logical_field_keys(key, expected, implicit_join_catalog, connection):
    query = f"SELECT b.k FROM (SELECT k, m FROM m.a) t NATURAL JOIN (SELECT {key} AS k) b"
    assert connection.execute(expand(query, implicit_join_catalog)).fetchall() == expected
