"""Hypothesis strategies shared by every property test.

Per ``docs/ALGEBRA_LAWS.md §1``: strategies are deliberately minimal —
just enough to exercise the algebra without drifting into scenarios the
Foundation does not support.

Landed so far:

* ``identifiers()`` — syntactically valid, non-reserved identifiers
* ``dimension_sets()`` — small grain sets
* ``dimension_columns()`` / ``fact_columns()`` — individual columns
* ``states()`` — valid :class:`CalculationState`, built through
  :func:`osi.planning.algebra.source` so every generated state is
  reachable via the algebra

Strategies that require running SQL against DuckDB (e.g.
``duckdb_fixtures()``) land alongside Phase 4 codegen.
"""

from __future__ import annotations

from typing import cast

import sqlglot
from hypothesis import strategies as st
from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.common.types import DimensionSet
from osi.planning.algebra import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
    source,
)

_IDENTIFIER_REGEX = r"^[a-z][a-z0-9_]{0,15}$"

# Tokens we never want Hypothesis to feed into a SQL builder. Two
# disjoint groups:
#
# * **OSI internal sentinels** — names the algebra reserves for grain /
#   provenance / wildcard handling; ``normalize_identifier`` already
#   refuses them, so generating them here would just be a wasted draw.
# * **SQL reserved words** in the dialects we currently target (ANSI,
#   DuckDB, Snowflake). The shape regex above happily produces tokens
#   like ``in`` / ``as`` / ``or`` / ``on`` because they match
#   ``[a-z][a-z0-9_]{0,15}``. When such a token leaks through and is
#   later concatenated into a SQL string by another strategy
#   (notably :func:`aggregate_column`, which builds ``SUM(<over>)``),
#   sqlglot raises a ``ParseError`` and the whole property test
#   fails with what looks like an unrelated crash.
#
# Filtering at the strategy level is sufficient because every column /
# expression strategy below either (a) runs the identifier through
# :func:`_frozen_col_ref` (which quotes) or (b) builds the AST
# programmatically with ``quoted=True``. Production code is *not*
# protected by this list — ``normalize_identifier`` accepts SQL
# keywords today; if you want to close that gap, fix it at the
# parser / codegen layer rather than mirroring the keyword list here.
_OSI_RESERVED_TOKENS = frozenset({"__grain__", "__provenance__", "__all__"})

# Conservative subset of SQL reserved words that match the identifier
# regex (``[a-z][a-z0-9_]{0,15}``) and are known to cause sqlglot
# parser failures when used unquoted in expression position. We do
# not need to mirror the full ANSI / Snowflake / DuckDB keyword
# lists — the property tests only exercise a handful of expression
# shapes, and any keyword that survives this filter and still breaks
# parsing should be added here.
_SQL_KEYWORD_TOKENS = frozenset(
    {
        "all",
        "and",
        "as",
        "asc",
        "between",
        "by",
        "case",
        "cast",
        "cross",
        "desc",
        "distinct",
        "else",
        "end",
        "exists",
        "false",
        "for",
        "from",
        "full",
        "group",
        "having",
        "if",
        "in",
        "inner",
        "is",
        "join",
        "left",
        "like",
        "limit",
        "no",
        "not",
        "null",
        "of",
        "on",
        "or",
        "order",
        "outer",
        "qualify",
        "right",
        "select",
        "set",
        "some",
        "table",
        "then",
        "to",
        "true",
        "union",
        "unique",
        "using",
        "values",
        "when",
        "where",
        "with",
    }
)
_RESERVED_TOKENS = _OSI_RESERVED_TOKENS | _SQL_KEYWORD_TOKENS


def identifiers() -> st.SearchStrategy[Identifier]:
    """Generate a syntactically valid, non-reserved identifier."""
    return (
        st.from_regex(_IDENTIFIER_REGEX, fullmatch=True)
        .filter(lambda s: s not in _RESERVED_TOKENS)
        .map(normalize_identifier)
    )


def dimension_sets(
    min_size: int = 0,
    max_size: int = 4,
) -> st.SearchStrategy[DimensionSet]:
    """Generate a small, deduplicated frozenset of identifiers (a grain)."""
    return cast(
        st.SearchStrategy[DimensionSet],
        st.lists(identifiers(), min_size=min_size, max_size=max_size, unique=True).map(
            frozenset
        ),
    )


def _frozen_col_ref(name: Identifier) -> FrozenSQL:
    """Build a quoted column reference without going through ``parse_one``.

    Building the AST directly via :func:`exp.column` with
    ``quoted=True`` avoids two failure modes that bit us in the
    earlier ``parse_one(f'"{name}"')`` form:

    1. **Reserved-word leakage** — even though we wrapped the name in
       double quotes in the f-string, sqlglot still occasionally chose
       to parse generated names like ``in`` or ``as`` as keyword
       tokens before recognising the quoting context. Skipping the
       parser sidesteps the issue entirely.
    2. **Dialect-default quote style** — ``"foo"`` is portable today
       but a future dialect addition (Spark, BigQuery) might prefer
       backticks. ``quoted=True`` lets sqlglot pick the right style at
       render time.
    """
    return FrozenSQL.of(exp.column(str(name), quoted=True))


def dimension_column(name: Identifier) -> Column:
    """Canonical dimension column for ``name`` (identity expression)."""
    return Column(
        name=name,
        expression=_frozen_col_ref(name),
        dependencies=frozenset(),
        kind=ColumnKind.DIMENSION,
    )


def fact_column(name: Identifier) -> Column:
    """Canonical fact column for ``name`` (identity expression)."""
    return Column(
        name=name,
        expression=_frozen_col_ref(name),
        dependencies=frozenset(),
        kind=ColumnKind.FACT,
    )


def aggregate_column(
    name: Identifier,
    *,
    function: AggregateFunction = AggregateFunction.SUM,
    over: Identifier,
) -> Column:
    """Build an AGGREGATE column named ``name`` reducing ``over``.

    The aggregate AST is built programmatically rather than parsed
    from a formatted string. The previous string path
    (``parse_one(f"{function.name}({over})")``) fed an unquoted
    identifier into sqlglot's expression parser; when Hypothesis
    drew a SQL keyword like ``in``, sqlglot raised ``ParseError``
    and the test failed with what looked like an unrelated crash.
    Constructing the column reference with ``quoted=True`` and the
    aggregate node with :func:`exp.Anonymous` skips parsing
    entirely and is keyword-safe by construction.
    """
    column_ref = exp.column(str(over), quoted=True)
    if function is AggregateFunction.COUNT_DISTINCT:
        agg_node = exp.Count(this=exp.Distinct(expressions=[column_ref]))
    else:
        agg_node = exp.Anonymous(
            this=function.name,
            expressions=[column_ref],
        )
    return Column(
        name=name,
        expression=FrozenSQL.of(agg_node),
        dependencies=frozenset({over}),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(
            function=function,
            argument=FrozenSQL.of(exp.column(str(over), quoted=True)),
        ),
    )


@st.composite
def source_states(
    draw: st.DrawFn,
    *,
    min_dims: int = 1,
    max_dims: int = 4,
    min_facts: int = 0,
    max_facts: int = 3,
) -> CalculationState:
    """Generate a valid :class:`CalculationState` by calling ``source``.

    This guarantees **invariant I-3**: every generated state arrives
    through the algebra, not through direct construction. Hypothesis
    shrinking stays inside the valid-state space because ``source``
    validates its preconditions.
    """
    # Draw dims and facts separately so we can honour ``min_facts``
    # without relying on partition arithmetic. ``unique=True`` on a
    # case-folding map would under-count, so we deduplicate after map.
    n_dims = draw(st.integers(min_value=min_dims, max_value=max_dims))
    n_facts = draw(st.integers(min_value=min_facts, max_value=max_facts))
    pool = draw(
        st.lists(
            identifiers(),
            unique=True,
            min_size=n_dims + n_facts,
            max_size=n_dims + n_facts,
        )
    )
    dim_names = pool[:n_dims]
    fact_names = pool[n_dims:]
    pk_size = draw(st.integers(min_value=1, max_value=len(dim_names)))
    primary_key: DimensionSet = frozenset(dim_names[:pk_size])
    return source(
        primary_key=primary_key,
        dimension_columns=[dimension_column(n) for n in dim_names],
        fact_columns=[fact_column(n) for n in fact_names],
    )


def states(
    *, min_dims: int = 1, max_dims: int = 4, min_facts: int = 0, max_facts: int = 3
) -> st.SearchStrategy[CalculationState]:
    """Public wrapper for :func:`source_states`."""
    return source_states(
        min_dims=min_dims,
        max_dims=max_dims,
        min_facts=min_facts,
        max_facts=max_facts,
    )


__all__ = [
    "aggregate_column",
    "dimension_column",
    "dimension_sets",
    "fact_column",
    "identifiers",
    "source_states",
    "states",
]
