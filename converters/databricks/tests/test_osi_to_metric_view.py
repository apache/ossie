"""Tests for osi_to_metric_view export logic.

Includes property-based tests (Hypothesis) for correctness property 6,
plus example-based unit tests for specific export mapping behaviors.
"""

from __future__ import annotations

import json
import warnings

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from osi.models import (
    OSIAIContextObject,
    OSICustomExtension,
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)

from osi_databricks.osi_to_metric_view import (
    osi_to_metric_view,
)

# --- Hypothesis Strategies ---

_identifier = st.from_regex(r"[a-z][a-z0-9_]{0,29}", fullmatch=True)
_safe_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=50,
)
_expr = st.from_regex(r"[A-Za-z_][A-Za-z0-9_. ()]{0,49}", fullmatch=True)

# Adversarial strings that test YAML quoting and JSON serialization edge cases
_yaml_dangerous_literals = st.sampled_from([
    "true", "false", "null", "~", "1.0", "0", "-1",
    "2024-01-01", "key: value", "has # comment",
    "{curly}", "[bracket]", "pipe | here",
    "star * wild", 'double"quote', "single'quote",
    "backslash\\here", "newline\\n", "tab\\t",
    "ratio: sales/cost", "a > b", "x & y",
    "  leading spaces", "trailing spaces  ",
])
_adversarial_text = st.one_of(_safe_text, _yaml_dangerous_literals)


@st.composite
def osi_field_with_ai_context(draw):
    """Generate an OSI field with ai_context.synonyms and description."""
    synonyms = draw(st.lists(_adversarial_text, min_size=1, max_size=5))
    description = draw(st.none() | _adversarial_text)
    expr_str = draw(_expr)
    name = draw(_identifier)

    return OSIField(
        name=name,
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str)]
        ),
        dimension=OSIDimension(is_time=False),
        description=description,
        ai_context=OSIAIContextObject(synonyms=tuple(synonyms)),
    )


@st.composite
def osi_metric_with_ai_context(draw):
    """Generate an OSI metric with ai_context.synonyms and description."""
    synonyms = draw(st.lists(_adversarial_text, min_size=1, max_size=5))
    description = draw(st.none() | _adversarial_text)
    expr_str = draw(_expr)
    name = draw(_identifier)

    return OSIMetric(
        name=name,
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str)]
        ),
        description=description,
        ai_context=OSIAIContextObject(synonyms=tuple(synonyms)),
    )


@st.composite
def osi_document_with_ai_context(draw):
    """Generate an OSI document with fields and metrics that have AI context."""
    fields = draw(st.lists(osi_field_with_ai_context(), min_size=1, max_size=3))
    metrics = draw(st.lists(osi_metric_with_ai_context(), min_size=1, max_size=3))
    dataset_name = draw(_identifier)

    dataset = OSIDataset(
        name=dataset_name,
        source=f"catalog.schema.{dataset_name}",
        fields=fields,
    )
    semantic_model = OSISemanticModel(
        name="test_model",
        datasets=[dataset],
        metrics=metrics,
    )
    return OSIDocument(
        version="0.2.0.dev0",
        dialects=[OSIDialect.DATABRICKS],
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[semantic_model],
    )


# --- Property 6: Export Maps AI Context to Synonyms and Comment ---


class TestExportAIContextMapping:
    """Property 6: Export maps AI context to synonyms and comment.

    For any OSI field or metric with ai_context.synonyms and/or description,
    the exporter SHALL map the first synonym to display_name, remaining synonyms
    to the synonyms list, and description to comment.
    """

    @given(doc=osi_document_with_ai_context())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_first_synonym_becomes_display_name(self, doc: OSIDocument):
        """First ai_context.synonym maps to display_name on exported fields."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        dataset = doc.semantic_model[0].datasets[0]
        if mv_model.fields and dataset.fields:
            for osi_field, mv_field in zip(dataset.fields, mv_model.fields):
                if osi_field.ai_context and osi_field.ai_context.synonyms:
                    assert mv_field.display_name == osi_field.ai_context.synonyms[0]

    @given(doc=osi_document_with_ai_context())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_remaining_synonyms_become_synonyms_list(self, doc: OSIDocument):
        """Remaining ai_context.synonyms map to the synonyms list on fields."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        dataset = doc.semantic_model[0].datasets[0]
        if mv_model.fields and dataset.fields:
            for osi_field, mv_field in zip(dataset.fields, mv_model.fields):
                if osi_field.ai_context and osi_field.ai_context.synonyms:
                    remaining = list(osi_field.ai_context.synonyms[1:])
                    if remaining:
                        assert mv_field.synonyms == remaining
                    else:
                        assert mv_field.synonyms is None

    @given(doc=osi_document_with_ai_context())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_description_becomes_comment(self, doc: OSIDocument):
        """OSI field description maps to Metric View field comment."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        dataset = doc.semantic_model[0].datasets[0]
        if mv_model.fields and dataset.fields:
            for osi_field, mv_field in zip(dataset.fields, mv_model.fields):
                assert mv_field.comment == osi_field.description

    @given(doc=osi_document_with_ai_context())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_first_synonym_becomes_display_name(self, doc: OSIDocument):
        """First ai_context.synonym maps to display_name on exported measures."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        metrics = doc.semantic_model[0].metrics
        if mv_model.measures and metrics:
            for osi_metric, mv_measure in zip(metrics, mv_model.measures):
                if osi_metric.ai_context and osi_metric.ai_context.synonyms:
                    assert mv_measure.display_name == osi_metric.ai_context.synonyms[0]

    @given(doc=osi_document_with_ai_context())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_remaining_synonyms_become_synonyms_list(self, doc: OSIDocument):
        """Remaining ai_context.synonyms map to the synonyms list on measures."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        metrics = doc.semantic_model[0].metrics
        if mv_model.measures and metrics:
            for osi_metric, mv_measure in zip(metrics, mv_model.measures):
                if osi_metric.ai_context and osi_metric.ai_context.synonyms:
                    remaining = list(osi_metric.ai_context.synonyms[1:])
                    if remaining:
                        assert mv_measure.synonyms == remaining
                    else:
                        assert mv_measure.synonyms is None

    @given(doc=osi_document_with_ai_context())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_description_becomes_comment(self, doc: OSIDocument):
        """OSI metric description maps to Metric View measure comment."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        metrics = doc.semantic_model[0].metrics
        if mv_model.measures and metrics:
            for osi_metric, mv_measure in zip(metrics, mv_model.measures):
                assert mv_measure.comment == osi_metric.description


# --- Unit Tests ---


class TestExportComplete:
    """Unit tests for complete OSI → Metric View export scenarios."""

    def test_export_with_databricks_dialect(self):
        """Export uses DATABRICKS dialect expressions."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[
                            OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="ss_net_paid"),
                            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="ss_net_paid"),
                        ]
                    ),
                )
            ],
            metrics=[
                OSIMetric(
                    name="total_sales",
                    expression=OSIExpression(
                        dialects=[
                            OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="SUM(ss_net_paid)"),
                        ]
                    ),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        assert len(results) == 1
        name, mv = results[0]
        assert name == "store_sales"
        assert mv.fields[0].expr == "ss_net_paid"
        assert mv.measures[0].expr == "SUM(ss_net_paid)"

    def test_fallback_to_ansi_sql(self):
        """Export falls back to ANSI_SQL when DATABRICKS dialect absent."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[
                            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="net_paid"),
                        ]
                    ),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        assert len(results) == 1
        _, mv = results[0]
        assert mv.fields[0].expr == "net_paid"

    def test_skip_field_no_usable_dialect(self):
        """Skip field when no DATABRICKS or ANSI_SQL dialect available."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="good_field",
                    expression=OSIExpression(
                        dialects=[
                            OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1"),
                        ]
                    ),
                ),
                OSIField(
                    name="bad_field",
                    expression=OSIExpression(
                        dialects=[
                            OSIDialectExpression(dialect=OSIDialect.SNOWFLAKE, expression="col2"),
                        ]
                    ),
                ),
            ],
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            results = osi_to_metric_view(doc)

        _, mv = results[0]
        # Only the good field should be exported
        assert len(mv.fields) == 1
        assert mv.fields[0].name == "good_field"
        # Warning should be emitted for skipped field
        assert any("bad_field" in str(warning.message) for warning in w)

    def test_skip_metric_no_usable_dialect(self):
        """Skip metric when no DATABRICKS or ANSI_SQL dialect available."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            metrics=[
                OSIMetric(
                    name="bad_metric",
                    expression=OSIExpression(
                        dialects=[
                            OSIDialectExpression(dialect=OSIDialect.SNOWFLAKE, expression="SUM(x)"),
                        ]
                    ),
                ),
            ],
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            results = osi_to_metric_view(doc)

        _, mv = results[0]
        assert mv.measures is None
        assert any("bad_metric" in str(warning.message) for warning in w)


class TestExportJoinReconstruction:
    """Unit tests for relationship → join ON clause reconstruction."""

    def test_single_key_join(self):
        """Single-key relationship reconstructed as ON clause."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            relationships=[
                OSIRelationship(
                    name="date_dim",
                    **{"from": "store_sales"},
                    to="date_dim",
                    from_columns=["ss_sold_date_sk"],
                    to_columns=["d_date_sk"],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.joins is not None
        assert len(mv.joins) == 1
        join = mv.joins[0]
        assert join.name == "date_dim"
        assert join.source == "date_dim"
        assert join.on == "source.ss_sold_date_sk = date_dim.d_date_sk"

    def test_composite_key_join(self):
        """Multi-key relationship reconstructed with AND in ON clause."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            relationships=[
                OSIRelationship(
                    name="items",
                    **{"from": "store_sales"},
                    to="order_items",
                    from_columns=["order_id", "item_id"],
                    to_columns=["order_id", "item_id"],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        join = mv.joins[0]
        assert join.on == "source.order_id = items.order_id AND source.item_id = items.item_id"

    def test_join_with_cardinality_and_rely(self):
        """Cardinality and rely restored from custom extensions."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            relationships=[
                OSIRelationship(
                    name="date_dim",
                    **{"from": "store_sales"},
                    to="date_dim",
                    from_columns=["date_sk"],
                    to_columns=["d_date_sk"],
                    custom_extensions=[
                        OSICustomExtension(
                            vendor_name=OSIVendor.DATABRICKS,
                            data=json.dumps({"cardinality": "many_to_one", "rely": {"at_most_one_match": True}}),
                        )
                    ],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        join = mv.joins[0]
        assert join.cardinality == "many_to_one"
        assert join.rely is not None
        assert join.rely.at_most_one_match is True


class TestExportAIContextUnit:
    """Unit tests for ai_context → display_name + synonyms mapping."""

    def test_synonyms_split_to_display_name_and_list(self):
        """First synonym → display_name, rest → synonyms."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="net_paid")]
                    ),
                    ai_context=OSIAIContextObject(synonyms=("Net Amount", "total paid", "payment")),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        field = mv.fields[0]
        assert field.display_name == "Net Amount"
        assert field.synonyms == ["total paid", "payment"]

    def test_single_synonym_becomes_display_name_only(self):
        """Single synonym → display_name with no synonyms list."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="net_paid")]
                    ),
                    ai_context=OSIAIContextObject(synonyms=("Net Amount",)),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        field = mv.fields[0]
        assert field.display_name == "Net Amount"
        assert field.synonyms is None

    def test_no_ai_context_produces_no_display_name(self):
        """No ai_context → no display_name or synonyms."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="net_paid")]
                    ),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        field = mv.fields[0]
        assert field.display_name is None
        assert field.synonyms is None

    def test_description_maps_to_comment(self):
        """OSI description → Metric View comment."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="net_paid")]
                    ),
                    description="Total net payment amount",
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        field = mv.fields[0]
        assert field.comment == "Total net payment amount"


class TestExportCustomExtensions:
    """Unit tests for materialization and filter restored from custom_extensions."""

    def test_filter_restored_from_extension(self):
        """Filter in dataset custom_extension → Metric View filter."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            dataset_extensions=[
                OSICustomExtension(
                    vendor_name=OSIVendor.DATABRICKS,
                    data=json.dumps({"filter": "quantity > 0"}),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.filter == "quantity > 0"

    def test_materialization_restored_from_extension(self):
        """Materialization in semantic model extension → Metric View materialization."""
        mat_data = {
            "schedule": "every 6 hours",
            "mode": "relaxed",
            "materialized_views": [
                {"name": "mv_daily", "type": "aggregated", "dimensions": ["date"], "measures": ["total"]}
            ],
        }
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            sm_extensions=[
                OSICustomExtension(
                    vendor_name=OSIVendor.DATABRICKS,
                    data=json.dumps({"materialization": mat_data}),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.materialization is not None
        assert mv.materialization.schedule == "every 6 hours"
        assert mv.materialization.mode == "relaxed"
        assert len(mv.materialization.materialized_views) == 1
        assert mv.materialization.materialized_views[0].name == "mv_daily"

    def test_source_query_restored_from_extension(self):
        """Source query in dataset custom_extension → Metric View source."""
        query = "SELECT a, b FROM my_table WHERE active"
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            dataset_extensions=[
                OSICustomExtension(
                    vendor_name=OSIVendor.DATABRICKS,
                    data=json.dumps({"source_query": query}),
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.source == query

    def test_other_vendor_extensions_ignored(self):
        """Extensions for other vendors (SNOWFLAKE, DBT) are ignored during export."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                    custom_extensions=[
                        OSICustomExtension(
                            vendor_name=OSIVendor.SNOWFLAKE,
                            data=json.dumps({"snowflake_specific": "value"}),
                        )
                    ],
                )
            ],
            dataset_extensions=[
                OSICustomExtension(
                    vendor_name=OSIVendor.DBT,
                    data=json.dumps({"dbt_specific": "value"}),
                )
            ],
        )

        # Should not error and should produce valid output
        results = osi_to_metric_view(doc)
        assert len(results) == 1
        _, mv = results[0]
        assert mv.fields[0].name == "f1"
        assert mv.filter is None  # No DATABRICKS filter extension

    def test_field_format_restored_from_extension(self):
        """Field format in custom_extension → Metric View field format."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="amount",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="net_paid")]
                    ),
                    custom_extensions=[
                        OSICustomExtension(
                            vendor_name=OSIVendor.DATABRICKS,
                            data=json.dumps({"format": {"type": "currency", "currency_code": "USD"}}),
                        )
                    ],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.fields[0].format is not None
        assert mv.fields[0].format.type == "currency"
        assert mv.fields[0].format.currency_code == "USD"

    def test_measure_window_restored_from_extension(self):
        """Measure window in custom_extension → Metric View measure window."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            metrics=[
                OSIMetric(
                    name="rolling_sum",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="SUM(amount)")]
                    ),
                    custom_extensions=[
                        OSICustomExtension(
                            vendor_name=OSIVendor.DATABRICKS,
                            data=json.dumps({"window": [{"order": "d_date", "range": "trailing 7 day"}]}),
                        )
                    ],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.measures[0].window is not None
        assert len(mv.measures[0].window) == 1
        assert mv.measures[0].window[0].order == "d_date"
        assert mv.measures[0].window[0].range == "trailing 7 day"


class TestExportDatasetSelection:
    """Unit tests for dataset selection logic."""

    def test_dataset_without_fields_or_metrics_skipped(self):
        """Datasets with no fields and no metrics are not exported."""
        doc = OSIDocument(
            version="0.2.0.dev0",
            semantic_model=[
                OSISemanticModel(
                    name="model",
                    datasets=[
                        OSIDataset(name="empty_ds", source="cat.sch.empty"),
                    ],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        assert len(results) == 0

    def test_multiple_datasets_produce_multiple_results(self):
        """Each dataset with content produces one (name, model) tuple."""
        doc = OSIDocument(
            version="0.2.0.dev0",
            semantic_model=[
                OSISemanticModel(
                    name="model",
                    datasets=[
                        OSIDataset(
                            name="ds1",
                            source="cat.sch.ds1",
                            fields=[
                                OSIField(
                                    name="f1",
                                    expression=OSIExpression(
                                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="c1")]
                                    ),
                                )
                            ],
                        ),
                        OSIDataset(
                            name="ds2",
                            source="cat.sch.ds2",
                            fields=[
                                OSIField(
                                    name="f2",
                                    expression=OSIExpression(
                                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="c2")]
                                    ),
                                )
                            ],
                        ),
                    ],
                    metrics=[
                        OSIMetric(
                            name="m1",
                            expression=OSIExpression(
                                dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="SUM(c1)")]
                            ),
                        )
                    ],
                )
            ],
        )

        results = osi_to_metric_view(doc)
        assert len(results) == 2
        names = [r[0] for r in results]
        assert "ds1" in names
        assert "ds2" in names

    def test_semantic_model_description_becomes_comment(self):
        """Semantic model description maps to Metric View top-level comment."""
        doc = _make_doc(
            fields=[
                OSIField(
                    name="f1",
                    expression=OSIExpression(
                        dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression="col1")]
                    ),
                )
            ],
            description="Store sales model",
        )

        results = osi_to_metric_view(doc)
        _, mv = results[0]
        assert mv.comment == "Store sales model"


# --- Test Helpers ---


def _make_doc(
    fields: list[OSIField] | None = None,
    metrics: list[OSIMetric] | None = None,
    relationships: list[OSIRelationship] | None = None,
    dataset_extensions: list[OSICustomExtension] | None = None,
    sm_extensions: list[OSICustomExtension] | None = None,
    description: str | None = None,
    dataset_name: str = "store_sales",
    dataset_source: str = "catalog.schema.store_sales",
) -> OSIDocument:
    """Helper to build a minimal OSI document for testing."""
    dataset = OSIDataset(
        name=dataset_name,
        source=dataset_source,
        fields=fields,
        custom_extensions=dataset_extensions,
    )
    semantic_model = OSISemanticModel(
        name="test_model",
        description=description,
        datasets=[dataset],
        relationships=relationships,
        metrics=metrics,
        custom_extensions=sm_extensions,
    )
    return OSIDocument(
        version="0.2.0.dev0",
        dialects=[OSIDialect.DATABRICKS],
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[semantic_model],
    )
