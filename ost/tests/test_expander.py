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


def staff_catalog():
    catalog = Catalog()
    catalog.add_model(
        "m",
        """
version: 0.3.0.dev0
name: m
datasets:
  - name: staff
    source: scott.emp
    metrics:
      - name: sum_sal
        expression:
          sql: SUM(sal)
""",
    )
    return catalog


@pytest.mark.parametrize(
    "sql",
    [
        "select agg(sum_sal) from (select distinct deptno, sum_sal from m.staff) t",
        "select agg(sum_sal) from (select distinct * from m.staff) t",
    ],
)
def test_distinct_rejects_measure_pass_through(sql):
    # Spec 7.8: DISTINCT over an unevaluated measure is rejected.
    with pytest.raises(ValueError, match="DISTINCT over unevaluated measure sum_sal"):
        expand(sql, staff_catalog())


def test_distinct_allows_fields_and_evaluated_measures():
    assert "DISTINCT" in expand("select distinct deptno from m.staff", staff_catalog())
    assert "SUM(sal)" in expand("select distinct agg(sum_sal) from m.staff", staff_catalog())
