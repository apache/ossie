"""Shared pytest fixtures and Hypothesis strategies for osi-databricks tests.

This module provides:
- Hypothesis strategies for generating valid MetricViewModel instances
- Hypothesis strategies for generating valid OSIDocument instances with DATABRICKS dialects
- Pytest fixtures that load TPC-DS YAML fixture files from disk
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Pytest Fixtures — TPC-DS Fixture Files
# ---------------------------------------------------------------------------


@pytest.fixture
def metric_view_tpcds_yaml() -> str:
    """Raw YAML string of the TPC-DS Metric View fixture."""
    return (FIXTURES_DIR / "metric_view_tpcds.yaml").read_text()


@pytest.fixture
def metric_view_tpcds_model(metric_view_tpcds_yaml: str) -> MetricViewModel:
    """Parsed MetricViewModel from the TPC-DS fixture."""
    return MetricViewModel.from_yaml(metric_view_tpcds_yaml)


@pytest.fixture
def osi_tpcds_yaml() -> str:
    """Raw YAML string of the TPC-DS OSI fixture."""
    return (FIXTURES_DIR / "osi_tpcds.yaml").read_text()


@pytest.fixture
def osi_tpcds_document(osi_tpcds_yaml: str) -> OSIDocument:
    """Parsed OSIDocument from the TPC-DS fixture."""
    raw = yaml.safe_load(osi_tpcds_yaml)
    return OSIDocument.model_validate(raw)


# ---------------------------------------------------------------------------
# Base Hypothesis Strategies — Building Blocks
# ---------------------------------------------------------------------------

# Simple SQL-safe identifiers (lowercase, starts with letter)
identifier = st.from_regex(r"[a-z][a-z0-9_]{0,19}", fullmatch=True)

# Three-part catalog.schema.table names
three_part_name = st.builds(
    lambda a, b, c: f"{a}.{b}.{c}",
    identifier,
    identifier,
    identifier,
)

# Simple column-like expressions (standard SQL, no Databricks-specific syntax)
standard_expr = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,19}", fullmatch=True)

# Safe text for metadata fields (avoids JSON/YAML issues)
safe_text = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=126,
        blacklist_characters='"\\',
    ),
    min_size=1,
    max_size=30,
)

# Aggregate function expressions
aggregate_expr = st.builds(
    lambda func, col: f"{func}({col})",
    st.sampled_from(["SUM", "COUNT", "AVG", "MAX", "MIN"]),
    standard_expr,
)


# ---------------------------------------------------------------------------
# Hypothesis Strategies — MetricViewModel Components
# ---------------------------------------------------------------------------


@st.composite
def mv_formats(draw):
    """Generate valid MetricViewFormat instances."""
    fmt_type = draw(st.sampled_from(["number", "currency", "date", "percentage"]))
    kwargs = {"type": fmt_type}

    if fmt_type == "currency":
        kwargs["currency_code"] = draw(st.sampled_from(["USD", "EUR", "GBP"]))
    elif fmt_type == "date":
        kwargs["date_format"] = draw(st.sampled_from(["year_month_day", "month_day_year"]))
    elif fmt_type == "number":
        kwargs["decimal_places"] = {"min": 2, "max": 2}

    return MetricViewFormat(**kwargs)


@st.composite
def mv_fields(draw, *, with_metadata: bool = True):
    """Generate valid MetricViewField instances.

    Args:
        with_metadata: If True, may include comment, display_name, synonyms, format.
    """
    name = draw(identifier)
    expr = draw(standard_expr)

    kwargs = {"name": name, "expr": expr}

    if with_metadata:
        kwargs["comment"] = draw(st.none() | safe_text)
        kwargs["display_name"] = draw(st.none() | safe_text)
        kwargs["synonyms"] = draw(st.none() | st.lists(safe_text, min_size=1, max_size=3))
        kwargs["format"] = draw(st.none() | mv_formats())

    return MetricViewField(**kwargs)


@st.composite
def mv_windows(draw):
    """Generate valid MetricViewWindow instances."""
    order = draw(identifier)
    range_val = draw(st.sampled_from([
        "trailing 7 day",
        "trailing 30 day",
        "trailing 1 month",
        "unbounded",
    ]))
    semiadditive = draw(st.none() | st.sampled_from(["last", "first"]))
    return MetricViewWindow(order=order, range=range_val, semiadditive=semiadditive)


@st.composite
def mv_measures(draw, *, with_metadata: bool = True):
    """Generate valid MetricViewMeasure instances.

    Args:
        with_metadata: If True, may include comment, display_name, synonyms, format, window.
    """
    name = draw(identifier)
    expr = draw(aggregate_expr)

    kwargs = {"name": name, "expr": expr}

    if with_metadata:
        kwargs["comment"] = draw(st.none() | safe_text)
        kwargs["display_name"] = draw(st.none() | safe_text)
        kwargs["synonyms"] = draw(st.none() | st.lists(safe_text, min_size=1, max_size=3))
        kwargs["format"] = draw(st.none() | mv_formats())
        kwargs["window"] = draw(st.none() | st.lists(mv_windows(), min_size=1, max_size=2))

    return MetricViewMeasure(**kwargs)


@st.composite
def mv_joins(draw, *, allow_nested: bool = False):
    """Generate valid MetricViewJoin instances.

    The ON clause uses the format: source.from_col = join_name.to_col
    which matches what the converter produces on export.

    Args:
        allow_nested: If True, may include nested joins (one level deep only).
    """
    name = draw(identifier)
    source = draw(three_part_name)
    from_col = draw(identifier)
    to_col = draw(identifier)
    on_clause = f"source.{from_col} = {name}.{to_col}"

    cardinality = draw(st.none() | st.sampled_from(["many_to_one", "one_to_many"]))
    rely = draw(st.none() | st.just(MetricViewRely(at_most_one_match=True)))

    nested_joins = None
    if allow_nested:
        nested_joins = draw(st.none() | st.lists(mv_joins(allow_nested=False), min_size=1, max_size=1))

    return MetricViewJoin(
        name=name,
        source=source,
        on=on_clause,
        cardinality=cardinality,
        rely=rely,
        joins=nested_joins,
    )


@st.composite
def mv_materializations(draw):
    """Generate valid MetricViewMaterialization instances."""
    schedule = draw(st.none() | st.sampled_from(["every 6 hours", "every 1 hour", "daily"]))
    mode = draw(st.none() | st.sampled_from(["relaxed", "strict"]))

    # Optionally generate materialized views
    mat_views = draw(st.none() | st.lists(
        st.builds(
            MetricViewMaterializedView,
            name=identifier,
            type=st.sampled_from(["aggregated", "unaggregated"]),
            dimensions=st.none() | st.lists(identifier, min_size=1, max_size=3),
            measures=st.none() | st.lists(identifier, min_size=1, max_size=2),
        ),
        min_size=1,
        max_size=2,
    ))

    return MetricViewMaterialization(
        schedule=schedule,
        mode=mode,
        materialized_views=mat_views,
    )


@st.composite
def mv_models(draw, *, with_joins: bool = True, with_materialization: bool = True):
    """Generate valid MetricViewModel instances.

    Produces models with three-part name sources, fields, optional measures,
    optional joins, and optional materialization.

    Args:
        with_joins: If True, may include joins.
        with_materialization: If True, may include materialization config.
    """
    source = draw(three_part_name)
    comment = draw(st.none() | safe_text)
    filter_expr = draw(st.none() | standard_expr)
    fields = draw(st.lists(mv_fields(), min_size=1, max_size=5))
    measures = draw(st.none() | st.lists(mv_measures(), min_size=1, max_size=4))

    joins = None
    if with_joins:
        joins = draw(st.none() | st.lists(mv_joins(allow_nested=True), min_size=1, max_size=3))

    materialization = None
    if with_materialization:
        materialization = draw(st.none() | mv_materializations())

    return MetricViewModel(
        version="1.1",
        source=source,
        comment=comment,
        filter=filter_expr,
        fields=fields,
        measures=measures,
        joins=joins,
        materialization=materialization,
    )


# ---------------------------------------------------------------------------
# Hypothesis Strategies — OSI Document Components
# ---------------------------------------------------------------------------


@st.composite
def osi_fields(draw, *, with_ansi: bool = False, with_metadata: bool = True):
    """Generate valid OSIField instances with DATABRICKS dialect.

    Args:
        with_ansi: If True, also includes an ANSI_SQL dialect expression.
        with_metadata: If True, may include description and ai_context.
    """
    name = draw(identifier)
    expr_str = draw(standard_expr)

    dialects = [OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str)]
    if with_ansi:
        dialects.append(OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=expr_str))

    kwargs: dict = {
        "name": name,
        "expression": OSIExpression(dialects=dialects),
        "dimension": OSIDimension(is_time=draw(st.booleans())),
    }

    if with_metadata:
        kwargs["description"] = draw(st.none() | safe_text)
        synonyms = draw(st.none() | st.lists(safe_text, min_size=1, max_size=4))
        if synonyms:
            kwargs["ai_context"] = OSIAIContextObject(synonyms=tuple(synonyms))

    return OSIField(**kwargs)


@st.composite
def osi_metrics(draw, *, with_ansi: bool = False, with_metadata: bool = True):
    """Generate valid OSIMetric instances with DATABRICKS dialect.

    Args:
        with_ansi: If True, also includes an ANSI_SQL dialect expression.
        with_metadata: If True, may include description and ai_context.
    """
    name = draw(identifier)
    expr_str = draw(aggregate_expr)

    dialects = [OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str)]
    if with_ansi:
        dialects.append(OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=expr_str))

    kwargs: dict = {
        "name": name,
        "expression": OSIExpression(dialects=dialects),
    }

    if with_metadata:
        kwargs["description"] = draw(st.none() | safe_text)
        synonyms = draw(st.none() | st.lists(safe_text, min_size=1, max_size=4))
        if synonyms:
            kwargs["ai_context"] = OSIAIContextObject(synonyms=tuple(synonyms))

    return OSIMetric(**kwargs)


@st.composite
def osi_relationships(draw, dataset_name: str):
    """Generate valid OSIRelationship instances.

    Args:
        dataset_name: The name of the 'from' dataset for the relationship.
    """
    name = draw(identifier)
    from_col = draw(identifier)
    to_col = draw(identifier)

    cardinality = draw(st.none() | st.sampled_from(["many_to_one", "one_to_many"]))
    custom_extensions = None
    if cardinality:
        custom_extensions = [
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps({"cardinality": cardinality}),
            )
        ]

    return OSIRelationship(
        name=name,
        **{"from": dataset_name},
        to=name,  # target dataset = relationship name for simplicity
        from_columns=[from_col],
        to_columns=[to_col],
        custom_extensions=custom_extensions,
    )


@st.composite
def osi_documents(
    draw,
    *,
    with_metrics: bool = True,
    with_relationships: bool = True,
    with_ansi: bool = False,
):
    """Generate valid OSIDocument instances with DATABRICKS dialect expressions.

    Produces documents with a single semantic model containing one dataset,
    optional metrics, and optional relationships.

    Args:
        with_metrics: If True, may include metrics.
        with_relationships: If True, may include relationships.
        with_ansi: If True, fields/metrics also get ANSI_SQL dialect entries.
    """
    dataset_name = draw(identifier)
    source = f"catalog.schema.{dataset_name}"
    fields = draw(st.lists(osi_fields(with_ansi=with_ansi), min_size=1, max_size=4))

    metrics = None
    if with_metrics:
        metrics = draw(st.none() | st.lists(osi_metrics(with_ansi=with_ansi), min_size=1, max_size=3))

    relationships = None
    if with_relationships:
        relationships = draw(
            st.none() | st.lists(osi_relationships(dataset_name), min_size=1, max_size=2)
        )

    description = draw(st.none() | safe_text)

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

    dialects = [OSIDialect.DATABRICKS]
    if with_ansi:
        dialects.append(OSIDialect.ANSI_SQL)

    return OSIDocument(
        version="0.2.0.dev0",
        dialects=dialects,
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[semantic_model],
    )


@st.composite
def osi_documents_with_extensions(draw):
    """Generate OSIDocument instances with DATABRICKS and other vendor custom extensions.

    Useful for testing that round-trips preserve custom extensions for all vendors.
    """
    dataset_name = draw(identifier)
    source = f"catalog.schema.{dataset_name}"
    expr_str = draw(standard_expr)

    # DATABRICKS dataset extension (filter)
    filter_expr = draw(st.none() | standard_expr)
    ds_extensions = []
    if filter_expr:
        ds_extensions.append(
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps({"filter": filter_expr}),
            )
        )

    # Other vendor extension that should survive round-trips
    other_vendor = draw(st.sampled_from([OSIVendor.SNOWFLAKE, OSIVendor.DBT, OSIVendor.GOODDATA]))
    other_key = draw(identifier)
    other_value = draw(safe_text)
    ds_extensions.append(
        OSICustomExtension(
            vendor_name=other_vendor,
            data=json.dumps({other_key: other_value}),
        )
    )

    # Field with format extension
    field = OSIField(
        name=draw(identifier),
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr_str)]
        ),
        dimension=OSIDimension(is_time=False),
        custom_extensions=[
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps({"format": {"type": "number"}}),
            )
        ],
    )

    # Metric with window extension
    metric = OSIMetric(
        name=draw(identifier),
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=f"SUM({expr_str})")]
        ),
        custom_extensions=[
            OSICustomExtension(
                vendor_name=OSIVendor.DATABRICKS,
                data=json.dumps({"window": [{"order": "d_date", "range": "trailing 7 day"}]}),
            )
        ],
    )

    # Semantic model with materialization extension
    sm_ext = OSICustomExtension(
        vendor_name=OSIVendor.DATABRICKS,
        data=json.dumps({"materialization": {"schedule": "every 6 hours", "mode": "relaxed"}}),
    )

    dataset = OSIDataset(
        name=dataset_name,
        source=source,
        fields=[field],
        custom_extensions=ds_extensions if ds_extensions else None,
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
