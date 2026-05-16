"""Unit tests for :mod:`osi.parsing.deferred`.

Every Foundation-deferred feature must raise :class:`ErrorCode.E1105`
either at the YAML key level or inside a SQL expression.
"""

from __future__ import annotations

import pytest

from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.deferred import (
    DEFERRED_METRIC_KEYS,
    check_expression_deferred,
    check_yaml_deferred,
)

# ---------------------------------------------------------------------------
# YAML-level deferred keys
# ---------------------------------------------------------------------------


def _doc(extra: dict[str, object]) -> dict[str, object]:
    return {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "sales.orders",
                        "fields": [{"name": "id", "expression": "id"}],
                    }
                ],
                **extra,
            }
        ]
    }


class TestYamlDeferred:
    def test_metric_grain_key_rejected_E1105(self) -> None:
        doc = _doc(
            {"metrics": [{"name": "rev", "expression": "SUM(amount)", "grain": []}]}
        )
        with pytest.raises(OSIParseError) as exc:
            check_yaml_deferred(doc)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED

    def test_metric_filter_key_rejected_E1105(self) -> None:
        doc = _doc(
            {
                "metrics": [
                    {"name": "rev", "expression": "SUM(amount)", "filter": "x > 0"}
                ]
            }
        )
        with pytest.raises(OSIParseError) as exc:
            check_yaml_deferred(doc)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED

    def test_relationship_condition_key_rejected_E1105(self) -> None:
        doc = _doc(
            {
                "relationships": [
                    {
                        "name": "r",
                        "from": "orders",
                        "to": "orders",
                        "from_columns": ["id"],
                        "to_columns": ["id"],
                        "condition": "a > b",
                    }
                ]
            }
        )
        with pytest.raises(OSIParseError) as exc:
            check_yaml_deferred(doc)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED

    def test_dataset_level_filters_rejected_E1105(self) -> None:
        doc = {
            "semantic_model": [
                {
                    "name": "m",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "sales.orders",
                            "filters": [{"name": "f", "expression": "status = 'x'"}],
                            "fields": [{"name": "id", "expression": "id"}],
                        }
                    ],
                }
            ]
        }
        with pytest.raises(OSIParseError) as exc:
            check_yaml_deferred(doc)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED

    def test_field_grain_key_rejected_E1105(self) -> None:
        doc = {
            "semantic_model": [
                {
                    "name": "m",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "sales.orders",
                            "fields": [
                                {
                                    "name": "id",
                                    "expression": "id",
                                    "grain": "order_id",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        with pytest.raises(OSIParseError) as exc:
            check_yaml_deferred(doc)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED

    def test_happy_path_no_deferred_keys(self) -> None:
        doc = _doc({"metrics": [{"name": "rev", "expression": "SUM(amount)"}]})
        check_yaml_deferred(doc)  # must not raise

    def test_known_deferred_keys_enumerated(self) -> None:
        assert "grain" in DEFERRED_METRIC_KEYS
        assert "semi_additive" in DEFERRED_METRIC_KEYS
        assert "window" in DEFERRED_METRIC_KEYS


# ---------------------------------------------------------------------------
# Expression-level deferred constructs
# ---------------------------------------------------------------------------


def _frozen(expr: str) -> FrozenSQL:
    return FrozenSQL.of(parse_sql_expr(expr))


class TestExpressionDeferred:
    def test_window_function_accepted(self) -> None:
        # S-22 (D-028 / D-030): valid window functions pass parser
        # screening; only nested-window (D-031) and deferred frame
        # modes (D-032) raise their own named codes.
        expr = _frozen("ROW_NUMBER() OVER (ORDER BY id)")
        check_expression_deferred(expr, where="metric x")

    def test_scalar_expression_allowed(self) -> None:
        expr = _frozen("SUM(amount)")
        check_expression_deferred(expr, where="metric x")

    def test_case_when_allowed(self) -> None:
        expr = _frozen("CASE WHEN status = 'done' THEN 1 ELSE 0 END")
        check_expression_deferred(expr, where="metric x")
