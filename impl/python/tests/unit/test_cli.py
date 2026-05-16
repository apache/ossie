"""Tests for the ``python -m osi`` CLI surface."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from osi.cli import main

_MODEL_YAML = textwrap.dedent("""\
    semantic_model:
      - name: demo
        dialect: ANSI_SQL
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [order_id]
            fields:
              - name: order_id
                expression: order_id
                role: dimension
              - name: customer_id
                expression: customer_id
                role: dimension
              - name: status
                expression: status
                role: dimension
              - name: amount
                expression: amount
                role: fact
          - name: customers
            source: sales.customers
            primary_key: [id]
            fields:
              - name: id
                expression: id
                role: dimension
              - name: region
                expression: region
                role: dimension
        relationships:
          - name: orders_to_customers
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
        metrics:
          - name: total_revenue
            expression: SUM(orders.amount)
    """)

_QUERY_JSON = {
    "dimensions": [{"dataset": "customers", "name": "region"}],
    "measures": [{"dataset": "orders", "name": "total_revenue"}],
}


@pytest.fixture()
def model_path(tmp_path: Path) -> Path:
    p = tmp_path / "model.yaml"
    p.write_text(_MODEL_YAML)
    return p


@pytest.fixture()
def query_path(tmp_path: Path) -> Path:
    p = tmp_path / "query.json"
    p.write_text(json.dumps(_QUERY_JSON))
    return p


def test_describe__text_mode(capsys, model_path: Path) -> None:
    rc = main(["describe", str(model_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "orders" in out and "customers" in out


def test_describe__json_mode(capsys, model_path: Path) -> None:
    rc = main(["describe", str(model_path), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "demo"
    assert {d["name"] for d in data["datasets"]} == {"orders", "customers"}


def test_explain__shows_plan_steps(capsys, model_path: Path, query_path: Path) -> None:
    rc = main(["explain", str(model_path), str(query_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "step_000" in out


def test_resolve__lists_relationships(
    capsys, model_path: Path, query_path: Path
) -> None:
    rc = main(["resolve", str(model_path), str(query_path), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "orders_to_customers" in {r["name"] for r in data["relationships"]}


def test_compile__emits_duckdb_sql(capsys, model_path: Path, query_path: Path) -> None:
    rc = main(["compile", str(model_path), str(query_path), "--dialect", "duckdb"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WITH" in out and "SELECT" in out


def test_explain_code__by_enum_name(capsys) -> None:
    rc = main(["explain-code", "E_NAME_NOT_FOUND"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("E_NAME_NOT_FOUND")
    assert "did not resolve" in out


def test_explain_code__by_numeric_value(capsys) -> None:
    rc = main(["explain-code", "E1001"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("E1001")
    assert "YAML" in out


def test_explain_code__case_insensitive(capsys) -> None:
    rc = main(["explain-code", "e_name_not_found"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("E_NAME_NOT_FOUND")


def test_explain_code__json_mode(capsys) -> None:
    rc = main(["explain-code", "E_NAME_NOT_FOUND", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["code"] == "E_NAME_NOT_FOUND"
    assert "explanation" in data and data["explanation"]


def test_explain_code__list_emits_every_code(capsys) -> None:
    rc = main(["explain-code", "--list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    from osi.errors import ErrorCode

    assert set(data) == {c.value for c in ErrorCode}


def test_explain_code__unknown_code_returns_2(capsys) -> None:
    rc = main(["explain-code", "NOT_A_CODE"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "is not a known OSI error code" in err


def test_explain_code__missing_code_returns_2(capsys) -> None:
    rc = main(["explain-code"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "required" in err


def test_cli__reports_osi_errors_via_stderr(
    capsys, tmp_path: Path, model_path: Path
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "dimensions": [],
                "measures": [{"dataset": "orders", "name": "no_such_metric"}],
            }
        )
    )
    rc = main(["explain", str(model_path), str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("E")
