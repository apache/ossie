"""Law §4.13 — Error Taxonomy.

Every exception raised from the algebra is an :class:`OSIError` subclass
with a code that matches a value in :class:`ErrorCode`. No bare
:class:`ValueError` / :class:`AssertionError` / :class:`RuntimeError` at
runtime.

Mutation target: ``src/osi/errors.py``.
"""

from __future__ import annotations

from hypothesis import given, settings

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIError
from osi.planning.algebra import CalculationState, project, source
from tests.properties.strategies import dimension_column, identifiers, states


@given(state=states(), bogus=identifiers())
@settings(max_examples=200, deadline=None)
def test_project_unknown_column_raises_typed_osi_error(
    state: CalculationState, bogus: str
) -> None:
    if bogus in state.column_names:
        return
    try:
        project(state, [bogus])
    except OSIError as err:
        assert err.code in ErrorCode
        assert err.code.value.startswith(("E3", "E4"))
        return
    except Exception as err:  # pragma: no cover — law-breach signal
        raise AssertionError(
            f"non-OSIError raised from project: {type(err).__name__}"
        ) from err
    raise AssertionError("project on unknown column must raise OSIError")


def test_source_empty_pk_raises_typed_osi_error() -> None:
    """Example-based: empty primary key is a structural violation."""
    pk = frozenset()
    try:
        source(
            primary_key=pk,
            dimension_columns=[dimension_column(normalize_identifier("id"))],
        )
    except OSIError as err:
        assert err.code is ErrorCode.E2007_MISSING_PRIMARY_KEY
        return
    except Exception as err:  # pragma: no cover
        raise AssertionError(
            f"non-OSIError raised from source: {type(err).__name__}"
        ) from err
    raise AssertionError("source with empty PK must raise")
