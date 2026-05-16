"""End-to-end unit tests for :func:`osi.parsing.parser.parse_semantic_model`.

Covers the full pipeline (YAML → deferred check → pydantic → AST check →
cross-ref → namespace → graph) and the pydantic-error translation for
``E1001``/``E1002``/``E1003``/``E1004``.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from osi.errors import ErrorCode, OSIParseError
from osi.parsing import parse_semantic_model

_HAPPY_YAML = dedent("""
    semantic_model:
      - name: m
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [id]
            fields:
              - name: id
                expression: id
              - name: customer_id
                expression: customer_id
              - name: amount
                expression: amount
                role: fact
          - name: customers
            source: sales.customers
            primary_key: [id]
            fields:
              - name: id
                expression: id
        relationships:
          - name: o_c
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
        metrics:
          - name: revenue
            expression: SUM(orders.amount)
          - name: avg_rev
            expression: revenue / 2
        filters:
          - name: done
            expression: status = 'done'
        parameters:
          - name: min_amount
            data_type: NUMBER
            default: 0
    """).strip()


class TestHappyPath:
    def test_parse_string_source(self) -> None:
        result = parse_semantic_model(_HAPPY_YAML)
        assert result.model.name == "m"
        assert len(result.model.datasets) == 2
        assert len(result.graph.edges) == 1
        # Metrics are model-scoped (top-level) per Foundation v0.1
        # §4.5; per-dataset ``metrics:`` blocks are deferred.
        assert "revenue" in {str(m.name) for m in result.model.metrics}

    def test_parse_path_source(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "model.yaml"
        yaml_path.write_text(_HAPPY_YAML)
        result = parse_semantic_model(yaml_path)
        assert result.model.name == "m"

    def test_example_fixture_parses(self) -> None:
        example = (
            Path(__file__).resolve().parents[3]
            / "examples"
            / "models"
            / "demo_orders.yaml"
        )
        assert example.exists()
        result = parse_semantic_model(example)
        assert result.model.name == "demo_orders"


class TestYamlSyntaxErrors:
    def test_invalid_yaml_E1001(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(":\n  : bad\n : :")
        assert exc.value.code is ErrorCode.E1001_YAML_SYNTAX

    def test_empty_document_E1001(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model("")
        assert exc.value.code is ErrorCode.E1001_YAML_SYNTAX

    def test_root_not_mapping_E1004(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model("- just a list")
        assert exc.value.code is ErrorCode.E1004_TYPE_MISMATCH

    def test_semantic_model_list_wrong_length_E1002(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: a
                datasets: [{name: d, source: s, fields: [{name: id, expression: id}]}]
              - name: b
                datasets: [{name: d, source: s, fields: [{name: id, expression: id}]}]
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E1002_MISSING_REQUIRED_FIELD

    def test_missing_file_E1001(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(missing)
        assert exc.value.code is ErrorCode.E1001_YAML_SYNTAX


class TestSchemaTranslation:
    def test_extra_forbidden_E1001(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: m
                bogus: true
                datasets:
                  - name: orders
                    source: s
                    fields: [{name: id, expression: id}]
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E1001_YAML_SYNTAX

    def test_missing_required_field_E1002(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: m
                datasets:
                  - source: sales.orders
                    fields: [{name: id, expression: id}]
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E1002_MISSING_REQUIRED_FIELD

    def test_invalid_enum_E1003(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: m
                datasets:
                  - name: orders
                    source: sales.orders
                    fields:
                      - name: id
                        expression: id
                        role: measurement
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E1003_INVALID_ENUM_VALUE

    def test_bad_identifier_E1005(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: m
                datasets:
                  - name: "1bad"
                    source: sales.orders
                    fields: [{name: id, expression: id}]
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E1005_IDENTIFIER_INVALID


class TestDeferredIntegration:
    def test_metric_grain_key_E1105(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: m
                datasets:
                  - name: orders
                    source: sales.orders
                    fields:
                      - {name: id, expression: id}
                      - {name: amount, expression: amount, role: fact}
                metrics:
                  - name: rev
                    expression: SUM(amount)
                    grain: [id]
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED

    def test_window_in_metric_expression_accepted(self) -> None:
        # S-22 (D-028 / D-030): valid windowed metrics now parse
        # successfully; the deferred-construct check no longer fires
        # on bare ``OVER (...)``. Nested windows / deferred frame
        # modes are still rejected with their named codes
        # (E_NESTED_WINDOW, E_DEFERRED_FRAME_MODE) — see
        # `test_deferred.py::TestExpressionDeferred`.
        yaml = dedent("""
            semantic_model:
              - name: m
                datasets:
                  - name: orders
                    source: sales.orders
                    fields:
                      - {name: id, expression: id}
                      - {name: amount, expression: amount, role: fact}
                metrics:
                  - name: running
                    expression: "SUM(amount) OVER (ORDER BY id)"
            """).strip()
        result = parse_semantic_model(yaml)
        assert any(m.name == "running" for m in result.model.metrics)


class TestCrossRefIntegration:
    def test_relationship_unknown_column_E2006(self) -> None:
        yaml = dedent("""
            semantic_model:
              - name: m
                datasets:
                  - name: orders
                    source: s
                    primary_key: [id]
                    fields: [{name: id, expression: id}]
                  - name: customers
                    source: s
                    primary_key: [id]
                    fields: [{name: id, expression: id}]
                relationships:
                  - name: r
                    from: orders
                    to: customers
                    from_columns: [nope]
                    to_columns: [id]
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(yaml)
        assert exc.value.code is ErrorCode.E2006_INVALID_RELATIONSHIP


class TestParseResultShape:
    def test_parse_result_is_frozen(self) -> None:
        result = parse_semantic_model(_HAPPY_YAML)
        with pytest.raises((AttributeError, TypeError)):
            result.model = None  # type: ignore[misc]
