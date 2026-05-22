"""OSI Foundation v0.1 Python reference implementation.

The happy-path symbols an adopter needs are re-exported at the package
root so ``from osi import …`` works without digging through
subpackages:

    from osi import (
        parse_semantic_model,
        plan,
        compile_plan,
        Dialect,
        SemanticQuery,
        Reference,
        PlannerContext,
        ErrorCode,
        OSIError,
    )

End-to-end example:

    >>> from osi import (
    ...     parse_semantic_model,
    ...     plan,
    ...     compile_plan,
    ...     PlannerContext,
    ...     SemanticQuery,
    ...     Reference,
    ...     Dialect,
    ... )
    >>> # 1. Parse a model file (or YAML string).
    >>> # result = parse_semantic_model(Path("model.yaml"))
    >>> # 2. Build a planner context.
    >>> # ctx = PlannerContext(
    >>> #     model=result.model,
    >>> #     namespace=result.namespace,
    >>> #     graph=result.graph,
    >>> # )
    >>> # 3. Build a query and plan it.
    >>> # plan_ = plan(query, ctx)
    >>> # 4. Render SQL.
    >>> # sql = compile_plan(plan_, dialect=Dialect.DUCKDB)

Internals (algebra operators, plan-builder, classifier helpers) live
in ``osi.parsing``, ``osi.planning``, ``osi.codegen``, and
``osi.diagnostics`` and remain accessible for power users and
extension authors. The top-level façade is intentionally narrow: when
in doubt, prefer the façade.

See ``SPEC.md`` and ``ARCHITECTURE.md`` at the project root for the
contract, and the top-level ``README.md`` for a runnable quick-start
example. The normative standard text lives one repository level up at
``../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md``.
"""

from __future__ import annotations

from osi.codegen import Dialect, compile_plan
from osi.errors import ErrorCode, OSIError
from osi.parsing.parser import parse_semantic_model
from osi.planning import Reference, SemanticQuery, plan
from osi.planning.planner_context import PlannerContext

__version__ = "0.1.0"

__all__ = [
    "Dialect",
    "ErrorCode",
    "OSIError",
    "PlannerContext",
    "Reference",
    "SemanticQuery",
    "__version__",
    "compile_plan",
    "parse_semantic_model",
    "plan",
]
