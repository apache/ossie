"""Third-party OSI metrics must never be silently lost on import.

Regression coverage for the review on OSI PR #153: the converter only emits
``ANSI_SQL`` on the OBML -> OSI path, so our own roundtrip corpus never exercised
the import drop paths. A genuinely foreign OSI model (Snowflake/Databricks
authored, or a non-SQL dialect) hit them and lost metrics with only a warning.

Two guarantees are tested here:

1. **Dialect catching** - a metric whose only expression is ``SNOWFLAKE`` or
   ``DATABRICKS`` (SQL engines OrionBelt targets) is converted, not dropped.
2. **No silent loss** - a metric with only a non-SQL dialect (``MDX``), or an
   expression OBML can't decompose, is preserved verbatim (re-emitted on the way
   back) and raises a loud ``LOSSY:`` warning.
"""

from __future__ import annotations

from typing import Any

import pytest

import ossie_orionbelt.converter as conv


def _osi_model(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Minimal single-dataset OSI v0.2 model carrying the given metrics."""
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "sales",
                "datasets": [
                    {
                        "name": "Orders",
                        "source": "ANALYTICS.PUBLIC.ORDERS",
                        "fields": [
                            {"name": "amount", "data_type": "number"},
                            {"name": "id", "data_type": "integer"},
                        ],
                    }
                ],
                "metrics": metrics,
            }
        ],
    }


def _metric(name: str, dialect: str, expression: str, **extra: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "name": name,
        "expression": {"dialects": [{"dialect": dialect, "expression": expression}]},
    }
    m.update(extra)
    return m


class TestDialectCatching:
    """SNOWFLAKE / DATABRICKS aggregations convert like ANSI_SQL."""

    def test_snowflake_simple_agg_becomes_measure(self) -> None:
        osi = _osi_model([_metric("Total Amount", "SNOWFLAKE", "SUM(Orders.amount)")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        assert "Total Amount" in obml.get("measures", {})
        assert obml["measures"]["Total Amount"]["aggregation"] == "sum"
        # Converted cleanly - no loss warning, nothing stashed.
        assert not any(w.startswith("LOSSY:") for w in converter.warnings)
        assert "customExtensions" not in obml or all(
            "obml_unconverted_metrics" not in ext.get("data", "")
            for ext in obml["customExtensions"]
        )

    def test_databricks_expression_agg_becomes_measure(self) -> None:
        osi = _osi_model([_metric("Net", "DATABRICKS", "SUM(Orders.amount * Orders.amount)")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        assert "Net" in obml.get("measures", {})
        assert not any(w.startswith("LOSSY:") for w in converter.warnings)

    def test_snowflake_uppercased_identifiers_resolve_to_canonical(self) -> None:
        # Snowflake commonly upper-cases identifiers. They must resolve back to
        # the real OSI dataset/field names, not produce refs to ORDERS.AMOUNT.
        osi = _osi_model([_metric("Total Amount", "SNOWFLAKE", "SUM(ORDERS.AMOUNT)")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        ref = obml["measures"]["Total Amount"]["columns"][0]
        assert ref == {"dataObject": "Orders", "column": "amount"}
        assert not any(w.startswith("LOSSY:") for w in converter.warnings)

    def test_quoted_identifiers_resolve_to_canonical(self) -> None:
        # Quoted forms must be unquoted and resolved, not fall through to an
        # expression measure containing raw "Orders"."amount".
        osi = _osi_model([_metric("Total Amount", "SNOWFLAKE", 'SUM("Orders"."amount")')])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        measure = obml["measures"]["Total Amount"]
        assert measure["columns"][0] == {"dataObject": "Orders", "column": "amount"}
        assert "expression" not in measure  # resolved to a simple-agg measure
        assert not any(w.startswith("LOSSY:") for w in converter.warnings)

    def test_decimal_literals_not_rewritten_as_refs(self) -> None:
        # A decimal literal inside an expression measure must stay literal, not
        # become an OBML ref like {[1].[23]} (which fails validate_obml).
        osi = _osi_model([_metric("Scaled", "ANSI_SQL", "SUM(Orders.amount * 1.23)")])
        obml = conv.OSItoOBML(osi).convert()
        measure = (obml.get("measures") or {}).get("Scaled") or (obml.get("metrics") or {})[
            "Scaled"
        ]
        assert "1.23" in measure["expression"]
        assert "{[1]" not in measure["expression"]
        assert conv.validate_obml(obml).valid

    def test_expression_agg_identifiers_resolved(self) -> None:
        # Canonical refs inside an expression measure, from upper-cased input.
        osi = _osi_model([_metric("Net", "SNOWFLAKE", "SUM(ORDERS.AMOUNT * ORDERS.ID)")])
        obml = conv.OSItoOBML(osi).convert()
        expr = obml["measures"]["Net"]["expression"]
        assert "{[Orders].[amount]}" in expr and "{[Orders].[id]}" in expr
        assert "ORDERS" not in expr

    def test_unmappable_reference_is_preserved_not_dangling(self) -> None:
        # A column that does not exist in the model must not become a dangling
        # OBML reference; the metric is preserved verbatim with a LOSSY warning.
        osi = _osi_model([_metric("Bogus", "SNOWFLAKE", "SUM(Orders.nonexistent)")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        assert "Bogus" not in obml.get("measures", {})
        assert "Bogus" not in obml.get("metrics", {})
        assert any(m["name"] == "Bogus" for m in _unconverted_stash(obml))
        assert any(w.startswith("LOSSY:") and "Bogus" in w for w in converter.warnings)

    def test_unknown_dataset_reference_is_preserved(self) -> None:
        osi = _osi_model([_metric("Cross", "SNOWFLAKE", "SUM(Unknown.amount)")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()
        assert "Cross" not in obml.get("measures", {})
        assert any(m["name"] == "Cross" for m in _unconverted_stash(obml))

    def test_ansi_preferred_over_other_dialects(self) -> None:
        osi = _osi_model(
            [
                {
                    "name": "Total Amount",
                    "expression": {
                        "dialects": [
                            {"dialect": "SNOWFLAKE", "expression": "SUM(Orders.amount)"},
                            {"dialect": "ANSI_SQL", "expression": "SUM(Orders.id)"},
                        ]
                    },
                }
            ]
        )
        obml = conv.OSItoOBML(osi).convert()
        # ANSI_SQL wins regardless of ordering -> column is `id`, not `amount`.
        assert obml["measures"]["Total Amount"]["columns"][0]["column"] == "id"


class TestNoSilentLoss:
    """Non-convertible metrics are preserved + warned, never dropped."""

    def test_non_sql_dialect_is_preserved_not_dropped(self) -> None:
        osi = _osi_model([_metric("Mdx Thing", "MDX", "[Measures].[Amount]")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        # Not emitted as an OBML measure/metric...
        assert "Mdx Thing" not in obml.get("measures", {})
        assert "Mdx Thing" not in obml.get("metrics", {})
        # ...but preserved verbatim under the OSI vendor...
        stash = _unconverted_stash(obml)
        assert any(m["name"] == "Mdx Thing" for m in stash)
        # ...and loudly flagged.
        assert any(w.startswith("LOSSY:") and "Mdx Thing" in w for w in converter.warnings)

    def test_real_maql_metric_syntax_is_preserved(self) -> None:
        # MAQL is an analytical query language, not SQL, with `{fact/...}` /
        # `{metric/...}` object references. A MAQL-only metric must be preserved
        # verbatim, not mis-parsed as SQL and dropped.
        maql = "SELECT SUM({fact/store_sales.fact.store_sales.ss_net_profit})"
        osi = _osi_model([_metric("Total Profit", "MAQL", maql)])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        assert "Total Profit" not in obml.get("measures", {})
        preserved = next((m for m in _unconverted_stash(obml) if m["name"] == "Total Profit"), None)
        assert preserved is not None
        # Verbatim: the MAQL expression survives untouched for the round trip.
        assert preserved["expression"]["dialects"][0]["expression"] == maql
        assert any(w.startswith("LOSSY:") and "Total Profit" in w for w in converter.warnings)

    def test_undecomposable_sql_is_preserved_not_dropped(self) -> None:
        # A non-aggregated expression: no AGG(...) to lift into a measure, so
        # it decomposes into nothing and cannot become an OBML metric.
        osi = _osi_model([_metric("Ratio", "ANSI_SQL", "Orders.amount / Orders.id")])
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        assert "Ratio" not in obml.get("measures", {})
        assert "Ratio" not in obml.get("metrics", {})
        assert any(m["name"] == "Ratio" for m in _unconverted_stash(obml))
        assert any(w.startswith("LOSSY:") and "Ratio" in w for w in converter.warnings)

    def test_roundtrip_restores_preserved_metric(self) -> None:
        original = _metric("Mdx Thing", "MDX", "[Measures].[Amount]", description="from cube")
        osi = _osi_model([original])

        obml = conv.OSItoOBML(osi).convert()
        osi_again = conv.OBMLtoOSI(obml, "sales").convert()

        metrics = osi_again["semantic_model"][0].get("metrics", [])
        restored = next((m for m in metrics if m["name"] == "Mdx Thing"), None)
        assert restored is not None
        # Verbatim: expression dialect + description survive the round trip.
        assert restored["description"] == "from cube"
        assert restored["expression"]["dialects"][0]["dialect"] == "MDX"

    def test_mixed_model_keeps_convertible_and_preserves_rest(self) -> None:
        osi = _osi_model(
            [
                _metric("Total Amount", "SNOWFLAKE", "SUM(Orders.amount)"),
                _metric("Mdx Thing", "MDX", "[Measures].[Amount]"),
            ]
        )
        converter = conv.OSItoOBML(osi)
        obml = converter.convert()

        assert "Total Amount" in obml["measures"]
        assert any(m["name"] == "Mdx Thing" for m in _unconverted_stash(obml))
        lossy = [w for w in converter.warnings if w.startswith("LOSSY:")]
        assert len(lossy) == 1 and "Mdx Thing" in lossy[0]


class TestRestoredMetricMetadata:
    """A preserved metric's dialect/vendor survive on the metric itself, not in
    a (non-conformant) root array. See OSI PR #148."""

    def test_preserved_metric_keeps_dialect_and_vendor_on_the_metric(self) -> None:
        # MDX metric carrying a GOODDATA custom extension. After OSI -> OBML ->
        # OSI the metric round-trips with its dialect on expression.dialects[]
        # and its vendor on custom_extensions - the schema-valid homes.
        metric = _metric("Cube Metric", "MDX", "[Measures].[Amount]")
        metric["custom_extensions"] = [{"vendor_name": "GOODDATA", "data": "{}"}]
        osi = _osi_model([metric])

        obml = conv.OSItoOBML(osi).convert()
        osi_again = conv.OBMLtoOSI(obml, "sales").convert()

        restored = next(
            m for m in osi_again["semantic_model"][0]["metrics"] if m["name"] == "Cube Metric"
        )
        assert [d["dialect"] for d in restored["expression"]["dialects"]] == ["MDX"]
        assert any(e["vendor_name"] == "GOODDATA" for e in restored["custom_extensions"])

    def test_no_root_dialects_or_vendors_emitted(self) -> None:
        # The output must stay schema-conformant: no root-level dialects/vendors,
        # whether or not a preserved metric is present.
        osi = _osi_model(
            [
                _metric("Total", "ANSI_SQL", "SUM(Orders.amount)"),
                _metric("Cube Metric", "MDX", "[Measures].[Amount]"),
            ]
        )
        obml = conv.OSItoOBML(osi).convert()
        osi_again = conv.OBMLtoOSI(obml, "sales").convert()
        assert "dialects" not in osi_again
        assert "vendors" not in osi_again


class TestStaleStashNameCollision:
    """A queryable OBML metric wins over a stale preserved metric of same name."""

    def test_collision_drops_preserved_copy_not_duplicate(self) -> None:
        # Import an unconvertible metric named "Revenue"...
        osi = _osi_model([_metric("Revenue", "MDX", "[Measures].[Rev]")])
        obml = conv.OSItoOBML(osi).convert()
        assert any(m["name"] == "Revenue" for m in _unconverted_stash(obml))

        # ...then the user adds a real OBML measure with the same name.
        obml["measures"] = {
            "Revenue": {
                "columns": [{"dataObject": "Orders", "column": "amount"}],
                "resultType": "float",
                "aggregation": "sum",
            }
        }

        converter = conv.OBMLtoOSI(obml, "sales")
        osi_again = converter.convert()

        metrics = osi_again["semantic_model"][0].get("metrics", [])
        revenue = [m for m in metrics if m["name"] == "Revenue"]
        # Exactly one "Revenue" — no duplicate that would fail validation.
        assert len(revenue) == 1
        # The queryable (converted) one wins: it has a real SQL expression.
        assert revenue[0]["expression"]["dialects"][0]["dialect"] == "ANSI_SQL"
        # And the drop is reported.
        assert any("Revenue" in w and "dropped on export" in w for w in converter.warnings)

    def test_output_passes_osi_validation_after_collision(self) -> None:
        pytest.importorskip("jsonschema")  # validate_osi needs it
        osi = _osi_model([_metric("Revenue", "MDX", "[Measures].[Rev]")])
        obml = conv.OSItoOBML(osi).convert()
        obml["measures"] = {
            "Revenue": {
                "columns": [{"dataObject": "Orders", "column": "amount"}],
                "resultType": "float",
                "aggregation": "sum",
            }
        }
        osi_again = conv.OBMLtoOSI(obml, "sales").convert()
        result = conv.validate_osi(osi_again)
        assert result.valid, result.errors


class TestIdempotency:
    """convert() may be called twice on one instance without duplication."""

    def test_osi_to_obml_convert_is_idempotent(self) -> None:
        osi = _osi_model(
            [
                _metric("Total Amount", "ANSI_SQL", "SUM(Orders.amount)"),
                _metric("Mdx Thing", "MDX", "[Measures].[Amount]"),
            ]
        )
        converter = conv.OSItoOBML(osi)
        first = converter.convert()
        first_warnings = list(converter.warnings)
        second = converter.convert()

        # Preserved-metric stash is not duplicated on the second pass.
        assert len(_unconverted_stash(first)) == 1
        assert len(_unconverted_stash(second)) == 1
        # Warnings do not accumulate across calls.
        assert converter.warnings == first_warnings

    def test_obml_to_osi_convert_is_idempotent(self) -> None:
        obml = conv.OSItoOBML(
            _osi_model([_metric("Mdx Thing", "MDX", "[Measures].[Amount]")])
        ).convert()
        converter = conv.OBMLtoOSI(obml, "sales")
        converter.convert()
        warnings_after_first = list(converter.warnings)
        osi_again = converter.convert()

        metrics = osi_again["semantic_model"][0].get("metrics", [])
        assert [m["name"] for m in metrics].count("Mdx Thing") == 1
        assert converter.warnings == warnings_after_first


def _unconverted_stash(obml: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the preserved OSI metrics out of the OSI-vendor customExtension."""
    import json

    for ext in obml.get("customExtensions", []) or []:
        if ext.get("vendor") in ("OSI", "OBSL"):
            data = json.loads(ext.get("data", "{}"))
            if "obml_unconverted_metrics" in data:
                return data["obml_unconverted_metrics"]
    return []
