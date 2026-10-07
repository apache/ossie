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

"""Snapshot tests over the two fixtures, plus the external checks that confirm
the snapshots are not merely stable but correct.

A snapshot test proves the converter still produces what it produced. It cannot
prove the output is right. Three checks here do that, each against the real
artifact rather than a proxy:

`test_the_converted_document_passes_the_ossie_schema` runs the forward output
through `core-spec/ossie-schema.json`.

`test_the_generated_aml_compiles` runs `holistics aml validate` over every
generated file, which parses the AML and type-checks its AQL.

`test_the_round_trip_returns_the_same_ossie_document` compiles the generated AML
back to JSON, converts that to Ossie, and compares it with the document the
reverse path started from. That closes the loop AML to Ossie to AML to Ossie.

Those that need the Holistics CLI skip when it is absent, so the suite runs
without it. CI installs it, so they do not skip there.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import snapshots
from ossie_holistics import _yaml, aml_to_ossie, aql, ossie_to_aml
from ossie_holistics.issues import Severity

HOLISTICS_CLI = shutil.which("holistics") or str(Path.home() / ".holistics" / "bin" / "holistics")

needs_cli = pytest.mark.skipif(
    not Path(HOLISTICS_CLI).exists(),
    reason="the Holistics CLI is not installed (https://docs.holistics.io/docs/cli)",
)


@pytest.mark.parametrize("relative", sorted(snapshots.all_snapshots()))
def test_the_snapshot_matches_what_the_converter_produces(relative):
    expected = snapshots.FIXTURES / relative
    assert expected.exists(), (
        f"{relative} is missing. Run python3 tools/update_snapshots.py"
    )
    assert snapshots.all_snapshots()[relative] == expected.read_text(encoding="utf-8"), (
        f"{relative} is stale. Read the diff, then run python3 tools/update_snapshots.py"
    )


def test_no_committed_snapshot_is_orphaned():
    """Every file under `tests/fixtures/expected*` is one a snapshot produces.

    Renaming a model leaves its old file behind, and a stale file that no test
    reads looks like coverage it is not.
    """
    produced = {snapshots.FIXTURES / name for name in snapshots.all_snapshots()}
    committed = {
        path
        for path in snapshots.FIXTURES.rglob("expected*")
        if path.is_file()
    } | {
        path
        for path in snapshots.FIXTURES.rglob("expected_aml/**/*")
        if path.is_file()
    }
    assert committed - produced == set()


def test_the_converted_document_passes_the_ossie_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (snapshots.REPO_ROOT / "core-spec" / "ossie-schema.json").read_text(encoding="utf-8")
    )
    _, result = snapshots.forward()
    jsonschema.validate(result.model, schema)


def test_the_forward_direction_reports_the_losses_it_declares():
    """Every limitation `docs/limitations.md` lists for this fixture is raised.

    Asserted on the codes rather than the count, so adding an unrelated issue
    does not fail this and removing a declared one does.
    """
    _, result = snapshots.forward()
    raised = {issue.code for issue in result.issues.issues}
    assert raised == {
        aml_to_ossie.ISSUE_AQL_NOT_TRANSLATED,
        aml_to_ossie.ISSUE_INACTIVE_RELATIONSHIP,
        aml_to_ossie.ISSUE_PARAM_DROPPED,
        aml_to_ossie.ISSUE_QUERY_SOURCE,
        aml_to_ossie.ISSUE_UNRESOLVED_QUERY,
    }


def test_every_qualified_reference_resolves():
    """A `<dataset>.<name>` reference names a field the document declares.

    apache/ossie#459 found 211 of 612 references in one converter's output where
    the qualifier was the Ossie dataset name and the other half was the
    warehouse column, so the pair resolved in neither namespace. Upstream's
    validator cannot see it, because `Dim_Customer.Customer_Name` parses as SQL.
    """
    _, result = snapshots.forward()
    model = result.model
    declared = {
        dataset["name"]: {field["name"] for field in dataset.get("fields") or []}
        for dataset in model["datasets"]
    }
    metrics = {metric["name"] for metric in model.get("metrics") or []}

    def sql_expressions():
        for dataset in model["datasets"]:
            for field in dataset.get("fields") or []:
                for entry in field["expression"]["dialects"]:
                    yield f"field {dataset['name']}.{field['name']}", entry
        for metric in model.get("metrics") or []:
            for entry in metric["expression"]["dialects"]:
                yield f"metric {metric['name']}", entry

    unresolved = []
    for where, entry in sql_expressions():
        if entry["dialect"] == aml_to_ossie.AQL_DIALECT:
            continue
        for qualifier, name in re.findall(r"\b([A-Za-z_]\w*)\.(\w+)", entry["expression"]):
            if name not in declared.get(qualifier, ()):
                unresolved.append(f"{where}: {qualifier}.{name}")
        for bare in re.findall(r"(?<![\w.])([A-Za-z_]\w*)(?!\s*\()(?![\w.])", entry["expression"]):
            if "__" in bare and bare not in metrics:
                unresolved.append(f"{where}: {bare}")

    assert not unresolved, "references naming nothing the document declares:\n" + "\n".join(
        unresolved
    )


@pytest.mark.parametrize(
    "produce",
    [
        snapshots.forward,
        snapshots.reverse_ecommerce,
        snapshots.reverse_tpcds,
        snapshots.reverse_aql,
        snapshots.reverse_metric_references,
    ],
)
def test_the_issue_log_survives_being_folded(produce):
    """Reading the committed YAML back gives the issues the converter raised.

    Folded style wraps a long message so a reviewer can read it in a diff, and a
    fold is only safe while it breaks at spaces. A break inside a URL would
    round-trip to the same string with a space in the middle of it.
    """
    _, result = produce()
    text = _yaml.dump_issues(result.issues.as_dicts())
    assert _yaml.load(text) == result.issues.as_dicts()


#: What `tests/fixtures/aql/translation.ossie.yaml` must still produce. Pinned by
#: metric name so a snapshot update cannot quietly stop covering a path.
AQL_TRANSLATIONS = {
    "quantity_per_customer": "sum(items.quantity) / count_distinct(users.id)",
    "rounded_ratio": "round(safe_divide(sum(items.quantity), abs(count(users.id))), 2)",
    "untyped_total": "sum(items.product_id) + count(users.id)",
    "borrowed_column_total": "sum(items.sale_price) + count(users.id)",
    "passthrough_call": "sum(items.quantity) + sql_number('MY_WAREHOUSE_UDF', count(users.id), 3)",
}

#: Metrics with no AQL rendering, and the text the ERROR for each one carries.
AQL_DROPPED = {
    "windowed_rank": "window function",
    "unmapped_function": "LOG",
}


def test_each_sql_to_aql_path_still_produces_what_it_did():
    _, result = snapshots.reverse_aql()
    dataset = next(text for name, text in result.files if name.endswith(".dataset.aml"))
    for metric, expected in AQL_TRANSLATIONS.items():
        assert f"definition: @aql {expected};;" in dataset, f"{metric} changed"
    for metric in AQL_DROPPED:
        assert f"metric {metric} " not in dataset, f"{metric} should have been dropped"


def test_each_dropped_metric_says_why():
    _, result = snapshots.reverse_aql()
    reasons = {
        issue.object_ref: issue.message
        for issue in result.issues.issues
        if issue.code == ossie_to_aml.ISSUE_METRIC_NOT_TRANSLATED
    }
    assert set(reasons) == set(AQL_DROPPED)
    for metric, expected in AQL_DROPPED.items():
        assert expected in reasons[metric], reasons[metric]


def test_the_aql_document_lists_every_fixture_metric():
    """`docs/aql.md` names one metric per translation path, and the fixture holds them.

    The document's table is how a reader finds the example for a path. A metric
    added to the fixture and not to the table has no example anyone can find,
    and a row naming a metric the fixture dropped sends them looking for one
    that is not there.
    """
    document = _yaml.load(snapshots.AQL_TRANSLATION.read_text(encoding="utf-8"))
    fixture = [metric["name"] for metric in document["metrics"]]
    text = (snapshots.CONVERTER_ROOT / "docs" / "aql.md").read_text(encoding="utf-8")
    # Scoped to its own section. The function table's rows share this shape.
    section = text[text.index("## Reading it back") :]
    table = re.findall(r"^\| `(\w+)` \| ", section, re.M)
    assert table == fixture


def test_the_documented_renames_are_the_only_renames():
    """`docs/aql.md` lists every function whose AQL name differs from its SQL one.

    The document's claim is that a SQL name carries over lowercased, with a
    short list of exceptions. A rename missing from that list makes the claim
    false for a reader who trusts it, and the count beside it goes stale the
    same way.
    """
    sql_names = []
    for node, aql_name in aql._functions().items():
        spellings = getattr(node, "sql_names", None)
        sql = spellings()[0] if spellings else node.__name__.upper()
        sql_names.append((sql, aql_name))

    renamed = {sql for sql, aql_name in sql_names if sql.lower() != aql_name}
    text = (snapshots.CONVERTER_ROOT / "docs" / "aql.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([A-Z_]+)` \| `\w+` \|$", text, re.M))
    assert documented == renamed

    kept = len(sql_names) - len(renamed)
    assert f"{kept} of the table's {len(sql_names)} entries" in text


def test_the_borrowed_column_becomes_a_hidden_dimension():
    """AQL resolves a reference against declared fields, and SQL does not.

    `SUM(items.sale_price)` names a column `items` never declares, so without
    the added dimension AQL answers ``Field `sale_price` not found in model
    `items` ``.
    """
    _, result = snapshots.reverse_aql()
    items = next(text for name, text in result.files if name.endswith("items.model.aml"))
    assert "dimension sale_price {" in items
    assert "hidden: true" in items
    # `product_id` declares no Ossie datatype, and a metric sums it, so the
    # metric is the only thing saying it is a number rather than text.
    assert "dimension product_id {" in items
    product_id = items[items.index("dimension product_id {") :]
    assert "type: 'number'" in product_id[: product_id.index("}")]


def test_a_sql_field_naming_another_dataset_is_an_error():
    """A `@sql` expression names only fields of the model it sits in.

    Holistics accepts the file and then fails at SQL generation with
    `Cannot refer to external field in TableModel/QueryModel`, so the converter
    has to catch this itself. No fixture reaches it, which is why it is asserted
    directly rather than through a snapshot.
    """
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "CAST(b.y AS DATE)"}]}
    document = {
        "version": aml_to_ossie.SPEC_VERSION,
        "name": "m",
        "datasets": [
            {
                "name": "a",
                "source": "db.a",
                "fields": [{"name": "x", "datatype": "Date", "expression": expression}],
            },
            {
                "name": "b",
                "source": "db.b",
                "fields": [
                    {
                        "name": "y",
                        "datatype": "Date",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "y"}]
                        },
                    }
                ],
            },
        ],
    }
    result = ossie_to_aml.convert(document, "ANSI_SQL", data_source_name="probe")
    raised = [i for i in result.issues.issues if i.code == ossie_to_aml.ISSUE_CROSS_MODEL_SQL]
    assert [i.object_ref for i in raised] == ["a.x"]
    assert raised[0].severity is Severity.ERROR
    assert result.issues.has_errors()


def _two_dataset_document(metrics: list[dict]) -> dict:
    """Two joined datasets and the metrics given, as one Ossie document."""

    def column(name: str) -> dict:
        return {
            "name": name,
            "datatype": "Decimal",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]},
        }

    return {
        "version": aml_to_ossie.SPEC_VERSION,
        "name": "m",
        "datasets": [
            {
                "name": "orders",
                "source": "db.orders",
                "primary_key": ["id"],
                "fields": [column("id"), column("amount")],
            },
            {
                "name": "users",
                "source": "db.users",
                "primary_key": ["id"],
                "fields": [column("id"), column("order_id")],
            },
        ],
        "relationships": [
            {
                "name": "users_to_orders",
                "from": "users",
                "to": "orders",
                "from_columns": ["order_id"],
                "to_columns": ["id"],
            }
        ],
        "metrics": metrics,
    }


def _metric(name: str, sql: str, **stashed) -> dict:
    payload = {
        "name": name,
        "datatype": "Decimal",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]},
    }
    if stashed:
        payload["custom_extensions"] = [
            {"vendor_name": "HOLISTICS", "data": json.dumps({"_v": 1, **stashed})}
        ]
    return payload


def test_a_sql_measure_naming_a_dataset_level_metric_is_an_error():
    """A `@sql` body has no spelling for a metric that lives on the dataset.

    `orders.spanning` qualifies a dataset-level metric with a model, and the
    sibling reference that reads like names a field `orders` does not have.
    Holistics accepts the file and fails at query time, so the converter has to
    catch it.
    """
    document = _two_dataset_document(
        [
            _metric("spanning", "SUM(orders.amount) / COUNT(users.id)"),
            _metric("doubled", "SUM(orders.amount) * orders.spanning"),
        ]
    )
    result = ossie_to_aml.convert(document, "ANSI_SQL", data_source_name="probe")
    raised = [i for i in result.issues.issues if i.code == ossie_to_aml.ISSUE_CROSS_MODEL_SQL]
    assert [i.object_ref for i in raised] == ["orders.doubled"]
    assert raised[0].severity is Severity.ERROR
    assert "dataset-level metric 'spanning'" in raised[0].message


def test_a_sql_measure_naming_another_models_measure_is_an_error():
    """A measure stashed onto one model, naming a measure of another.

    Without the stash the body's two models send the metric to the dataset,
    where AQL resolves it. The stash pins it to one model, and the `@sql` body
    that leaves cannot name the other, failing SQL generation with
    `Cannot refer to external field in TableModel/QueryModel`.
    """
    document = _two_dataset_document(
        [
            _metric("revenue", "SUM(orders.amount)", model="orders", aml_name="revenue"),
            _metric("per_user", "COUNT(users.id) + revenue", model="users", aml_name="per_user"),
        ]
    )
    result = ossie_to_aml.convert(document, "ANSI_SQL", data_source_name="probe")
    raised = [i for i in result.issues.issues if i.code == ossie_to_aml.ISSUE_CROSS_MODEL_SQL]
    assert [i.object_ref for i in raised] == ["users.per_user"]
    assert raised[0].severity is Severity.ERROR
    assert "a measure of model 'orders'" in raised[0].message


def test_a_metric_reference_cycle_is_an_error():
    """Placement follows metric references, so a cycle has to end the walk.

    `holistics aml validate` accepts a cycle, and Holistics inlines one metric
    into the next, so the pair has no SQL.
    """
    document = _two_dataset_document(
        [
            _metric("ping", "pong + 1"),
            _metric("pong", "ping * 2"),
        ]
    )
    result = ossie_to_aml.convert(document, "ANSI_SQL", data_source_name="probe")
    raised = [i for i in result.issues.issues if i.code == ossie_to_aml.ISSUE_METRIC_CYCLE]
    assert [i.object_ref for i in raised] == ["ping"]
    assert raised[0].severity is Severity.ERROR
    assert result.issues.has_errors()


#: sqlglot names a node after the shape it parsed, so several SQL spellings
#: share one node. The passthrough reads the original name back out of the
#: source using the offset sqlglot recorded for the name token.
SOURCE_NAMES = {
    "LPAD(a.s, 5)": "sql_number('LPAD', a.s, 5)",
    "RPAD(a.s, 5)": "sql_number('RPAD', a.s, 5)",
    "LTRIM(a.s)": "sql_number('LTRIM', a.s)",
    "RTRIM(a.s)": "sql_number('RTRIM', a.s)",
    "STRPOS(a.s, 'z')": "sql_number('STRPOS', a.s, 'z')",
    "MY_UDF(a.x)": "sql_number('MY_UDF', a.x)",
}

#: Calls sqlglot rewrote, so an argument carries no offset and the original
#: cannot be rebuilt. `LOG10` gains a base the source never had, and
#: `DATE_TRUNC` has its unit normalised, and an absent offset does not say which.
REWRITTEN_CALLS = ["LOG10(a.x)", "DATE_TRUNC('month', a.d)", "SUBSTRING(a.s, 1, 3)"]


@pytest.mark.parametrize("sql,expected", SOURCE_NAMES.items(), ids=list(SOURCE_NAMES))
def test_the_passthrough_uses_the_name_the_source_wrote(sql, expected):
    rendered, _ = aql.translate(sql, "ANSI_SQL", lambda table, column: f"{table}.{column}")
    assert rendered == expected


@pytest.mark.parametrize("sql", REWRITTEN_CALLS)
def test_a_call_sqlglot_rewrote_raises_untranslatable(sql):
    with pytest.raises(aql.Untranslatable):
        aql.translate(sql, "ANSI_SQL", lambda table, column: f"{table}.{column}")


def _write(files: dict[str, str], directory: Path) -> None:
    for name, text in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _validate_aml(directory: Path, dataset_file: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [HOLISTICS_CLI, "aml", "validate", dataset_file, "-r", "."],
        cwd=directory,
        capture_output=True,
        text=True,
    )


@needs_cli
@pytest.mark.parametrize(
    "produce,dataset_file",
    [
        (snapshots.reverse_ecommerce, "ecommerce.dataset.aml"),
        (snapshots.reverse_tpcds, "tpcds_retail_model.dataset.aml"),
        (snapshots.reverse_aql, "aql_translation.dataset.aml"),
        (snapshots.reverse_metric_references, "metric_references.dataset.aml"),
    ],
    ids=["ecommerce", "tpcds", "aql", "metric_refs"],
)
def test_the_generated_aml_compiles(produce, dataset_file, tmp_path):
    _, result = produce()
    _write(dict(result.files), tmp_path)
    completed = _validate_aml(tmp_path, dataset_file)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "No validation errors found" in completed.stdout


@needs_cli
def test_the_source_fixture_still_compiles_to_the_committed_json(tmp_path):
    """The committed JSON is what the CLI produces from the committed AML.

    The fixture ships both, and an edit to the AML that forgets the recompile
    would leave every other test reading the old payload.
    """
    source = snapshots.FIXTURES / "ecommerce" / "aml"
    completed = subprocess.run(
        [HOLISTICS_CLI, "aml", "compile", "ecommerce.dataset.aml", "-r", ".", "-o", str(tmp_path)],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    produced = json.loads((tmp_path / "ecommerce.dataset.aml.json").read_text(encoding="utf-8"))
    committed = json.loads(snapshots.ECOMMERCE_COMPILED.read_text(encoding="utf-8"))
    assert produced == committed


@needs_cli
def test_the_round_trip_returns_the_same_ossie_document(tmp_path):
    """AML to Ossie to AML to Ossie returns the document it started from.

    Compiling the generated AML is the only way to check the reverse path
    against the thing that actually reads AML, rather than against this
    converter's own idea of it.
    """
    _, forward_result = snapshots.forward()
    _, reverse_result = snapshots.reverse_ecommerce()

    generated = tmp_path / "aml"
    _write(dict(reverse_result.files), generated)
    compiled = tmp_path / "compiled"
    completed = subprocess.run(
        [HOLISTICS_CLI, "aml", "compile", "ecommerce.dataset.aml", "-r", ".", "-o", str(compiled)],
        cwd=generated,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    payload = json.loads((compiled / "ecommerce.dataset.aml.json").read_text(encoding="utf-8"))
    again = aml_to_ossie.convert(payload, sql_dialect=snapshots.SQL_DIALECT)
    assert _yaml.dump(again.model) == _yaml.dump(forward_result.model)
