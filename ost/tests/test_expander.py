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

import pytest
from ossie_ost.expander import Catalog, expand


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "select count(*) from m.dept",
            ["FROM scott.dept"],
        ),
        (
            """
            select agg(sum_sal)
            from m.staff
            where deptno in (
                select deptno from m.dept where loc = 'DALLAS'
            )
            """,
            ["FROM scott.dept", "FROM scott.emp AS staff", "SUM(sal)"],
        ),
        (
            """
            select dname, count(*) as c
            from m.emp join m.dept using (deptno)
            group by dname
            """,
            ["FROM scott.emp", "JOIN scott.dept"],
        ),
    ],
)
def test_dataset_without_metrics(sql, expected):
    catalog = Catalog()
    catalog.add_model(
        "m",
        """
version: 0.3.0.dev0
name: m
datasets:
  - name: dept
    source: scott.dept
  - name: emp
    source: scott.emp
  - name: staff
    source: scott.emp
    metrics:
      - name: sum_sal
        expression:
          sql: SUM(sal)
""",
    )

    result = expand(sql, catalog)

    for fragment in expected:
        assert fragment in result

    assert "FROM m.dept" not in result
    assert "FROM m.emp" not in result
    assert "JOIN m.dept" not in result


def _query_source_catalog(source: str) -> Catalog:
    catalog = Catalog()
    catalog.add_model(
        "q",
        f"""
version: 0.3.0.dev0
name: q
datasets:
  - name: clerks
    source: {source}
    primary_key: [empno]
    metrics:
      - name: sum_sal
        expression:
          sql: SUM(sal)
""",
    )
    return catalog


@pytest.mark.parametrize(
    "source, expected",
    [
        # Issue 811, first symptom: one dot in the query was quoted as a
        # schema and a table. The Dataset schema (core-spec/spec.md)
        # allows source to be a query; the expansion must be a derived
        # table, as the issue's expected result states.
        (
            "SELECT empno, sal FROM scott.emp WHERE job = 'CLERK'",
            [
                "FROM (",
                "FROM scott.emp",
                "job = 'CLERK'",
                ") AS clerks",
            ],
        ),
        # Issue 811, second symptom: more than two dots raised
        # "too many values to unpack (expected 3, got 5)".
        (
            "SELECT e.empno, e.sal FROM scott.emp AS e WHERE e.job = 'CLERK'",
            [
                "FROM (",
                "FROM scott.emp AS e",
                "e.job = 'CLERK'",
                ") AS clerks",
            ],
        ),
        # A WITH query is still a query, so it is also a derived table.
        (
            "WITH c AS (SELECT empno, sal FROM scott.emp WHERE job = 'CLERK') SELECT * FROM c",
            [
                "FROM (",
                "WITH c AS (",
                "FROM scott.emp",
                ") AS clerks",
            ],
        ),
        # A UNION ALL source is a query (sqlglot: Union, not Select).
        # Checking only Select would send this back through to_table.
        (
            "SELECT empno, sal FROM scott.emp WHERE job = 'CLERK' "
            "UNION ALL SELECT empno, sal FROM scott.emp WHERE job = 'MANAGER'",
            [
                "FROM (",
                "FROM scott.emp",
                "UNION ALL",
                "job = 'CLERK'",
                "job = 'MANAGER'",
                ") AS clerks",
            ],
        ),
    ],
)
def test_query_source_becomes_a_derived_table(source, expected):
    result = expand("SELECT AGG(sum_sal) AS sum_sal FROM q.clerks", _query_source_catalog(source))
    for fragment in expected:
        assert fragment in result
    assert "FROM q.clerks" not in result
    # The old to_table path quotes a one-dot query as a schema and table.
    assert 'FROM "SELECT' not in result


def test_query_source_keeps_a_table_alias():
    # The query in issue 811, read under an explicit alias.
    result = expand(
        "SELECT AGG(sum_sal) AS sum_sal FROM q.clerks AS c",
        _query_source_catalog("SELECT empno, sal FROM scott.emp WHERE job = 'CLERK'"),
    )
    assert ") AS c" in result
    assert "AS clerks" not in result


def test_table_source_is_unchanged():
    # A table name must still be inlined as a table, as in every
    # existing expand script around scott.emp.
    result = expand(
        "SELECT AGG(sum_sal) AS sum_sal FROM q.clerks",
        _query_source_catalog("scott.emp"),
    )
    assert "FROM scott.emp AS clerks" in result
    assert "FROM (" not in result


def test_parenthesized_query_source_has_one_pair_of_parentheses():
    # A source that is already a parenthesized subquery is a query
    # (sqlglot: Subquery, not Select). It must keep a single pair of
    # parentheses, not be wrapped again.
    result = expand(
        "SELECT AGG(sum_sal) AS sum_sal FROM q.clerks",
        _query_source_catalog("(SELECT empno, sal FROM scott.emp WHERE job = 'CLERK')"),
    )
    expected = "FROM (\n  SELECT\n    empno,\n    sal\n  FROM scott.emp\n  WHERE\n    job = 'CLERK'\n) AS clerks"
    assert expected in result
    assert "FROM (\n  (" not in result
    assert 'FROM "(' not in result


def test_unparseable_table_name_is_still_a_table():
    # scott.select does not parse as SQL (select is a keyword). The
    # baseline expander still read it as a table, scott."select".
    # A missing ParseError handler would fail this source instead of
    # falling back to the table name.
    result = expand(
        "SELECT AGG(sum_sal) AS sum_sal FROM q.clerks",
        _query_source_catalog("scott.select"),
    )
    assert 'FROM scott."select" AS clerks' in result
    assert "FROM (" not in result
