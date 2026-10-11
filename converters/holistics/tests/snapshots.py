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

"""What every snapshot holds, computed once and shared.

`tests/test_snapshots.py` compares these against the committed files, and
`tools/update_snapshots.py` writes them. One definition, so a snapshot can never
disagree with the test that checks it.
"""
from __future__ import annotations

import json
from pathlib import Path

from ossie_holistics import _yaml, aml_to_ossie, ossie_to_aml
from ossie_holistics.issues import IssueLog

REPO_ROOT = Path(__file__).resolve().parents[3]
CONVERTER_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = CONVERTER_ROOT / "tests" / "fixtures"

ECOMMERCE_COMPILED = FIXTURES / "ecommerce" / "ecommerce.dataset.aml.json"
TPCDS_OSSIE = REPO_ROOT / "examples" / "tpcds_semantic_model.yaml"
AQL_TRANSLATION = FIXTURES / "aql" / "translation.ossie.yaml"
METRIC_REFERENCES = FIXTURES / "metric_refs" / "references.ossie.yaml"

#: The fixture is a BigQuery dataset: `DATE_TRUNC(created_at, month)` is BigQuery
#: argument order, not ANSI SQL.
SQL_DIALECT = "BIGQUERY"

#: TPC-DS carries no HOLISTICS stash, so the AML-required connection name has to
#: come from the caller. Its expressions are all `ANSI_SQL`, so naming that as
#: the target warehouse writes every body as it stands.
TPCDS_DATA_SOURCE = "tpcds_warehouse"
TPCDS_SQL_DIALECT = "ANSI_SQL"

LICENSE_HEADER = """\
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

"""


def _issues(log: IssueLog) -> str:
    return LICENSE_HEADER + _yaml.dump_issues(log.as_dicts())


def forward() -> tuple[dict[str, str], aml_to_ossie.Result]:
    """Compiled AML to Ossie: one YAML document and its issue log."""
    payload = json.loads(ECOMMERCE_COMPILED.read_text(encoding="utf-8"))
    result = aml_to_ossie.convert(payload, sql_dialect=SQL_DIALECT)
    return {
        "ecommerce/expected.ossie.yaml": LICENSE_HEADER + _yaml.dump(result.model),
        "ecommerce/expected.issues.yaml": _issues(result.issues),
    }, result


def reverse_ecommerce() -> tuple[dict[str, str], ossie_to_aml.Result]:
    """Ossie back to AML, starting from this converter's own forward output.

    Reading the forward result rather than the committed snapshot keeps the two
    directions from drifting apart between snapshot updates.
    """
    _, forward_result = forward()
    result = ossie_to_aml.convert(forward_result.model, SQL_DIALECT)
    files = {f"ecommerce/expected_aml/{name}": text for name, text in result.files}
    files["ecommerce/expected_aml.issues.yaml"] = _issues(result.issues)
    return files, result


def reverse_tpcds() -> tuple[dict[str, str], ossie_to_aml.Result]:
    """Ossie to AML, starting from a document this converter did not write.

    `examples/tpcds_semantic_model.yaml` carries no HOLISTICS stash, so every
    AML property the core specification has no field for is derived or supplied.
    """
    document = _yaml.load(TPCDS_OSSIE.read_text(encoding="utf-8"))
    result = ossie_to_aml.convert(
        document, TPCDS_SQL_DIALECT, data_source_name=TPCDS_DATA_SOURCE
    )
    files = {f"tpcds/expected_aml/{name}": text for name, text in result.files}
    files["tpcds/expected_aml.issues.yaml"] = _issues(result.issues)
    return files, result


def reverse_aql() -> tuple[dict[str, str], ossie_to_aml.Result]:
    """Ossie to AML for a document written to exercise the SQL to AQL translation.

    Every metric in it spans two datasets, which is what makes AML take an
    `@aql` body, and each covers a different path through `aql.translate`.
    """
    document = _yaml.load(AQL_TRANSLATION.read_text(encoding="utf-8"))
    result = ossie_to_aml.convert(document, "ANSI_SQL", data_source_name="bigquery_demo")
    files = {f"aql/expected_aml/{name}": text for name, text in result.files}
    files["aql/expected_aml.issues.yaml"] = _issues(result.issues)
    return files, result


def reverse_metric_references() -> tuple[dict[str, str], ossie_to_aml.Result]:
    """Ossie to AML for metrics that name other metrics.

    Which AML home a metric lands in decides how it can name another one, so
    these cover a reference into each home, and the placement that a body
    naming only another metric depends on.
    """
    document = _yaml.load(METRIC_REFERENCES.read_text(encoding="utf-8"))
    result = ossie_to_aml.convert(document, "ANSI_SQL", data_source_name="bigquery_demo")
    files = {f"metric_refs/expected_aml/{name}": text for name, text in result.files}
    files["metric_refs/expected_aml.issues.yaml"] = _issues(result.issues)
    return files, result


def all_snapshots() -> dict[str, str]:
    """Every committed snapshot, keyed by its path under `tests/fixtures/`."""
    files: dict[str, str] = {}
    for produce in (
        forward,
        reverse_ecommerce,
        reverse_tpcds,
        reverse_aql,
        reverse_metric_references,
    ):
        files.update(produce()[0])
    return files
