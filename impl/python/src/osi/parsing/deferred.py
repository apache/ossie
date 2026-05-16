"""Deferred-feature rejection.

Every feature listed in ``specs/deferred/`` must be unambiguously
refused at parse time with :class:`ErrorCode.E_DEFERRED_KEY_REJECTED`.

Two surfaces need guarding:

1. **Raw YAML keys** — this is what pydantic ``extra="forbid"`` handles,
   but some deferred features live inside otherwise-valid shapes (e.g.
   a ``grain`` attribute on a metric). :func:`check_yaml_deferred` walks
   the raw document before pydantic validation so we can attach a
   friendlier error and a stable ``E1105``.

2. **SQL ASTs** — window functions, grouping-set constructs, PIVOT,
   lateral joins, etc. :func:`check_expression_deferred` walks the
   SQLGlot AST of every expression after pydantic parsed it.

Both entry points take a source location so diagnostics can point at
the offending YAML node or expression.
"""

from __future__ import annotations

from typing import Any, Final, Iterable

from sqlglot import expressions as exp

from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIParseError
from osi.parsing._root import unwrap_model_root

# ---------------------------------------------------------------------------
# YAML deferred-key inventory
# ---------------------------------------------------------------------------

# Keys that may appear at the top level of a *metric* or *field* mapping
# but are deferred. Present ⇒ E_DEFERRED_KEY_REJECTED.
#
# S-1 expanded this set to enforce the §10 deferred list of the
# Foundation v0.1 spec. Every key here MUST appear in
# ``Proposed_OSI_Semantics.md §10`` or in the Appendix B
# decision-archive.
DEFERRED_METRIC_KEYS: Final[frozenset[str]] = frozenset(
    {
        "grain",
        "filter",
        "semi_additive",
        "window",
        "reset",
        # S-1: per-metric joins block (D-001 / D-004 deferred form)
        "joins",
        # S-1: ``using_relationships`` was the per-metric override; the
        # Foundation routes joins by default-shape (D-004) instead.
        "using_relationships",
        # S-1: named-filter scope tags
        "named_filters",
        # S-1: ``dataset:`` on a top-level metric is a v1 proposal for
        # explicit metric scoping. Foundation v0.1 requires the metric
        # body to be self-describing (the home dataset is inferred from
        # the resolved expression). Catch this before pydantic so the
        # rejection cites the deferred catalog instead of a generic
        # "extra field" error.
        "dataset",
        # S-1: ``agg`` as a top-level YAML key on a metric/field is
        # the deferred ``AGG()`` keyword family (D-009). The function
        # form is caught by the SQL-AST screen; this catches the YAML
        # form before pydantic.
        "agg",
    }
)

DEFERRED_FIELD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "grain",
        "window",
        # S-3 will reject the YAML ``role:`` field once the
        # routing-by-resolved-shape (D-005) classifier replaces the
        # current ``role``-driven planner. Until then ``role:`` is
        # still the way the internal model identifies dimensions vs
        # facts; the user-facing rejection lives in the SQL surface
        # (a ``{role=…}`` reference in an expression) which is caught
        # via _DEFERRED_FUNCTION_NAMES / unknown-construct paths.

        # S-1: ``agg:`` on a field is the deferred ``AGG`` keyword.
        "agg",
    }
)

DEFERRED_DATASET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "filters",  # dataset-level filters with scope propagation
        # ``role:`` follows the same plan as on fields above (S-3).
    }
)

DEFERRED_RELATIONSHIP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "condition",
        "asof",
        "range",
        "temporal",
        # S-1: ``referential_integrity`` is removed in favour of the
        # default LEFT (D-001) join shape; an engine that wants to
        # honour RI must do so as a per-engine optimisation.
        "referential_integrity",
    }
)

DEFERRED_MODEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        # S-1: top-level named-filter section is removed.
        "named_filters",
    }
)

DEFERRED_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "query_filters",
        "reset",
        "grain",
        "filter_context",
        "grouping_sets",
        "rollup",
        "cube",
        "pivot",
    }
)


# ---------------------------------------------------------------------------
# SQL AST deferred constructs
# ---------------------------------------------------------------------------

# Any of these AST classes appearing in a scalar / aggregate expression
# means the author is reaching for a deferred feature. All raise
# E_DEFERRED_KEY_REJECTED.
#
# S-22 (D-028..D-032): ``exp.Window`` is no longer in this set —
# the positive planner now passes valid windows through to codegen.
# ``_check_window_rules`` still runs first and routes nested-window /
# deferred-frame-mode cases to their named Foundation codes; only
# valid windows reach the planner.
_DEFERRED_AST_NODES: Final[tuple[type[exp.Expression], ...]] = (
    exp.Pivot,
    exp.Lateral,
    exp.Cube,
    exp.Rollup,
    exp.GroupingSets,
)

_DEFERRED_AST_NAMES: Final[frozenset[str]] = frozenset(
    cls.__name__ for cls in _DEFERRED_AST_NODES
)

# S-1: function names removed from OSI_SQL_2026 + the Foundation. Any
# of these as a function call ⇒ E_DEFERRED_KEY_REJECTED. Compared
# case-insensitively because SQL is case-insensitive on identifiers.
_DEFERRED_FUNCTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "EXISTS_IN",
        "NOT_EXISTS_IN",  # alias surface; canonicalised by sqlglot
        "ATTR",
        "UNSAFE",
        "AGG",
        "GRAIN_AGG",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_yaml_deferred(document: Any) -> None:
    """Walk a parsed YAML document and reject deferred keys.

    ``document`` is the output of ``yaml.safe_load`` — a ``dict`` rooted
    at ``semantic_model`` or at the model content directly. The function
    descends through known shapes; it never silently accepts a mapping
    it does not understand (unknown top-level keys are caught later by
    pydantic and surface as ``E1001``).
    """
    root = unwrap_model_root(document)
    _check_deferred(
        mapping=root,
        banned=DEFERRED_MODEL_KEYS,
        location="semantic_model",
    )
    for ds in _as_list(root.get("datasets")):
        _check_dataset_deferred(ds)
    for rel in _as_list(root.get("relationships")):
        _check_deferred(
            mapping=rel,
            banned=DEFERRED_RELATIONSHIP_KEYS,
            location=f"relationship {rel.get('name', '?')!r}",
        )
    for metric in _as_list(root.get("metrics")):
        _check_deferred(
            mapping=metric,
            banned=DEFERRED_METRIC_KEYS,
            location=f"metric {metric.get('name', '?')!r}",
        )


def check_expression_deferred(expression: FrozenSQL, *, where: str) -> None:
    """Reject deferred SQL constructs in an expression AST."""
    # S-12: window analysis runs first so we can route specific
    # window-rule violations to their named codes before the blanket
    # rejection fires for "valid" windows that the planner does not
    # yet implement.
    _check_window_rules(expression, where=where)
    for node in expression.expr.walk():
        ast = _unwrap_walk_item(node)
        if isinstance(ast, _DEFERRED_AST_NODES):
            raise OSIParseError(
                ErrorCode.E_DEFERRED_KEY_REJECTED,
                (
                    f"{where} uses deferred SQL construct "
                    f"{type(ast).__name__}; see specs/deferred/README.md"
                ),
                context={
                    "where": where,
                    "construct": type(ast).__name__,
                    "expression": expression.canonical,
                },
            )
        # S-1: deferred function calls (EXISTS_IN, ATTR, UNSAFE, AGG,
        # GRAIN_AGG). These parse as ``exp.Anonymous`` nodes (sqlglot's
        # catch-all for "unknown function") whose ``this`` is the
        # function name.
        if isinstance(ast, exp.Anonymous):
            fn_name = (ast.this or "").upper()
            if fn_name in _DEFERRED_FUNCTION_NAMES:
                raise OSIParseError(
                    ErrorCode.E_DEFERRED_KEY_REJECTED,
                    (
                        f"{where} uses deferred SQL function "
                        f"{fn_name}; see specs/deferred/README.md"
                    ),
                    context={
                        "where": where,
                        "construct": fn_name,
                        "expression": expression.canonical,
                    },
                )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _check_window_rules(expression: FrozenSQL, *, where: str) -> None:
    """Apply S-12 window-rejection rules before the blanket deferred check.

    Order matters: D-031 (nested) and D-032 (frame mode) raise their
    own named codes; if neither fires we let the caller's blanket
    rejection handle the still-unimplemented positive case.
    """
    from osi.planning.windows import (  # local import — avoids planning→parsing cycle
        first_deferred_frame_clause,
        first_nested_window,
    )

    nested = first_nested_window(expression.expr)
    if nested is not None:
        raise OSIParseError(
            ErrorCode.E_NESTED_WINDOW,
            (
                f"{where} contains a window function whose argument or "
                "frame contains another window function "
                "(D-031 — nested windows are not in Foundation v0.1)"
            ),
            context={
                "where": where,
                "expression": expression.canonical,
            },
        )
    deferred_frame = first_deferred_frame_clause(expression.expr)
    if deferred_frame is not None:
        _, reason = deferred_frame
        raise OSIParseError(
            ErrorCode.E_DEFERRED_FRAME_MODE,
            (
                f"{where} uses {reason} which is deferred from "
                "Foundation v0.1 (D-032 — only literal ROWS / RANGE "
                "frames are accepted)"
            ),
            context={
                "where": where,
                "reason": reason,
                "expression": expression.canonical,
            },
        )


def _check_dataset_deferred(dataset: Any) -> None:
    if not isinstance(dataset, dict):
        return
    name = dataset.get("name", "?")
    _check_deferred(
        mapping=dataset,
        banned=DEFERRED_DATASET_KEYS,
        location=f"dataset {name!r}",
    )
    for field in _as_list(dataset.get("fields")):
        _check_deferred(
            mapping=field,
            banned=DEFERRED_FIELD_KEYS,
            location=f"field {_fq(name, field)!r}",
        )
    for metric in _as_list(dataset.get("metrics")):
        _check_deferred(
            mapping=metric,
            banned=DEFERRED_METRIC_KEYS,
            location=f"metric {_fq(name, metric)!r}",
        )


def _check_deferred(*, mapping: Any, banned: Iterable[str], location: str) -> None:
    if not isinstance(mapping, dict):
        return
    for key in mapping:
        if key in banned:
            raise OSIParseError(
                ErrorCode.E_DEFERRED_KEY_REJECTED,
                (
                    f"{location} uses deferred field {key!r}; "
                    "see specs/deferred/README.md"
                ),
                context={"location": location, "field": key},
            )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _fq(dataset_name: Any, member: Any) -> str:
    inner = member.get("name", "?") if isinstance(member, dict) else "?"
    return f"{dataset_name}.{inner}"


def _unwrap_walk_item(item: Any) -> exp.Expression:
    """Normalize SQLGlot ``walk()`` items across versions.

    Different SQLGlot releases yield either an :class:`exp.Expression`
    directly or a ``(node, parent, key)`` tuple; this helper collapses
    both shapes to the bare expression.
    """
    if isinstance(item, exp.Expression):
        return item
    if isinstance(item, tuple) and item and isinstance(item[0], exp.Expression):
        return item[0]
    return exp.Expression()  # defensive — no match ⇒ benign Expression()


__all__ = [
    "DEFERRED_DATASET_KEYS",
    "DEFERRED_FIELD_KEYS",
    "DEFERRED_METRIC_KEYS",
    "DEFERRED_MODEL_KEYS",
    "DEFERRED_QUERY_KEYS",
    "DEFERRED_RELATIONSHIP_KEYS",
    "check_expression_deferred",
    "check_yaml_deferred",
]
