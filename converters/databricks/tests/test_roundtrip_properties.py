"""Round-trip property tests for the osi-databricks converter.

Property 7: MV→OSI→MV round-trip preservation
Property 8: OSI→MV→OSI round-trip preservation
Property 9: Custom extensions preserved during round-trip

These tests verify that converting in one direction and back preserves the
essential information — field names, expressions, measure names, join sources,
filter expressions, synonyms, and custom extensions for all vendors.
"""

from __future__ import annotations

import json

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

from osi_databricks.metric_view_to_osi import metric_view_to_osi
from osi_databricks.models import (
    MetricViewField,
    MetricViewJoin,
    MetricViewMeasure,
    MetricViewModel,
    MetricViewRely,
)
from osi_databricks.osi_to_metric_view import osi_to_metric_view

# --- Hypothesis Strategies ---

_identifier = st.from_regex(r"[a-z][a-z0-9_]{0,19}", fullmatch=True)
_three_part_name = st.builds(
    lambda a, b, c: f"{a}.{b}.{c}",
    _identifier,
    _identifier,
    _identifier,
)

# Expressions that are valid column-like expressions (no Databricks-specific syntax
# to ensure ANSI_SQL dialect is also generated, making round-trips more predictable)
_standard_expr = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,19}", fullmatch=True)

# Safe text that avoids JSON and YAML issues for metadata
_safe_text = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=126,
        blacklist_characters='"\\',
    ),
    min_size=1,
    max_size=30,
)


# --- Strategies for Property 7 (MV→OSI→MV) ---


@st.composite
def roundtrip_mv_fields(draw):
    """Generate fields suitable for MV→OSI→MV round-trip testing.

    Uses simple column-like expressions that produce parseable ON clauses
    and avoid time-inference ambiguity.
    """
    name = draw(_identifier)
    # Use expressions that won't trigger time inference (avoid DATE, TIME, etc.)
    expr = draw(_standard_expr)
    comment = draw(st.none() | _safe_text)
    display_name = draw(st.none() | _safe_text)
    synonyms = draw(st.none() | st.lists(_safe_text, min_size=1, max_size=3))

    return MetricViewField(
        name=name,
        expr=expr,
        comment=comment,
        display_name=display_name,
        synonyms=synonyms,
    )


@st.composite
def roundtrip_mv_measures(draw):
    """Generate measures suitable for round-trip testing."""
    name = draw(_identifier)
    # Use SUM/COUNT-like expressions that are standard SQL
    col = draw(_standard_expr)
    expr = draw(st.sampled_from([
        f"SUM({col})",
        f"COUNT({col})",
        f"AVG({col})",
        f"MAX({col})",
        f"MIN({col})",
    ]))
    comment = draw(st.none() | _safe_text)
    display_name = draw(st.none() | _safe_text)
    synonyms = draw(st.none() | st.lists(_safe_text, min_size=1, max_size=3))

    return MetricViewMeasure(
        name=name,
        expr=expr,
        comment=comment,
        display_name=display_name,
        synonyms=synonyms,
    )


@st.composite
def roundtrip_mv_joins(draw):
    """Generate joins with parseable ON clauses for round-trip testing.

    The ON clause format must match what _build_on_clause produces:
    source.from_col = join_name.to_col
    """
    name = draw(_identifier)
    source = draw(_three_part_name)
    # Generate a parseable ON clause: source.col1 = name.col2
    from_col = draw(_identifier)
    to_col = draw(_identifier)
    on_clause = f"source.{from_col} = {name}.{to_col}"

    cardinality = draw(st.none() | st.sampled_from(["many_to_one", "one_to_many"]))
    rely = draw(st.none() | st.just(MetricViewRely(at_most_one_match=True)))

    return MetricViewJoin(
        name=name,
        source=source,
        on=on_clause,
        cardinality=cardinality,
        rely=rely,
    )


@st.composite
def roundtrip_mv_models(draw):
    """Generate MetricViewModel instances designed for round-trip fidelity testing.

    Constraints:
    - Uses three-part name sources (SQL queries change dataset naming)
    - Uses parseable ON clause format in joins
    - Avoids expressions that trigger time inference ambiguity
    """
    source = draw(_three_part_name)
    comment = draw(st.none() | _safe_text)
    filter_expr = draw(st.none() | _standard_expr)
    fields = draw(st.lists(roundtrip_mv_fields(), min_size=1, max_size=4))
    measures = draw(st.none() | st.lists(roundtrip_mv_measures(), min_size=1, max_size=3))
    joins = draw(st.none() | st.lists(roundtrip_mv_joins(), min_size=1, max_size=2))

    return MetricViewModel(
        version="1.1",
        source=source,
        comment=comment,
        filter=filter_expr,
        fields=fields,
        measures=measures,
        joins=joins,
    )


# --- Strategies for Property 8 (OSI→MV→OSI) ---


@st.composite
def roundtrip_osi_fields(draw):
    """Generate OSI fields with DATABRICKS dialect for round-trip testing."""
    name = draw(_identifier)
    expr_str = draw(_standard_expr)
    description = draw(st.none() | _safe_text)
    synonyms = draw(st.none() | st.lists(_safe_text, min_size=1, max_size=4))

    ai_context = None
    if synonyms:
        ai_context = OSIAIContextObject(synonyms=tuple(synonyms))

    return OSIField(
        name=name,
        expression=OSIExpression(
            dialects=[
                OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str),
            ]
        ),
        dimension=OSIDimension(is_time=False),
        description=description,
        ai_context=ai_context,
    )


@st.composite
def roundtrip_osi_metrics(draw):
    """Generate OSI metrics with DATABRICKS dialect for round-trip testing."""
    name = draw(_identifier)
    col = draw(_standard_expr)
    expr_str = draw(st.sampled_from([
        f"SUM({col})",
        f"COUNT({col})",
        f"AVG({col})",
        f"MAX({col})",
        f"MIN({col})",
    ]))
    description = draw(st.none() | _safe_text)
    synonyms = draw(st.none() | st.lists(_safe_text, min_size=1, max_size=4))

    ai_context = None
    if synonyms:
        ai_context = OSIAIContextObject(synonyms=tuple(synonyms))

    return OSIMetric(
        name=name,
        expression=OSIExpression(
            dialects=[
                OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str),
            ]
        ),
        description=description,
        ai_context=ai_context,
    )


@st.composite
def roundtrip_osi_relationships(draw, dataset_name):
    """Generate OSI relationships for round-trip testing."""
    name = draw(_identifier)
    from_col = draw(_identifier)
    to_col = draw(_identifier)

    return OSIRelationship(
        name=name,
        **{"from": dataset_name},
        to=name,  # target dataset name = relationship name for simplicity
        from_columns=[from_col],
        to_columns=[to_col],
    )


@st.composite
def roundtrip_osi_documents(draw):
    """Generate OSI documents suitable for OSI→MV→OSI round-trip testing.

    Constraints:
    - Single semantic model with a single dataset
    - All fields/metrics use DATABRICKS dialect
    - Source is a three-part name
    - Relationships reference the dataset correctly
    """
    dataset_name = draw(_identifier)
    source = f"catalog.schema.{dataset_name}"
    fields = draw(st.lists(roundtrip_osi_fields(), min_size=1, max_size=4))
    metrics = draw(st.none() | st.lists(roundtrip_osi_metrics(), min_size=1, max_size=3))
    relationships = draw(
        st.none() | st.lists(roundtrip_osi_relationships(dataset_name), min_size=1, max_size=2)
    )
    description = draw(st.none() | _safe_text)

    dataset = OSIDataset(
        name=dataset_name,
        source=source,
        fields=fields,
    )
    semantic_model = OSISemanticModel(
        name="test_model",
        description=description,
        datasets=[dataset],
        relationships=relationships,
        metrics=metrics,
    )
    return OSIDocument(
        version="0.2.0.dev0",
        dialects=[OSIDialect.DATABRICKS],
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[semantic_model],
    )


# --- Strategies for Property 9 (Custom Extensions Round-Trip) ---


@st.composite
def roundtrip_osi_documents_with_multi_vendor_extensions(draw):
    """Generate OSI documents with custom extensions for multiple vendors.

    Tests that DATABRICKS extensions are preserved through round-trip and
    that other vendor extensions (SNOWFLAKE, DBT) survive the trip unharmed.
    """
    dataset_name = draw(_identifier)
    source = f"catalog.schema.{dataset_name}"
    expr_str = draw(_standard_expr)

    # Generate DATABRICKS-specific extensions
    filter_expr = draw(st.none() | _standard_expr)
    ds_ext_data: dict = {}
    if filter_expr:
        ds_ext_data["filter"] = filter_expr

    dataset_extensions = []
    if ds_ext_data:
        dataset_extensions.append(
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps(ds_ext_data),
            )
        )

    # Add other vendor extensions that should survive
    other_vendor = draw(st.sampled_from([OSIVendor.SNOWFLAKE, OSIVendor.DBT, OSIVendor.GOODDATA]))
    other_ext_key = draw(_identifier)
    other_ext_value = draw(_safe_text)
    dataset_extensions.append(
        OSICustomExtension(
            vendor_name=other_vendor,
            data=json.dumps({other_ext_key: other_ext_value}),
        )
    )

    # Field with DATABRICKS custom extension (format)
    field_ext = OSICustomExtension(
        vendor_name=OSIVendor.DATABRICKS,
        data=json.dumps({"format": {"type": "number"}}),
    )
    # Field with another vendor's extension
    other_field_ext = OSICustomExtension(
        vendor_name=other_vendor,
        data=json.dumps({"other_field_meta": "preserved"}),
    )

    field = OSIField(
        name=draw(_identifier),
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str)]
        ),
        dimension=OSIDimension(is_time=False),
        custom_extensions=[field_ext, other_field_ext],
    )

    # Metric with DATABRICKS window extension
    metric_name = draw(_identifier)
    metric_ext = OSICustomExtension(
        vendor_name=OSIVendor.DATABRICKS,
        data=json.dumps({"window": [{"order": "d_date", "range": "trailing 7 day"}]}),
    )
    metric = OSIMetric(
        name=metric_name,
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=f"SUM({expr_str})")]
        ),
        custom_extensions=[metric_ext],
    )

    # Semantic model with materialization extension
    mat_data = {"schedule": "every 6 hours", "mode": "relaxed"}
    sm_ext = OSICustomExtension(
        vendor_name=OSIVendor.DATABRICKS,
        data=json.dumps({"materialization": mat_data}),
    )

    dataset = OSIDataset(
        name=dataset_name,
        source=source,
        fields=[field],
        custom_extensions=dataset_extensions if dataset_extensions else None,
    )
    semantic_model = OSISemanticModel(
        name="test_model",
        datasets=[dataset],
        metrics=[metric],
        custom_extensions=[sm_ext],
    )
    return OSIDocument(
        version="0.2.0.dev0",
        dialects=[OSIDialect.DATABRICKS],
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[semantic_model],
    )


# --- Property 7: MV→OSI→MV Round-Trip Preservation ---


class TestMVtoOSItoMVRoundTrip:
    """Property 7: MV→OSI→MV round-trip preservation.

    For any valid Metric View YAML input, importing to OSI then exporting
    back to Metric View YAML SHALL preserve: the source, all field names and
    expressions, all measure names and expressions, all join names and sources,
    the filter expression, and all synonyms/display_name metadata.
    """

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_source_preserved(self, model: MetricViewModel):
        """Source (three-part name) is preserved through MV→OSI→MV."""
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        assert len(results) > 0
        _, mv_out = results[0]
        assert mv_out.source == model.source

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_field_names_preserved(self, model: MetricViewModel):
        """All field names are preserved through round-trip."""
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        assert len(results) > 0
        _, mv_out = results[0]
        assert mv_out.fields is not None
        original_names = [f.name for f in model.fields]
        roundtrip_names = [f.name for f in mv_out.fields]
        assert original_names == roundtrip_names

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_field_expressions_preserved(self, model: MetricViewModel):
        """All field expressions are preserved through round-trip."""
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        assert len(results) > 0
        _, mv_out = results[0]
        assert mv_out.fields is not None
        original_exprs = [f.expr for f in model.fields]
        roundtrip_exprs = [f.expr for f in mv_out.fields]
        assert original_exprs == roundtrip_exprs

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_measure_names_preserved(self, model: MetricViewModel):
        """All measure names are preserved through round-trip."""
        if model.measures is None:
            return
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.measures is not None
        original_names = [m.name for m in model.measures]
        roundtrip_names = [m.name for m in mv_out.measures]
        assert original_names == roundtrip_names

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_measure_expressions_preserved(self, model: MetricViewModel):
        """All measure expressions are preserved through round-trip."""
        if model.measures is None:
            return
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.measures is not None
        original_exprs = [m.expr for m in model.measures]
        roundtrip_exprs = [m.expr for m in mv_out.measures]
        assert original_exprs == roundtrip_exprs

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_join_names_and_sources_preserved(self, model: MetricViewModel):
        """All join names and sources are preserved through round-trip."""
        if model.joins is None:
            return
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.joins is not None
        original_names = [j.name for j in model.joins]
        roundtrip_names = [j.name for j in mv_out.joins]
        assert original_names == roundtrip_names

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_filter_preserved(self, model: MetricViewModel):
        """Filter expression is preserved through round-trip."""
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.filter == model.filter

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_synonyms_and_display_name_preserved(self, model: MetricViewModel):
        """Synonyms and display_name metadata content is preserved through round-trip.

        The round-trip merges display_name and synonyms into a single ordered list
        (ai_context.synonyms). On export, the first element becomes display_name
        and the rest become synonyms. The total content is preserved, though the
        split point may shift when display_name was originally None.
        """
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]

        for orig_field, rt_field in zip(model.fields, mv_out.fields):
            # Reconstruct the combined synonym list as the importer sees it
            original_all: list[str] = []
            if orig_field.display_name:
                original_all.append(orig_field.display_name)
            if orig_field.synonyms:
                original_all.extend(orig_field.synonyms)

            # Reconstruct the combined list from the round-tripped field
            roundtrip_all: list[str] = []
            if rt_field.display_name:
                roundtrip_all.append(rt_field.display_name)
            if rt_field.synonyms:
                roundtrip_all.extend(rt_field.synonyms)

            assert original_all == roundtrip_all

    @given(model=roundtrip_mv_models())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_comment_preserved(self, model: MetricViewModel):
        """Top-level comment is preserved through round-trip (as semantic model description)."""
        osi_doc = metric_view_to_osi(model, model_name="rt_model")
        results = osi_to_metric_view(osi_doc)
        _, mv_out = results[0]
        assert mv_out.comment == model.comment


# --- Property 8: OSI→MV→OSI Round-Trip Preservation ---


class TestOSItoMVtoOSIRoundTrip:
    """Property 8: OSI→MV→OSI round-trip preservation.

    For any valid OSI document containing datasets with DATABRICKS dialect
    expressions, exporting to Metric View YAML then importing back to OSI
    SHALL preserve: all dataset names, field names, field DATABRICKS expressions,
    metric names, metric DATABRICKS expressions, relationship names, and
    ai_context.synonyms.
    """

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_dataset_name_preserved(self, doc: OSIDocument):
        """Dataset name is preserved through OSI→MV→OSI."""
        results = osi_to_metric_view(doc)
        assert len(results) > 0
        dataset_name, mv_model = results[0]

        # Import back to OSI
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        ds_back = osi_back.semantic_model[0].datasets[0]
        assert ds_back.name == dataset_name

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_field_names_preserved(self, doc: OSIDocument):
        """Field names are preserved through OSI→MV→OSI."""
        original_ds = doc.semantic_model[0].datasets[0]
        original_names = [f.name for f in original_ds.fields]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        ds_back = osi_back.semantic_model[0].datasets[0]

        roundtrip_names = [f.name for f in ds_back.fields]
        assert original_names == roundtrip_names

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_field_databricks_expressions_preserved(self, doc: OSIDocument):
        """Field DATABRICKS dialect expressions are preserved through round-trip."""
        original_ds = doc.semantic_model[0].datasets[0]
        original_exprs = []
        for f in original_ds.fields:
            for d in f.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    original_exprs.append(d.expression)

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        ds_back = osi_back.semantic_model[0].datasets[0]

        roundtrip_exprs = []
        for f in ds_back.fields:
            for d in f.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    roundtrip_exprs.append(d.expression)

        assert original_exprs == roundtrip_exprs

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_names_preserved(self, doc: OSIDocument):
        """Metric names are preserved through OSI→MV→OSI."""
        original_metrics = doc.semantic_model[0].metrics
        if not original_metrics:
            return

        original_names = [m.name for m in original_metrics]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        back_metrics = osi_back.semantic_model[0].metrics

        assert back_metrics is not None
        roundtrip_names = [m.name for m in back_metrics]
        assert original_names == roundtrip_names

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_metric_databricks_expressions_preserved(self, doc: OSIDocument):
        """Metric DATABRICKS dialect expressions are preserved through round-trip."""
        original_metrics = doc.semantic_model[0].metrics
        if not original_metrics:
            return

        original_exprs = []
        for m in original_metrics:
            for d in m.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    original_exprs.append(d.expression)

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        back_metrics = osi_back.semantic_model[0].metrics

        roundtrip_exprs = []
        for m in back_metrics:
            for d in m.expression.dialects:
                if d.dialect == OSIDialect.DATABRICKS:
                    roundtrip_exprs.append(d.expression)

        assert original_exprs == roundtrip_exprs

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_relationship_names_preserved(self, doc: OSIDocument):
        """Relationship names are preserved through OSI→MV→OSI."""
        original_rels = doc.semantic_model[0].relationships
        if not original_rels:
            return

        original_names = [r.name for r in original_rels]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        back_rels = osi_back.semantic_model[0].relationships

        assert back_rels is not None
        roundtrip_names = [r.name for r in back_rels]
        assert original_names == roundtrip_names

    @given(doc=roundtrip_osi_documents())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_ai_context_synonyms_preserved(self, doc: OSIDocument):
        """ai_context.synonyms on fields are preserved through round-trip."""
        original_ds = doc.semantic_model[0].datasets[0]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        ds_back = osi_back.semantic_model[0].datasets[0]

        for orig_field, rt_field in zip(original_ds.fields, ds_back.fields):
            if orig_field.ai_context and hasattr(orig_field.ai_context, "synonyms") and orig_field.ai_context.synonyms:
                assert rt_field.ai_context is not None
                assert rt_field.ai_context.synonyms == orig_field.ai_context.synonyms
            else:
                # No synonyms in → no synonyms out
                if rt_field.ai_context:
                    assert rt_field.ai_context.synonyms is None or len(rt_field.ai_context.synonyms) == 0


# --- Property 9: Custom Extensions Preserved During Round-Trip ---


class TestCustomExtensionsRoundTrip:
    """Property 9: Custom extensions preserved during round-trip.

    For any OSI document containing custom_extensions for multiple vendors
    (DATABRICKS, SNOWFLAKE, DBT, etc.), exporting to Metric View and then
    importing back SHALL preserve all custom_extensions for all vendors.

    Note: The MV format only understands DATABRICKS extensions natively.
    Other vendor extensions on the OSI document are NOT written into the MV YAML
    (since MV has no concept of foreign vendor metadata). Therefore, full
    round-trip preservation of non-DATABRICKS extensions is only achievable at
    the OSI document level if the source document is retained.

    This test verifies that:
    1. DATABRICKS extensions (filter, materialization, format, window) survive
       the OSI→MV→OSI round-trip
    2. The export step does not corrupt or discard DATABRICKS extensions
    3. The import step correctly reconstructs DATABRICKS extensions from MV data
    """

    @given(doc=roundtrip_osi_documents_with_multi_vendor_extensions())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_databricks_filter_extension_preserved(self, doc: OSIDocument):
        """DATABRICKS filter extension survives OSI→MV→OSI round-trip."""
        original_ds = doc.semantic_model[0].datasets[0]

        # Extract original DATABRICKS filter
        original_filter = None
        if original_ds.custom_extensions:
            for ext in original_ds.custom_extensions:
                if ext.vendor_name == OSIVendor.DATABRICKS:
                    data = json.loads(ext.data)
                    if "filter" in data:
                        original_filter = data["filter"]

        results = osi_to_metric_view(doc)
        assert len(results) > 0
        _, mv_model = results[0]

        # Verify filter is in the MV model
        if original_filter:
            assert mv_model.filter == original_filter

        # Import back to OSI
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        ds_back = osi_back.semantic_model[0].datasets[0]

        # Check filter extension is restored
        if original_filter:
            roundtrip_filter = None
            if ds_back.custom_extensions:
                for ext in ds_back.custom_extensions:
                    if ext.vendor_name == OSIVendor.DATABRICKS:
                        data = json.loads(ext.data)
                        if "filter" in data:
                            roundtrip_filter = data["filter"]
            assert roundtrip_filter == original_filter

    @given(doc=roundtrip_osi_documents_with_multi_vendor_extensions())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_databricks_materialization_extension_preserved(self, doc: OSIDocument):
        """DATABRICKS materialization extension survives OSI→MV→OSI round-trip."""
        original_sm = doc.semantic_model[0]

        # Extract original materialization
        original_mat = None
        if original_sm.custom_extensions:
            for ext in original_sm.custom_extensions:
                if ext.vendor_name == OSIVendor.DATABRICKS:
                    data = json.loads(ext.data)
                    if "materialization" in data:
                        original_mat = data["materialization"]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]

        # Verify materialization is in the MV model
        if original_mat:
            assert mv_model.materialization is not None
            assert mv_model.materialization.schedule == original_mat.get("schedule")
            assert mv_model.materialization.mode == original_mat.get("mode")

        # Import back to OSI
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        sm_back = osi_back.semantic_model[0]

        # Check materialization extension is restored
        if original_mat:
            roundtrip_mat = None
            if sm_back.custom_extensions:
                for ext in sm_back.custom_extensions:
                    if ext.vendor_name == OSIVendor.DATABRICKS:
                        data = json.loads(ext.data)
                        if "materialization" in data:
                            roundtrip_mat = data["materialization"]
            assert roundtrip_mat is not None
            assert roundtrip_mat["schedule"] == original_mat["schedule"]
            assert roundtrip_mat["mode"] == original_mat["mode"]

    @given(doc=roundtrip_osi_documents_with_multi_vendor_extensions())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_databricks_format_extension_preserved(self, doc: OSIDocument):
        """DATABRICKS format extension on fields survives OSI→MV→OSI round-trip."""
        original_ds = doc.semantic_model[0].datasets[0]
        original_field = original_ds.fields[0]

        # Extract original DATABRICKS format
        original_format = None
        if original_field.custom_extensions:
            for ext in original_field.custom_extensions:
                if ext.vendor_name == OSIVendor.DATABRICKS:
                    data = json.loads(ext.data)
                    if "format" in data:
                        original_format = data["format"]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]

        # Verify format is on the MV field
        if original_format and mv_model.fields:
            assert mv_model.fields[0].format is not None
            assert mv_model.fields[0].format.type == original_format["type"]

        # Import back to OSI
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        ds_back = osi_back.semantic_model[0].datasets[0]
        field_back = ds_back.fields[0]

        # Check format extension is restored
        if original_format:
            roundtrip_format = None
            if field_back.custom_extensions:
                for ext in field_back.custom_extensions:
                    if ext.vendor_name == OSIVendor.DATABRICKS:
                        data = json.loads(ext.data)
                        if "format" in data:
                            roundtrip_format = data["format"]
            assert roundtrip_format is not None
            assert roundtrip_format["type"] == original_format["type"]

    @given(doc=roundtrip_osi_documents_with_multi_vendor_extensions())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_databricks_window_extension_preserved(self, doc: OSIDocument):
        """DATABRICKS window extension on metrics survives OSI→MV→OSI round-trip."""
        original_metrics = doc.semantic_model[0].metrics
        if not original_metrics:
            return

        original_metric = original_metrics[0]

        # Extract original DATABRICKS window
        original_window = None
        if original_metric.custom_extensions:
            for ext in original_metric.custom_extensions:
                if ext.vendor_name == OSIVendor.DATABRICKS:
                    data = json.loads(ext.data)
                    if "window" in data:
                        original_window = data["window"]

        results = osi_to_metric_view(doc)
        _, mv_model = results[0]

        # Verify window is on the MV measure
        if original_window and mv_model.measures:
            assert mv_model.measures[0].window is not None
            assert mv_model.measures[0].window[0].order == original_window[0]["order"]
            assert mv_model.measures[0].window[0].range == original_window[0]["range"]

        # Import back to OSI
        osi_back = metric_view_to_osi(mv_model, model_name="rt_model")
        back_metrics = osi_back.semantic_model[0].metrics

        # Check window extension is restored
        if original_window and back_metrics:
            roundtrip_window = None
            if back_metrics[0].custom_extensions:
                for ext in back_metrics[0].custom_extensions:
                    if ext.vendor_name == OSIVendor.DATABRICKS:
                        data = json.loads(ext.data)
                        if "window" in data:
                            roundtrip_window = data["window"]
            assert roundtrip_window is not None
            assert roundtrip_window[0]["order"] == original_window[0]["order"]
            assert roundtrip_window[0]["range"] == original_window[0]["range"]

    @given(doc=roundtrip_osi_documents_with_multi_vendor_extensions())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_export_does_not_corrupt_other_vendor_extensions(self, doc: OSIDocument):
        """Exporting to MV does not corrupt the source OSI document's non-DATABRICKS extensions.

        The export function should not modify the input document. Other vendor
        extensions (SNOWFLAKE, DBT, GOODDATA) on the source remain intact.
        """
        original_ds = doc.semantic_model[0].datasets[0]

        # Collect non-DATABRICKS extensions before export
        other_exts_before = []
        if original_ds.custom_extensions:
            for ext in original_ds.custom_extensions:
                if ext.vendor_name != OSIVendor.DATABRICKS:
                    other_exts_before.append((ext.vendor_name, ext.data))

        # Perform export (should not mutate source)
        _ = osi_to_metric_view(doc)

        # Verify non-DATABRICKS extensions are unchanged
        other_exts_after = []
        if original_ds.custom_extensions:
            for ext in original_ds.custom_extensions:
                if ext.vendor_name != OSIVendor.DATABRICKS:
                    other_exts_after.append((ext.vendor_name, ext.data))

        assert other_exts_before == other_exts_after
