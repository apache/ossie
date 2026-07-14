"""Tests for metric_view_to_osi import logic.

Includes property-based tests (Hypothesis) for correctness properties 2, 3, and 4,
plus example-based unit tests for specific mapping behaviors.
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from osi.models import OSIDialect, OSIVendor

from osi_databricks.metric_view_to_osi import (
    metric_view_to_osi,
)
from osi_databricks.models import (
    MetricViewField,
    MetricViewFormat,
    MetricViewJoin,
    MetricViewMaterialization,
    MetricViewMaterializedView,
    MetricViewMeasure,
    MetricViewModel,
    MetricViewRely,
    MetricViewWindow,
)

# --- Hypothesis Strategies ---

_identifier = st.from_regex(r"[a-z][a-z0-9_]{0,29}", fullmatch=True)
_three_part_name = st.builds(
    lambda a, b, c: f"{a}.{b}.{c}",
    _identifier,
    _identifier,
    _identifier,
)
_expr = st.from_regex(r"[A-Za-z_][A-Za-z0-9_. ()]{0,49}", fullmatch=True)
_safe_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=50,
)

# Adversarial strings for metadata fields (synonyms, comments, display_name)
# These test JSON serialization boundaries in custom extensions and YAML-unsafe values
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
def metric_view_fields_strategy(draw):
    """Generate valid MetricViewField instances with adversarial metadata."""
    return MetricViewField(
        name=draw(_identifier),
        expr=draw(_expr),
        comment=draw(st.none() | _adversarial_text),
        display_name=draw(st.none() | _adversarial_text),
        synonyms=draw(st.none() | st.lists(_adversarial_text, min_size=1, max_size=3)),
        format=draw(st.none() | st.just(MetricViewFormat(type="number"))),
    )


@st.composite
def metric_view_measures_strategy(draw):
    """Generate valid MetricViewMeasure instances with adversarial metadata."""
    return MetricViewMeasure(
        name=draw(_identifier),
        expr=draw(_expr),
        comment=draw(st.none() | _adversarial_text),
        display_name=draw(st.none() | _adversarial_text),
        synonyms=draw(st.none() | st.lists(_adversarial_text, min_size=1, max_size=3)),
        format=draw(st.none() | st.just(MetricViewFormat(type="currency", currency_code="USD"))),
        window=draw(st.none() | st.just([MetricViewWindow(order="d_date", range="trailing 7 day")])),
    )


@st.composite
def metric_view_models_with_fields_and_measures(draw):
    """Generate models that always have at least one field and one measure."""
    return MetricViewModel(
        version="1.1",
        source=draw(_three_part_name),
        fields=draw(st.lists(metric_view_fields_strategy(), min_size=1, max_size=5)),
        measures=draw(st.lists(metric_view_measures_strategy(), min_size=1, max_size=5)),
    )


# --- Property 2: Import Maps Fields and Measures Correctly ---


class TestImportFieldAndMeasureMapping:
    """Property 2: Import maps fields and measures correctly.

    For any valid Metric View definition containing fields and measures,
    importing to OSI SHALL produce one OSI field per Metric View field
    (each with a DATABRICKS dialect expression and dimension metadata)
    and one OSI metric per Metric View measure (each with a DATABRICKS
    dialect expression).
    """

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_one_osi_field_per_metric_view_field(self, model: MetricViewModel):
        """Each Metric View field maps to exactly one OSI field."""
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        assert dataset.fields is not None
        assert len(dataset.fields) == len(model.fields)

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_each_field_has_databricks_dialect(self, model: MetricViewModel):
        """Every OSI field has at least a DATABRICKS dialect expression."""
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        for osi_field in dataset.fields:
            dialect_names = [d.dialect for d in osi_field.expression.dialects]
            assert OSIDialect.DATABRICKS in dialect_names

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_each_field_has_dimension_metadata(self, model: MetricViewModel):
        """Every OSI field has dimension metadata set."""
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        for osi_field in dataset.fields:
            assert osi_field.dimension is not None
            assert isinstance(osi_field.dimension.is_time, bool)

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_field_names_preserved(self, model: MetricViewModel):
        """Field names are preserved during import."""
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        mv_names = [f.name for f in model.fields]
        osi_names = [f.name for f in dataset.fields]
        assert mv_names == osi_names

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_field_expressions_stored_as_databricks(self, model: MetricViewModel):
        """Field expressions are stored in the DATABRICKS dialect."""
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        for mv_field, osi_field in zip(model.fields, dataset.fields):
            databricks_exprs = [
                d.expression for d in osi_field.expression.dialects
                if d.dialect == OSIDialect.DATABRICKS
            ]
            assert len(databricks_exprs) == 1
            assert databricks_exprs[0] == mv_field.expr

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_one_osi_metric_per_metric_view_measure(self, model: MetricViewModel):
        """Each Metric View measure maps to exactly one OSI metric."""
        doc = metric_view_to_osi(model)
        metrics = doc.semantic_model[0].metrics
        assert metrics is not None
        assert len(metrics) == len(model.measures)

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_each_metric_has_databricks_dialect(self, model: MetricViewModel):
        """Every OSI metric has at least a DATABRICKS dialect expression."""
        doc = metric_view_to_osi(model)
        for metric in doc.semantic_model[0].metrics:
            dialect_names = [d.dialect for d in metric.expression.dialects]
            assert OSIDialect.DATABRICKS in dialect_names

    @given(model=metric_view_models_with_fields_and_measures())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_measure_names_and_expressions_preserved(self, model: MetricViewModel):
        """Measure names and expressions are preserved during import."""
        doc = metric_view_to_osi(model)
        metrics = doc.semantic_model[0].metrics
        for mv_measure, osi_metric in zip(model.measures, metrics):
            assert osi_metric.name == mv_measure.name
            databricks_exprs = [
                d.expression for d in osi_metric.expression.dialects
                if d.dialect == OSIDialect.DATABRICKS
            ]
            assert databricks_exprs[0] == mv_measure.expr


# --- Property 3: Import Maps Source Correctly ---


class TestImportSourceMapping:
    """Property 3: Import maps source correctly.

    For any Metric View source string, if it matches a three-part name pattern
    (X.Y.Z) the importer SHALL map it directly to the OSI dataset source field;
    if it is a SQL query the importer SHALL store it in a DATABRICKS
    custom_extension with key 'source_query'.
    """

    @given(source=_three_part_name)
    @settings(max_examples=100)
    def test_three_part_name_maps_to_source(self, source: str):
        """Three-part name source maps directly to dataset.source."""
        model = MetricViewModel(
            source=source,
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        assert dataset.source == source

    @given(source=_three_part_name)
    @settings(max_examples=100)
    def test_three_part_name_no_source_query_extension(self, source: str):
        """Three-part name source does not generate a source_query custom extension."""
        model = MetricViewModel(
            source=source,
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        if dataset.custom_extensions:
            for ext in dataset.custom_extensions:
                data = json.loads(ext.data)
                assert "source_query" not in data

    @given(
        table=_identifier,
        cols=st.lists(_identifier, min_size=1, max_size=3),
    )
    @settings(max_examples=100)
    def test_sql_query_stored_in_custom_extension(self, table: str, cols: list[str]):
        """SQL query source is stored in a DATABRICKS custom extension."""
        query = f"SELECT {', '.join(cols)} FROM {table}"
        model = MetricViewModel(
            source=query,
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]

        # Should have custom extension with source_query
        assert dataset.custom_extensions is not None
        ext_data_list = [json.loads(e.data) for e in dataset.custom_extensions]
        source_queries = [d["source_query"] for d in ext_data_list if "source_query" in d]
        assert len(source_queries) == 1
        assert source_queries[0] == query

    @given(
        table=_identifier,
        cols=st.lists(_identifier, min_size=1, max_size=3),
    )
    @settings(max_examples=100)
    def test_sql_query_dataset_source_is_generic(self, table: str, cols: list[str]):
        """When source is a SQL query, dataset.source is a generic name (not the query)."""
        query = f"SELECT {', '.join(cols)} FROM {table}"
        model = MetricViewModel(
            source=query,
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        dataset = doc.semantic_model[0].datasets[0]
        # Source should not be the SQL query itself
        assert not dataset.source.upper().startswith("SELECT")


# --- Property 4: Import Preserves Semantic Metadata ---


class TestImportSemanticMetadata:
    """Property 4: Import preserves semantic metadata.

    For any Metric View field or measure with a display_name, synonyms, or comment,
    the importer SHALL map display_name as the first element of ai_context.synonyms,
    append synonyms to the same list, and map comment to the OSI description field.
    """

    @given(
        display_name=_adversarial_text,
        synonyms=st.lists(_adversarial_text, min_size=1, max_size=3),
        comment=_adversarial_text,
    )
    @settings(max_examples=100)
    def test_field_display_name_is_first_synonym(
        self, display_name: str, synonyms: list[str], comment: str
    ):
        """display_name becomes the first element of ai_context.synonyms."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[
                MetricViewField(
                    name="f1",
                    expr="col1",
                    display_name=display_name,
                    synonyms=synonyms,
                    comment=comment,
                )
            ],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.ai_context is not None
        assert field.ai_context.synonyms[0] == display_name

    @given(
        display_name=_adversarial_text,
        synonyms=st.lists(_adversarial_text, min_size=1, max_size=3),
    )
    @settings(max_examples=100)
    def test_field_synonyms_appended_after_display_name(
        self, display_name: str, synonyms: list[str]
    ):
        """synonyms list is appended after display_name in ai_context.synonyms."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[
                MetricViewField(
                    name="f1",
                    expr="col1",
                    display_name=display_name,
                    synonyms=synonyms,
                )
            ],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        expected = tuple([display_name] + synonyms)
        assert field.ai_context.synonyms == expected

    @given(comment=_adversarial_text)
    @settings(max_examples=100)
    def test_field_comment_maps_to_description(self, comment: str):
        """comment maps to the OSI field description."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", comment=comment)],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.description == comment

    @given(
        display_name=_adversarial_text,
        synonyms=st.lists(_adversarial_text, min_size=1, max_size=3),
        comment=_adversarial_text,
    )
    @settings(max_examples=100)
    def test_measure_metadata_preserved(
        self, display_name: str, synonyms: list[str], comment: str
    ):
        """Measure display_name, synonyms, and comment are preserved in OSI metric."""
        model = MetricViewModel(
            source="a.b.c",
            measures=[
                MetricViewMeasure(
                    name="m1",
                    expr="SUM(col1)",
                    display_name=display_name,
                    synonyms=synonyms,
                    comment=comment,
                )
            ],
        )
        doc = metric_view_to_osi(model)
        metric = doc.semantic_model[0].metrics[0]
        assert metric.description == comment
        assert metric.ai_context is not None
        assert metric.ai_context.synonyms[0] == display_name
        expected_synonyms = tuple([display_name] + synonyms)
        assert metric.ai_context.synonyms == expected_synonyms

    @given(synonyms=st.lists(_adversarial_text, min_size=1, max_size=5))
    @settings(max_examples=100)
    def test_field_synonyms_only_no_display_name(self, synonyms: list[str]):
        """When only synonyms are present (no display_name), they map directly."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[
                MetricViewField(
                    name="f1",
                    expr="col1",
                    synonyms=synonyms,
                )
            ],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.ai_context is not None
        assert field.ai_context.synonyms == tuple(synonyms)

    def test_no_metadata_produces_no_ai_context(self):
        """When no display_name, synonyms, or comment, ai_context and description are None."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.ai_context is None
        assert field.description is None


# --- Unit Tests ---


class TestImportComplete:
    """Unit tests for complete Metric View import scenarios."""

    def test_import_complete_metric_view(self):
        """Test import of a complete Metric View YAML (all sections)."""
        model = MetricViewModel(
            version="1.1",
            source="catalog.schema.store_sales",
            comment="Store sales metric view",
            filter="ss_quantity > 0",
            joins=[
                MetricViewJoin(
                    name="date_dim",
                    source="catalog.schema.date_dim",
                    on="source.ss_sold_date_sk = date_dim.d_date_sk",
                    cardinality="many_to_one",
                    rely=MetricViewRely(at_most_one_match=True),
                )
            ],
            fields=[
                MetricViewField(
                    name="sold_date",
                    expr="date_dim.d_date",
                    comment="Sale date",
                    display_name="Date of Sale",
                    synonyms=["sale date", "transaction date"],
                ),
                MetricViewField(name="store_id", expr="ss_store_sk"),
            ],
            measures=[
                MetricViewMeasure(
                    name="total_sales",
                    expr="SUM(ss_net_paid)",
                    comment="Total net sales",
                    display_name="Total Sales",
                )
            ],
            materialization=MetricViewMaterialization(
                schedule="every 6 hours",
                mode="relaxed",
                materialized_views=[
                    MetricViewMaterializedView(
                        name="mv_daily",
                        type="aggregated",
                        dimensions=["sold_date"],
                        measures=["total_sales"],
                    )
                ],
            ),
        )

        doc = metric_view_to_osi(model, model_name="store_sales_model")

        # Check document-level
        assert doc.version == "0.2.0.dev0"
        assert OSIDialect.DATABRICKS in doc.dialects
        assert OSIVendor.DATABRICKS in doc.vendors

        # Check semantic model
        sm = doc.semantic_model[0]
        assert sm.name == "store_sales_model"
        assert sm.description == "Store sales metric view"

        # Check dataset
        ds = sm.datasets[0]
        assert ds.name == "store_sales"
        assert ds.source == "catalog.schema.store_sales"
        assert len(ds.fields) == 2

        # Check field mapping
        sold_date_field = ds.fields[0]
        assert sold_date_field.name == "sold_date"
        assert sold_date_field.description == "Sale date"
        assert sold_date_field.dimension.is_time is True  # "d_date" contains DATE
        assert sold_date_field.ai_context.synonyms == ("Date of Sale", "sale date", "transaction date")

        store_id_field = ds.fields[1]
        assert store_id_field.name == "store_id"
        assert store_id_field.dimension.is_time is False
        assert store_id_field.ai_context is None

        # Check filter in custom extension
        assert ds.custom_extensions is not None
        ds_ext = json.loads(ds.custom_extensions[0].data)
        assert ds_ext["filter"] == "ss_quantity > 0"

        # Check metrics
        assert len(sm.metrics) == 1
        metric = sm.metrics[0]
        assert metric.name == "total_sales"
        assert metric.description == "Total net sales"
        databricks_expr = [d for d in metric.expression.dialects if d.dialect == OSIDialect.DATABRICKS]
        assert databricks_expr[0].expression == "SUM(ss_net_paid)"

        # Check relationships
        assert len(sm.relationships) == 1
        rel = sm.relationships[0]
        assert rel.name == "date_dim"
        assert rel.from_dataset == "store_sales"
        assert rel.to == "date_dim"
        assert rel.from_columns == ["ss_sold_date_sk"]
        assert rel.to_columns == ["d_date_sk"]

        # Check relationship custom extensions (cardinality, rely)
        rel_ext = json.loads(rel.custom_extensions[0].data)
        assert rel_ext["cardinality"] == "many_to_one"
        assert rel_ext["rely"]["at_most_one_match"] is True

        # Check materialization custom extension
        assert sm.custom_extensions is not None
        mat_ext = json.loads(sm.custom_extensions[0].data)
        assert "materialization" in mat_ext
        assert mat_ext["materialization"]["schedule"] == "every 6 hours"


class TestImportSourceDetection:
    """Unit tests for source detection and mapping."""

    def test_three_part_name_maps_to_source(self):
        """Three-part name source maps to dataset.source directly."""
        model = MetricViewModel(
            source="my_catalog.my_schema.my_table",
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        ds = doc.semantic_model[0].datasets[0]
        assert ds.source == "my_catalog.my_schema.my_table"
        assert ds.name == "my_table"

    def test_sql_query_stored_in_extension(self):
        """SQL query source stored in custom_extension."""
        query = "SELECT a, b FROM my_table WHERE active = true"
        model = MetricViewModel(
            source=query,
            fields=[MetricViewField(name="f1", expr="a")],
        )
        doc = metric_view_to_osi(model)
        ds = doc.semantic_model[0].datasets[0]
        assert ds.name == "source"
        assert ds.source == "source"
        ext_data = json.loads(ds.custom_extensions[0].data)
        assert ext_data["source_query"] == query

    def test_with_clause_detected_as_query(self):
        """WITH clause is detected as SQL query."""
        query = "WITH cte AS (SELECT 1) SELECT * FROM cte"
        model = MetricViewModel(
            source=query,
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        ds = doc.semantic_model[0].datasets[0]
        ext_data = json.loads(ds.custom_extensions[0].data)
        assert ext_data["source_query"] == query


class TestImportTimeDimension:
    """Unit tests for time dimension inference."""

    def test_date_expression_inferred_as_time(self):
        """Expression containing 'date' is inferred as time dimension."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="sale_date", expr="d_date")],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.dimension.is_time is True

    def test_timestamp_expression_inferred_as_time(self):
        """Expression containing 'timestamp' is inferred as time dimension."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="ts", expr="event_timestamp")],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.dimension.is_time is True

    def test_date_trunc_inferred_as_time(self):
        """DATE_TRUNC function is inferred as time dimension."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="month", expr="DATE_TRUNC('month', d_date)")],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.dimension.is_time is True

    def test_plain_column_not_time(self):
        """Simple column reference without time patterns is not a time dimension."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="store_id", expr="ss_store_sk")],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.dimension.is_time is False


class TestImportJoins:
    """Unit tests for join mapping."""

    def test_on_clause_single_key(self):
        """Single-key ON clause parsed into from_columns/to_columns."""
        model = MetricViewModel(
            source="cat.sch.fact_sales",
            joins=[
                MetricViewJoin(
                    name="date_dim",
                    source="cat.sch.date_dim",
                    on="source.date_sk = date_dim.d_date_sk",
                )
            ],
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        rel = doc.semantic_model[0].relationships[0]
        assert rel.from_columns == ["date_sk"]
        assert rel.to_columns == ["d_date_sk"]

    def test_on_clause_composite_keys(self):
        """Composite ON clause (AND) parsed correctly."""
        model = MetricViewModel(
            source="cat.sch.orders",
            joins=[
                MetricViewJoin(
                    name="items",
                    source="cat.sch.order_items",
                    on="source.order_id = items.order_id AND source.item_id = items.item_id",
                )
            ],
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        rel = doc.semantic_model[0].relationships[0]
        assert rel.from_columns == ["order_id", "item_id"]
        assert rel.to_columns == ["order_id", "item_id"]

    def test_using_clause_mapping(self):
        """USING clause maps to same from_columns and to_columns."""
        model = MetricViewModel(
            source="cat.sch.fact_sales",
            joins=[
                MetricViewJoin(
                    name="date_dim",
                    source="cat.sch.date_dim",
                    using=["date_sk", "store_sk"],
                )
            ],
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        rel = doc.semantic_model[0].relationships[0]
        assert rel.from_columns == ["date_sk", "store_sk"]
        assert rel.to_columns == ["date_sk", "store_sk"]

    def test_nested_joins_produce_multiple_relationships(self):
        """Nested joins (snowflake schema) produce one relationship per join."""
        model = MetricViewModel(
            source="cat.sch.fact_sales",
            joins=[
                MetricViewJoin(
                    name="date_dim",
                    source="cat.sch.date_dim",
                    on="source.date_sk = date_dim.d_date_sk",
                    joins=[
                        MetricViewJoin(
                            name="fiscal_cal",
                            source="cat.sch.fiscal_calendar",
                            on="date_dim.cal_id = fiscal_cal.id",
                        )
                    ],
                )
            ],
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        rels = doc.semantic_model[0].relationships
        assert len(rels) == 2
        # First relationship: fact_sales -> date_dim
        assert rels[0].name == "date_dim"
        assert rels[0].from_dataset == "fact_sales"
        assert rels[0].to == "date_dim"
        # Second relationship: date_dim -> fiscal_calendar
        assert rels[1].name == "fiscal_cal"
        assert rels[1].from_dataset == "date_dim"
        assert rels[1].to == "fiscal_calendar"


class TestImportDialectGeneration:
    """Unit tests for ANSI_SQL dialect generation."""

    def test_standard_sql_generates_both_dialects(self):
        """Standard SQL expression generates both DATABRICKS and ANSI_SQL dialects."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="SUM(amount)")],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        dialect_names = [d.dialect for d in field.expression.dialects]
        assert OSIDialect.DATABRICKS in dialect_names
        assert OSIDialect.ANSI_SQL in dialect_names

    def test_databricks_specific_generates_only_databricks(self):
        """Databricks-specific expression generates only DATABRICKS dialect."""
        model = MetricViewModel(
            source="a.b.c",
            measures=[MetricViewMeasure(name="m1", expr="COUNT(*) FILTER (WHERE active)")],
        )
        doc = metric_view_to_osi(model)
        metric = doc.semantic_model[0].metrics[0]
        dialect_names = [d.dialect for d in metric.expression.dialects]
        assert OSIDialect.DATABRICKS in dialect_names
        assert OSIDialect.ANSI_SQL not in dialect_names

    def test_document_dialects_include_ansi_when_present(self):
        """Document-level dialects includes ANSI_SQL when at least one expr is standard."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        assert OSIDialect.ANSI_SQL in doc.dialects

    def test_document_dialects_no_ansi_when_all_databricks_specific(self):
        """Document-level dialects excludes ANSI_SQL when all exprs are Databricks-specific."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1::INT")],
            measures=[MetricViewMeasure(name="m1", expr="MEASURE(total)")],
        )
        doc = metric_view_to_osi(model)
        assert OSIDialect.ANSI_SQL not in doc.dialects


class TestImportCustomExtensions:
    """Unit tests for custom extension handling."""

    def test_filter_stored_in_dataset_extension(self):
        """Filter is stored in dataset custom extension."""
        model = MetricViewModel(
            source="a.b.c",
            filter="status = 'active'",
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        doc = metric_view_to_osi(model)
        ds = doc.semantic_model[0].datasets[0]
        assert ds.custom_extensions is not None
        ext_data = json.loads(ds.custom_extensions[0].data)
        assert ext_data["filter"] == "status = 'active'"

    def test_materialization_stored_in_model_extension(self):
        """Materialization is stored in semantic model custom extension."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1")],
            materialization=MetricViewMaterialization(
                schedule="every 6 hours",
                mode="relaxed",
            ),
        )
        doc = metric_view_to_osi(model)
        sm = doc.semantic_model[0]
        assert sm.custom_extensions is not None
        ext_data = json.loads(sm.custom_extensions[0].data)
        assert ext_data["materialization"]["schedule"] == "every 6 hours"
        assert ext_data["materialization"]["mode"] == "relaxed"

    def test_field_format_stored_in_field_extension(self):
        """Field format is stored in field custom extension."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[
                MetricViewField(
                    name="f1",
                    expr="col1",
                    format=MetricViewFormat(type="currency", currency_code="USD"),
                )
            ],
        )
        doc = metric_view_to_osi(model)
        field = doc.semantic_model[0].datasets[0].fields[0]
        assert field.custom_extensions is not None
        ext_data = json.loads(field.custom_extensions[0].data)
        assert ext_data["format"]["type"] == "currency"
        assert ext_data["format"]["currency_code"] == "USD"

    def test_measure_window_stored_in_metric_extension(self):
        """Measure window is stored in metric custom extension."""
        model = MetricViewModel(
            source="a.b.c",
            measures=[
                MetricViewMeasure(
                    name="m1",
                    expr="SUM(amount)",
                    window=[MetricViewWindow(order="d_date", range="trailing 7 day")],
                )
            ],
        )
        doc = metric_view_to_osi(model)
        metric = doc.semantic_model[0].metrics[0]
        assert metric.custom_extensions is not None
        ext_data = json.loads(metric.custom_extensions[0].data)
        assert ext_data["window"][0]["order"] == "d_date"
        assert ext_data["window"][0]["range"] == "trailing 7 day"
