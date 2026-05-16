"""Pre-classification AST rewrites.

A semantic query's ``where`` / ``having`` may contain two kinds of
references that need to be resolved before the planner sees them as
ordinary predicates:

1. **Parameter placeholders** — ``:param_name`` (``sqlglot.exp.Placeholder``
   nodes) bound to values supplied on :class:`~osi.planning.SemanticQuery`.
2. **Named-filter references** — bare column-shaped references (e.g.
   ``completed_orders``) whose name matches a
   :class:`~osi.parsing.models.NamedFilter` declared on the model.

Both are pure AST → AST rewrites and live outside the algebra. Running
them once up-front keeps :mod:`~osi.planning.classify` focused on its
real job (row-level vs. semi-join vs. post-aggregate splitting).

Foundation scope (``Proposed_OSI_Semantics.md §4.6`` and §5.1):

* Parameter values are literals. The :class:`Parameter.data_type` field
  is checked lightly — we do not coerce Python objects into SQL types
  here; sqlglot's :func:`~sqlglot.expressions.convert` does that for
  the common cases (numbers, strings, booleans).
* Named filters are **reusable boolean predicates**. Their inlined form
  behaves exactly as if the caller had pasted the filter's expression
  in place.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.models import NamedFilter, Parameter


def substitute_parameters(
    expr: FrozenSQL | None,
    *,
    provided: Mapping[Identifier, object],
    declared: Sequence[Parameter],
) -> FrozenSQL | None:
    """Replace every ``:name`` placeholder in ``expr`` with a SQL literal.

    Validation:

    * Every name in ``provided`` must match a declared parameter
      (``E2002``).
    * Every placeholder must resolve to either a provided value or the
      parameter's declared ``default``; otherwise ``E1002`` (no value).
    """
    declared_by_name = {p.name: p for p in declared}
    _validate_provided_names(provided, declared_by_name)
    if expr is None:
        return None

    placeholders_present = any(isinstance(n, exp.Placeholder) for n in expr.expr.walk())
    if not placeholders_present and not provided:
        return expr

    def _rewrite(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Placeholder) or node.this is None:
            return node
        name = normalize_identifier(str(node.this))
        param = declared_by_name.get(name)
        if param is None:
            raise OSIPlanningError(
                ErrorCode.E2002_NAME_NOT_FOUND,
                f"parameter {name!r} is referenced but not declared on the model",
                context={"parameter": name},
            )
        if name in provided:
            value = provided[name]
        elif param.default is not None:
            value = param.default
        else:
            raise OSIPlanningError(
                ErrorCode.E1002_MISSING_REQUIRED_FIELD,
                f"parameter {name!r} has no value and no default",
                context={"parameter": name},
            )
        return exp.convert(value)

    rewritten = expr.expr.copy().transform(_rewrite)
    return FrozenSQL.of(rewritten)


def inline_named_filters(
    expr: FrozenSQL | None,
    *,
    filters: Sequence[NamedFilter],
    field_names: frozenset[Identifier],
) -> FrozenSQL | None:
    """Inline bare :class:`NamedFilter` references in ``expr``.

    A bare column reference (``sqlglot.exp.Column`` with no ``table``)
    whose name matches both a field and a named filter is left alone —
    the field wins so there's no silent semantic change. Authors with
    such a collision must use ``model.filter.<name>`` at the SQL
    surface (out of scope today; we simply do not rewrite, and the
    normal field lookup path runs).
    """
    by_name = {f.name: f for f in filters}
    if expr is None or not by_name:
        return expr

    def _rewrite(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column) or node.table:
            return node
        name = normalize_identifier(node.name)
        if name not in by_name or name in field_names:
            return node
        return by_name[name].expression.expr.copy()

    rewritten = expr.expr.copy().transform(_rewrite)
    return FrozenSQL.of(rewritten)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_provided_names(
    provided: Mapping[Identifier, object],
    declared_by_name: Mapping[Identifier, Parameter],
) -> None:
    unknown = [k for k in provided if k not in declared_by_name]
    if unknown:
        raise OSIPlanningError(
            ErrorCode.E2002_NAME_NOT_FOUND,
            f"unknown parameter(s): {sorted(str(n) for n in unknown)}",
            context={
                "provided": sorted(str(n) for n in provided),
                "declared": sorted(str(n) for n in declared_by_name),
            },
        )


__all__ = ["inline_named_filters", "substitute_parameters"]
