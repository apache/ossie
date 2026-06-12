"""Tests for dialect_utils module.

Property 5: Dialect selection prefers DATABRICKS with ANSI_SQL fallback.
"""

from hypothesis import given, settings
from hypothesis import strategies as st
from osi.models import OSIDialect, OSIDialectExpression

from osi_databricks.dialect_utils import is_standard_sql, select_dialect_expression

# --- Hypothesis Strategies ---

_expr_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), whitelist_characters="_"),
    min_size=1,
    max_size=50,
)


@st.composite
def dialect_expression_lists(draw):
    """Generate lists of OSIDialectExpression with various dialect combos."""
    available_dialects = draw(
        st.lists(
            st.sampled_from([OSIDialect.DATABRICKS, OSIDialect.ANSI_SQL, OSIDialect.SNOWFLAKE]),
            min_size=1,
            max_size=3,
            unique=True,
        )
    )
    return [
        OSIDialectExpression(dialect=d, expression=draw(_expr_text))
        for d in available_dialects
    ]


# --- Property Tests ---


class TestDialectSelectionProperty:
    """Property 5: Dialect selection prefers DATABRICKS with ANSI_SQL fallback."""

    @given(dialects=dialect_expression_lists())
    @settings(max_examples=100)
    def test_prefers_databricks_over_ansi(self, dialects: list[OSIDialectExpression]):
        """When DATABRICKS is present, it is always selected."""
        result = select_dialect_expression(dialects)
        by_dialect = {d.dialect: d.expression for d in dialects}

        if OSIDialect.DATABRICKS in by_dialect:
            assert result == by_dialect[OSIDialect.DATABRICKS]
        elif OSIDialect.ANSI_SQL in by_dialect:
            assert result == by_dialect[OSIDialect.ANSI_SQL]
        else:
            assert result is None

    @given(expr=_expr_text)
    @settings(max_examples=100)
    def test_databricks_only_returns_databricks(self, expr: str):
        """Single DATABRICKS dialect always returns that expression."""
        dialects = [OSIDialectExpression(dialect=OSIDialect.DATABRICKS, expression=expr)]
        assert select_dialect_expression(dialects) == expr

    @given(expr=_expr_text)
    @settings(max_examples=100)
    def test_ansi_only_returns_ansi(self, expr: str):
        """Single ANSI_SQL dialect is used as fallback."""
        dialects = [OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=expr)]
        assert select_dialect_expression(dialects) == expr

    def test_no_usable_dialect_returns_none(self):
        """When neither DATABRICKS nor ANSI_SQL is present, returns None."""
        dialects = [OSIDialectExpression(dialect=OSIDialect.SNOWFLAKE, expression="x")]
        assert select_dialect_expression(dialects) is None


# --- Unit Tests for is_standard_sql ---


class TestIsStandardSql:
    """Unit tests for ANSI SQL detection."""

    def test_simple_column_is_standard(self):
        assert is_standard_sql("col1") is True

    def test_sum_is_standard(self):
        assert is_standard_sql("SUM(amount)") is True

    def test_filter_clause_is_not_standard(self):
        assert is_standard_sql("COUNT(*) FILTER (WHERE active)") is False

    def test_measure_is_not_standard(self):
        assert is_standard_sql("MEASURE(total_sales)") is False

    def test_qualify_is_not_standard(self):
        assert is_standard_sql("ROW_NUMBER() OVER() QUALIFY rn = 1") is False

    def test_cast_operator_is_not_standard(self):
        assert is_standard_sql("col::INT") is False

    def test_date_trunc_is_standard(self):
        # DATE_TRUNC itself is widely supported, not Databricks-only
        assert is_standard_sql("DATE_TRUNC('month', d_date)") is True
