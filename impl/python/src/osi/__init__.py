"""osi_python — Foundation reference implementation of OSI.

Public entry points:

    from osi.parsing.parser   import parse_semantic_model
    from osi.planning         import SemanticQuery, Reference, plan
    from osi.planning.planner_context import PlannerContext
    from osi.codegen          import Dialect, compile_plan

See ``SPEC.md`` and ``ARCHITECTURE.md`` at the project root for the contract,
and the top-level ``README.md`` for a runnable quick-start example.
"""

__version__ = "0.1.0"
