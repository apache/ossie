"""Integration round-trip tests using TPC-DS fixtures.

These tests verify that converting between Metric View YAML and OSI YAML
preserves all essential information when using realistic, fixture-based inputs.

Test coverage:
- MV→OSI→MV: Import the TPC-DS Metric View fixture to OSI, export back,
  and verify fields, measures, joins, filter, and metadata are preserved.
- OSI→MV→OSI: Export the TPC-DS OSI fixture to Metric View, import back,
  and verify datasets, fields, metrics, relationships, and metadata are preserved.
"""

from __future__ import annotations

import json

from osi.models import OSIDialect, OSIDocument, OSIVendor

from osi_databricks.metric_view_to_osi import metric_view_to_osi
from osi_databricks.models import MetricViewModel
from osi_databricks.osi_to_metric_view import osi_to_metric_view


class TestMVtoOSItoMVFixtureRoundTrip:
    """MV→OSI→MV round-trip with the TPC-DS Metric View fixture.

    Verifies that importing to OSI and exporting back preserves all fields,
    measures, joins, filter, and semantic metadata.
    """

    def test_source_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Three-part source name survives the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        assert len(results) == 1
        _, mv_out = results[0]
        assert mv_out.source == "tpcds.analytics.store_sales"

    def test_all_field_names_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """All 5 field names are preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        expected_names = ["sold_date", "sold_year", "item_id", "item_category", "store_id"]
        actual_names = [f.name for f in mv_out.fields]
        assert actual_names == expected_names

    def test_all_field_expressions_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """All field expressions are preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        expected_exprs = [
            "DATE_TRUNC('DAY', d_date)",
            "YEAR(d_date)",
            "i_item_id",
            "i_category",
            "ss_store_sk",
        ]
        actual_exprs = [f.expr for f in mv_out.fields]
        assert actual_exprs == expected_exprs

    def test_all_measure_names_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """All 5 measure names are preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        expected_names = [
            "total_sales",
            "total_quantity",
            "avg_discount",
            "net_profit",
            "trailing_7d_sales",
        ]
        actual_names = [m.name for m in mv_out.measures]
        assert actual_names == expected_names

    def test_all_measure_expressions_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """All measure expressions are preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        expected_exprs = [
            "SUM(ss_sales_price)",
            "SUM(ss_quantity)",
            "AVG(ss_ext_discount_amt)",
            "SUM(ss_net_profit)",
            "SUM(ss_sales_price)",
        ]
        actual_exprs = [m.expr for m in mv_out.measures]
        assert actual_exprs == expected_exprs

    def test_join_names_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Both join names are preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        assert mv_out.joins is not None
        expected_names = ["date_dim", "item"]
        actual_names = [j.name for j in mv_out.joins]
        assert actual_names == expected_names

    def test_join_sources_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Join target datasets are preserved (derived from source three-part name)."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        # The exporter stores relationship.to as the join source, which is the
        # table name extracted from the original three-part name
        assert mv_out.joins is not None
        actual_sources = [j.source for j in mv_out.joins]
        assert actual_sources == ["date_dim", "item"]

    def test_filter_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Filter expression is preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.filter == "ss_net_profit > 0"

    def test_comment_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Top-level comment is preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.comment == "Store sales fact table with date and item dimensions from TPC-DS benchmark"

    def test_field_synonyms_and_display_name_preserved(
        self, metric_view_tpcds_model: MetricViewModel
    ):
        """Field display_name and synonyms content is preserved through the round-trip.

        The round-trip merges display_name + synonyms into a single list in OSI,
        then splits the first element back to display_name on export.
        """
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        # sold_date: display_name="Sale Date", synonyms=["transaction date", "order date"]
        sold_date = mv_out.fields[0]
        assert sold_date.display_name == "Sale Date"
        assert sold_date.synonyms == ["transaction date", "order date"]

        # item_category: display_name="Category", synonyms=["product category", "item type"]
        item_category = mv_out.fields[3]
        assert item_category.display_name == "Category"
        assert item_category.synonyms == ["product category", "item type"]

    def test_measure_synonyms_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Measure display_name and synonyms are preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        # total_sales: display_name="Total Sales", synonyms=["revenue", "gross sales"]
        total_sales = mv_out.measures[0]
        assert total_sales.display_name == "Total Sales"
        assert total_sales.synonyms == ["revenue", "gross sales"]

        # net_profit: display_name="Net Profit", synonyms=["profit", "margin"]
        net_profit = mv_out.measures[3]
        assert net_profit.display_name == "Net Profit"
        assert net_profit.synonyms == ["profit", "margin"]

    def test_materialization_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Materialization config survives the round-trip via custom extensions."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        assert mv_out.materialization is not None
        assert mv_out.materialization.schedule == "every 6 hours"
        assert mv_out.materialization.mode == "relaxed"
        assert mv_out.materialization.materialized_views is not None
        assert len(mv_out.materialization.materialized_views) == 1
        mat_view = mv_out.materialization.materialized_views[0]
        assert mat_view.name == "sales_by_date"
        assert mat_view.type == "aggregated"
        assert mat_view.dimensions == ["sold_date", "item_category"]
        assert mat_view.measures == ["total_sales", "total_quantity"]

    def test_window_measure_preserved(self, metric_view_tpcds_model: MetricViewModel):
        """Window specification on trailing_7d_sales measure is preserved."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        trailing = mv_out.measures[4]
        assert trailing.name == "trailing_7d_sales"
        assert trailing.window is not None
        assert len(trailing.window) == 1
        assert trailing.window[0].order == "sold_date"
        assert trailing.window[0].range == "trailing 7 day"

    def test_format_preserved_on_fields(self, metric_view_tpcds_model: MetricViewModel):
        """Field format metadata is preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        # sold_date has format: type=date, date_format=year_month_day
        sold_date = mv_out.fields[0]
        assert sold_date.format is not None
        assert sold_date.format.type == "date"
        assert sold_date.format.date_format == "year_month_day"

    def test_format_preserved_on_measures(self, metric_view_tpcds_model: MetricViewModel):
        """Measure format metadata is preserved through the round-trip."""
        osi_doc = metric_view_to_osi(metric_view_tpcds_model, model_name="tpcds_store_sales")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        # total_sales has format: type=currency, currency_code=USD
        total_sales = mv_out.measures[0]
        assert total_sales.format is not None
        assert total_sales.format.type == "currency"
        assert total_sales.format.currency_code == "USD"


class TestOSItoMVtoOSIFixtureRoundTrip:
    """OSI→MV→OSI round-trip with the TPC-DS OSI fixture.

    Verifies that exporting to Metric View and importing back preserves
    datasets, fields, metrics, relationships, and metadata.
    """

    def test_dataset_name_preserved(self, osi_tpcds_document: OSIDocument):
        """Dataset name is preserved through OSI→MV→OSI."""
        results = osi_to_metric_view(osi_tpcds_document)
        assert len(results) == 1
        dataset_name, mv_model = results[0]
        assert dataset_name == "store_sales"

        # Import back
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        ds_back = osi_back.semantic_model[0].datasets[0]
        assert ds_back.name == "store_sales"

    def test_field_names_preserved(self, osi_tpcds_document: OSIDocument):
        """All field names are preserved through OSI→MV→OSI."""
        original_ds = osi_tpcds_document.semantic_model[0].datasets[0]
        original_names = [f.name for f in original_ds.fields]

        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        ds_back = osi_back.semantic_model[0].datasets[0]

        roundtrip_names = [f.name for f in ds_back.fields]
        assert roundtrip_names == original_names

    def test_field_databricks_expressions_preserved(self, osi_tpcds_document: OSIDocument):
        """Field DATABRICKS dialect expressions are preserved through OSI→MV→OSI."""
        original_ds = osi_tpcds_document.semantic_model[0].datasets[0]
        original_exprs = []
        for f in original_ds.fields:
            for d in f.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    original_exprs.append(d.expression)

        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        ds_back = osi_back.semantic_model[0].datasets[0]

        roundtrip_exprs = []
        for f in ds_back.fields:
            for d in f.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    roundtrip_exprs.append(d.expression)

        assert roundtrip_exprs == original_exprs

    def test_metric_names_preserved(self, osi_tpcds_document: OSIDocument):
        """All metric names are preserved through OSI→MV→OSI."""
        original_metrics = osi_tpcds_document.semantic_model[0].metrics
        original_names = [m.name for m in original_metrics]

        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        back_metrics = osi_back.semantic_model[0].metrics

        assert back_metrics is not None
        roundtrip_names = [m.name for m in back_metrics]
        assert roundtrip_names == original_names

    def test_metric_databricks_expressions_preserved(self, osi_tpcds_document: OSIDocument):
        """Metric DATABRICKS dialect expressions are preserved through OSI→MV→OSI."""
        original_metrics = osi_tpcds_document.semantic_model[0].metrics
        original_exprs = []
        for m in original_metrics:
            for d in m.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    original_exprs.append(d.expression)

        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        back_metrics = osi_back.semantic_model[0].metrics

        roundtrip_exprs = []
        for m in back_metrics:
            for d in m.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    roundtrip_exprs.append(d.expression)

        assert roundtrip_exprs == original_exprs

    def test_relationship_names_preserved(self, osi_tpcds_document: OSIDocument):
        """Relationship names are preserved through OSI→MV→OSI."""
        original_rels = osi_tpcds_document.semantic_model[0].relationships
        original_names = [r.name for r in original_rels]

        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        back_rels = osi_back.semantic_model[0].relationships

        assert back_rels is not None
        roundtrip_names = [r.name for r in back_rels]
        assert roundtrip_names == original_names

    def test_ai_context_synonyms_preserved(self, osi_tpcds_document: OSIDocument):
        """ai_context.synonyms on fields are preserved through OSI→MV→OSI."""
        original_ds = osi_tpcds_document.semantic_model[0].datasets[0]

        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        ds_back = osi_back.semantic_model[0].datasets[0]

        for orig_field, rt_field in zip(original_ds.fields, ds_back.fields):
            if orig_field.ai_context and hasattr(orig_field.ai_context, "synonyms"):
                if orig_field.ai_context.synonyms:
                    assert rt_field.ai_context is not None
                    assert rt_field.ai_context.synonyms == orig_field.ai_context.synonyms

    def test_filter_extension_preserved(self, osi_tpcds_document: OSIDocument):
        """DATABRICKS filter custom extension is preserved through OSI→MV→OSI."""
        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]

        # Verify filter was extracted
        assert mv_model.filter == "ss_net_profit > 0"

        # Import back and verify extension is reconstructed
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        ds_back = osi_back.semantic_model[0].datasets[0]
        assert ds_back.custom_extensions is not None

        # Find the DATABRICKS extension with filter
        filter_found = False
        for ext in ds_back.custom_extensions:
            if ext.vendor_name == OSIVendor.DATABRICKS:
                data = json.loads(ext.data)
                if "filter" in data:
                    assert data["filter"] == "ss_net_profit > 0"
                    filter_found = True
        assert filter_found

    def test_materialization_extension_preserved(self, osi_tpcds_document: OSIDocument):
        """DATABRICKS materialization custom extension is preserved through OSI→MV→OSI."""
        results = osi_to_metric_view(osi_tpcds_document)
        _, mv_model = results[0]

        # Verify materialization was extracted
        assert mv_model.materialization is not None
        assert mv_model.materialization.schedule == "every 6 hours"

        # Import back and verify extension is reconstructed
        osi_back = metric_view_to_osi(mv_model, model_name="tpcds_store_sales")
        sm_back = osi_back.semantic_model[0]
        assert sm_back.custom_extensions is not None

        mat_found = False
        for ext in sm_back.custom_extensions:
            if ext.vendor_name == OSIVendor.DATABRICKS:
                data = json.loads(ext.data)
                if "materialization" in data:
                    mat = data["materialization"]
                    assert mat["schedule"] == "every 6 hours"
                    assert mat["mode"] == "relaxed"
                    mat_found = True
        assert mat_found
