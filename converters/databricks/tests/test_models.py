"""Tests for Metric View Pydantic models.

Includes property-based tests (Hypothesis) and example-based unit tests.

The Hypothesis strategies deliberately generate adversarial inputs:
- YAML-special characters (: # { } [ ] * & ! etc.)
- Strings that YAML interprets as booleans (true, yes, on)
- Strings that YAML interprets as null (null, ~)
- Strings that look like numbers or dates
- Nested joins (snowflake schema, depth 2)
- Materialization config
- Empty vs None distinctions
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

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

# Adversarial strings that exercise YAML quoting edge cases
_yaml_dangerous_literals = st.sampled_from([
    "true", "false", "yes", "no", "on", "off",
    "True", "False", "Yes", "No", "On", "Off",
    "null", "Null", "NULL", "~",
    "1.0", "0", "3.14", "1e10", "-1",
    "2024-01-01", "12:30:00",
    "key: value", "has # comment", "with: colon",
    "{curly}", "[bracket]", "pipe | here",
    "star * wild", "ampersand & ref", "exclaim!",
    "percent%", "at@sign", "backtick`",
    "single'quote", 'double"quote',
    "  leading spaces", "trailing spaces  ",
    "ratio: sales/cost", "a > b", "x & y",
])

# Mix safe text with adversarial strings for broad coverage
_safe_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=50,
)
_adversarial_text = st.one_of(_safe_text, _yaml_dangerous_literals)


@st.composite
def metric_view_formats(draw):
    return MetricViewFormat(
        type=draw(st.sampled_from(["number", "currency", "date", "date_time", "percentage", "byte"])),
        currency_code=draw(st.none() | st.sampled_from(["USD", "EUR", "GBP"])),
        decimal_places=draw(st.none() | st.just({"min": 0, "max": 2})),
        hide_group_separator=draw(st.none() | st.booleans()),
        abbreviation=draw(st.none() | st.sampled_from(["K", "M", "B"])),
    )


@st.composite
def metric_view_fields(draw):
    return MetricViewField(
        name=draw(_identifier),
        expr=draw(_expr),
        comment=draw(st.none() | _adversarial_text),
        display_name=draw(st.none() | _adversarial_text),
        synonyms=draw(st.none() | st.lists(_adversarial_text, min_size=1, max_size=3)),
        format=draw(st.none() | metric_view_formats()),
    )


@st.composite
def metric_view_windows(draw):
    return MetricViewWindow(
        order=draw(_identifier),
        range=draw(st.sampled_from(["trailing 7 day", "trailing 30 day", "trailing 1 hour", "unbounded"])),
        semiadditive=draw(st.none() | st.sampled_from(["last", "first"])),
    )


@st.composite
def metric_view_measures(draw):
    return MetricViewMeasure(
        name=draw(_identifier),
        expr=draw(_expr),
        comment=draw(st.none() | _adversarial_text),
        display_name=draw(st.none() | _adversarial_text),
        synonyms=draw(st.none() | st.lists(_adversarial_text, min_size=1, max_size=3)),
        format=draw(st.none() | metric_view_formats()),
        window=draw(st.none() | st.lists(metric_view_windows(), min_size=1, max_size=2)),
    )


@st.composite
def metric_view_joins_leaf(draw):
    """Generate a leaf join (no nested joins)."""
    return MetricViewJoin(
        name=draw(_identifier),
        source=draw(_three_part_name),
        on=draw(st.none() | _expr),
        using=draw(st.none() | st.lists(_identifier, min_size=1, max_size=3)),
        cardinality=draw(st.none() | st.sampled_from(["many_to_one", "one_to_many"])),
        rely=draw(st.none() | st.builds(MetricViewRely, at_most_one_match=st.booleans())),
        joins=None,
    )


@st.composite
def metric_view_joins_nested(draw):
    """Generate a join with one level of nested joins (snowflake schema depth 2)."""
    children = draw(st.lists(metric_view_joins_leaf(), min_size=1, max_size=2))
    return MetricViewJoin(
        name=draw(_identifier),
        source=draw(_three_part_name),
        on=draw(st.none() | _expr),
        using=draw(st.none() | st.lists(_identifier, min_size=1, max_size=3)),
        cardinality=draw(st.none() | st.sampled_from(["many_to_one", "one_to_many"])),
        rely=draw(st.none() | st.builds(MetricViewRely, at_most_one_match=st.booleans())),
        joins=children,
    )


# Mix leaf and nested joins
_joins_strategy = st.one_of(metric_view_joins_leaf(), metric_view_joins_nested())


@st.composite
def metric_view_materializations(draw):
    mvs = draw(st.none() | st.lists(
        st.builds(
            MetricViewMaterializedView,
            name=_identifier,
            type=st.sampled_from(["aggregated", "unaggregated"]),
            dimensions=st.none() | st.lists(_identifier, min_size=1, max_size=3),
            measures=st.none() | st.lists(_identifier, min_size=1, max_size=3),
        ),
        min_size=1,
        max_size=2,
    ))
    return MetricViewMaterialization(
        schedule=draw(st.none() | st.sampled_from(["every 6 hours", "every 1 hour", "daily"])),
        mode=draw(st.none() | st.sampled_from(["relaxed", "strict"])),
        materialized_views=mvs,
    )


@st.composite
def metric_view_models(draw):
    return MetricViewModel(
        version="1.1",
        source=draw(_three_part_name),
        comment=draw(st.none() | _adversarial_text),
        filter=draw(st.none() | _expr),
        joins=draw(st.none() | st.lists(_joins_strategy, min_size=1, max_size=3)),
        fields=draw(st.none() | st.lists(metric_view_fields(), min_size=1, max_size=5)),
        measures=draw(st.none() | st.lists(metric_view_measures(), min_size=1, max_size=5)),
        materialization=draw(st.none() | metric_view_materializations()),
    )


# --- Property Tests ---


class TestMetricViewModelRoundTrip:
    """Property 1: Metric View Model parse-serialize round-trip."""

    @given(model=metric_view_models())
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_parse_serialize_roundtrip(self, model: MetricViewModel):
        """For any valid MetricViewModel, serializing then parsing produces an equivalent model."""
        yaml_str = model.to_yaml()
        parsed = MetricViewModel.from_yaml(yaml_str)
        assert parsed == model

    @given(model=metric_view_models())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_double_roundtrip_stable(self, model: MetricViewModel):
        """Double round-trip (serialize→parse→serialize→parse) is stable."""
        yaml1 = model.to_yaml()
        parsed1 = MetricViewModel.from_yaml(yaml1)
        yaml2 = parsed1.to_yaml()
        parsed2 = MetricViewModel.from_yaml(yaml2)
        assert parsed1 == parsed2
        assert yaml1 == yaml2


# --- Regression Tests for Specific Edge Cases ---


class TestYamlEdgeCases:
    """Pinned regression tests for YAML serialization edge cases."""

    def test_boolean_like_comment(self):
        """Comments that look like YAML booleans must round-trip."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", comment="true")],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.fields[0].comment == "true"

    def test_null_like_synonym(self):
        """Synonyms that look like YAML null must round-trip."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", synonyms=["null", "~"])],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.fields[0].synonyms == ["null", "~"]

    def test_numeric_like_display_name(self):
        """Display names that look like numbers must round-trip as strings."""
        model = MetricViewModel(
            source="a.b.c",
            measures=[MetricViewMeasure(name="m1", expr="SUM(x)", display_name="1.0")],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.measures[0].display_name == "1.0"

    def test_colon_in_comment(self):
        """Colons in comments must not break YAML parsing."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", comment="ratio: sales/cost")],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.fields[0].comment == "ratio: sales/cost"

    def test_special_chars_in_synonyms(self):
        """Special characters in synonyms must round-trip."""
        syns = ["star * wild", "hash # tag", "{curly}", "[bracket]"]
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", synonyms=syns)],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.fields[0].synonyms == syns

    def test_leading_trailing_whitespace(self):
        """Strings with leading/trailing whitespace must round-trip."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", comment="  padded  ")],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.fields[0].comment == "  padded  "

    def test_nested_joins_roundtrip(self):
        """Nested joins (snowflake schema) must round-trip."""
        model = MetricViewModel(
            source="cat.sch.fact_sales",
            joins=[
                MetricViewJoin(
                    name="date_dim",
                    source="cat.sch.date_dim",
                    on="source.date_sk = date_dim.d_date_sk",
                    cardinality="many_to_one",
                    joins=[
                        MetricViewJoin(
                            name="fiscal_cal",
                            source="cat.sch.fiscal_calendar",
                            on="date_dim.cal_id = fiscal_cal.id",
                        ),
                    ],
                ),
            ],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed == model
        assert parsed.joins[0].joins[0].name == "fiscal_cal"

    def test_materialization_roundtrip(self):
        """Full materialization config round-trips correctly."""
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1")],
            materialization=MetricViewMaterialization(
                schedule="every 6 hours",
                mode="relaxed",
                materialized_views=[
                    MetricViewMaterializedView(
                        name="mv1",
                        type="aggregated",
                        dimensions=["f1"],
                        measures=["m1"],
                    ),
                ],
            ),
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed == model

    def test_date_like_string_source(self):
        """A source that could look like a date still round-trips as string."""
        # This is an edge case — three-part names won't look like dates,
        # but expressions in comments could
        model = MetricViewModel(
            source="a.b.c",
            fields=[MetricViewField(name="f1", expr="col1", comment="2024-01-01")],
        )
        parsed = MetricViewModel.from_yaml(model.to_yaml())
        assert parsed.fields[0].comment == "2024-01-01"


# --- Unit Tests ---


class TestMetricViewModelParsing:
    """Unit tests for parsing Metric View YAML."""

    def test_parse_complete_metric_view(self):
        """Test parsing valid Metric View YAML with all sections populated."""
        yaml_str = """
version: "1.1"
source: catalog.schema.store_sales
comment: "Store sales metric view"
filter: "ss_quantity > 0"
joins:
  - name: date_dim
    source: catalog.schema.date_dim
    on: "source.ss_sold_date_sk = date_dim.d_date_sk"
    cardinality: many_to_one
fields:
  - name: sold_date
    expr: date_dim.d_date
    comment: "Sale date"
    display_name: "Date of Sale"
    synonyms: ["sale date", "transaction date"]
measures:
  - name: total_sales
    expr: "SUM(ss_net_paid)"
    comment: "Total net sales"
materialization:
  schedule: "every 6 hours"
  mode: relaxed
  materialized_views:
    - name: mv_daily_sales
      type: aggregated
      dimensions: [sold_date]
      measures: [total_sales]
"""
        model = MetricViewModel.from_yaml(yaml_str)
        assert model.version == "1.1"
        assert model.source == "catalog.schema.store_sales"
        assert model.comment == "Store sales metric view"
        assert model.filter == "ss_quantity > 0"
        assert len(model.joins) == 1
        assert model.joins[0].name == "date_dim"
        assert model.joins[0].cardinality == "many_to_one"
        assert len(model.fields) == 1
        assert model.fields[0].name == "sold_date"
        assert model.fields[0].display_name == "Date of Sale"
        assert model.fields[0].synonyms == ["sale date", "transaction date"]
        assert len(model.measures) == 1
        assert model.measures[0].name == "total_sales"
        assert model.materialization.schedule == "every 6 hours"
        assert model.materialization.materialized_views[0].name == "mv_daily_sales"

    def test_parse_minimal_metric_view(self):
        """Test parsing YAML with optional sections absent."""
        yaml_str = """
version: "1.1"
source: catalog.schema.my_table
fields:
  - name: col1
    expr: col1
"""
        model = MetricViewModel.from_yaml(yaml_str)
        assert model.source == "catalog.schema.my_table"
        assert model.joins is None
        assert model.filter is None
        assert model.materialization is None
        assert model.comment is None
        assert len(model.fields) == 1

    def test_validation_error_missing_source(self):
        """Test validation error on missing required field (source)."""
        yaml_str = """
version: "1.1"
fields:
  - name: col1
    expr: col1
"""
        with pytest.raises(ValidationError) as exc_info:
            MetricViewModel.from_yaml(yaml_str)
        assert "source" in str(exc_info.value)

    def test_serialization_excludes_none_fields(self):
        """Test serialization excludes None fields and preserves key ordering."""
        model = MetricViewModel(
            source="catalog.schema.table",
            fields=[MetricViewField(name="f1", expr="col1")],
        )
        yaml_str = model.to_yaml()
        assert "filter" not in yaml_str
        assert "joins" not in yaml_str
        assert "materialization" not in yaml_str
        assert "comment" not in yaml_str
        # Check key ordering: version before source, source before fields
        version_pos = yaml_str.index("version")
        source_pos = yaml_str.index("source")
        fields_pos = yaml_str.index("fields")
        assert version_pos < source_pos < fields_pos
