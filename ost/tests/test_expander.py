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

MODEL = """\
version: 0.3.0.dev0
name: m
datasets:
  - name: staff
    source: scott.emp
    metrics:
      - name: sum_sal
        expression: {sql: SUM(sal)}
"""


@pytest.fixture
def catalog():
    catalog = Catalog()
    catalog.add_model("m", MODEL)
    return catalog


def test_distinct_rejects_measure_pass_through(catalog):
    # Spec 7.8: DISTINCT over an unevaluated measure is rejected.
    sql = "select agg(sum_sal) from (select distinct deptno, sum_sal from m.staff) t"
    with pytest.raises(ValueError, match="DISTINCT over unevaluated measure sum_sal"):
        expand(sql, catalog)
    with pytest.raises(ValueError, match="DISTINCT over unevaluated measure sum_sal"):
        expand("select agg(sum_sal) from (select distinct * from m.staff) t", catalog)


def test_distinct_allows_fields_and_evaluated_measures(catalog):
    assert "DISTINCT" in expand("select distinct deptno from m.staff", catalog)
    assert "SUM(sal)" in expand("select distinct agg(sum_sal) from m.staff", catalog)
