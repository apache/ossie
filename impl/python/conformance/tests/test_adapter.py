"""Smoke tests for the osi_python compliance-suite adapter.

The adapter is a thin translator (see ADAPTER_INTERFACE.md); these
tests lock down the translation rules, not the underlying planner.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ADAPTER_PATH = Path(__file__).resolve().parent.parent / "adapter.py"


def _load_adapter_module():
    spec = importlib.util.spec_from_file_location(
        "osi_conformance_adapter", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def adapter():
    return _load_adapter_module()


def test_field_marker_becomes_role(adapter) -> None:
    dim = adapter._translate_field({"name": "c", "expression": "c", "dimension": {}})
    fact = adapter._translate_field({"name": "amount", "expression": "amount"})
    time = adapter._translate_field(
        {"name": "d", "expression": "d", "dimension": {"is_time": True}}
    )
    assert dim["role"] == "dimension"
    assert fact["role"] == "fact"
    assert time["role"] == "time_dimension"
    assert "dimension" not in dim
    assert "dimension" not in time


def test_translator_drops_unknown_field_markers(adapter) -> None:
    out = adapter._translate_field(
        {"name": "balance", "expression": "balance", "snapshot_dimensions": ["d"]}
    )
    assert "snapshot_dimensions" not in out
    assert out["role"] == "fact"


def test_dim_ref_parses_qualified_and_bare(adapter) -> None:
    r1 = adapter._dim_ref("orders.category")
    r2 = adapter._dim_ref("category")
    r3 = adapter._dim_ref({"name": "category", "dataset": "orders"})
    assert str(r1) == "orders.category"
    assert str(r2) == "category"
    assert str(r3) == "orders.category"


def test_measure_ref_prefers_metric_over_name(adapter) -> None:
    r = adapter._measure_ref({"name": "total", "metric": "sum_amount"})
    assert str(r) == "sum_amount"


def test_translate_model_roundtrips_through_parser(adapter) -> None:
    raw = {
        "name": "orders_basic",
        "datasets": [
            {
                "name": "orders",
                "source": "orders",
                "primary_key": ["id"],
                "fields": [
                    {"name": "id", "expression": "id", "dimension": {}},
                    {"name": "amount", "expression": "amount"},
                ],
            }
        ],
        "metrics": [{"name": "total", "expression": "SUM(amount)"}],
    }
    reshaped = adapter._translate_model(raw)
    # The YAML round-trip must survive parse_semantic_model.
    from osi.parsing.parser import parse_semantic_model

    result = parse_semantic_model(yaml.safe_dump(reshaped))
    field_roles = {f.name: f.role.value for f in result.model.datasets[0].fields}
    assert field_roles == {"id": "dimension", "amount": "fact"}


def test_adapter_end_to_end_on_trivial_model(adapter, tmp_path: Path) -> None:
    model_yaml = tmp_path / "model.yaml"
    model_yaml.write_text("""name: m
datasets:
  - name: orders
    source: orders
    primary_key: [id]
    fields:
      - name: id
        expression: id
        dimension: {}
      - name: category
        expression: category
        dimension: {}
      - name: amount
        expression: amount
metrics:
  - name: total
    expression: SUM(amount)
""")
    query_json = tmp_path / "query.json"
    query_json.write_text(
        '{"dataset": "orders", "dimensions": ["category"], '
        '"measures": [{"name": "total", "metric": "total"}]}'
    )

    rc = adapter.main(
        [
            "sql",
            "--model",
            str(model_yaml),
            "--query-file",
            str(query_json),
            "--dialect",
            "duckdb",
        ]
    )
    assert rc == 0
