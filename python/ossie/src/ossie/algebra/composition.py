# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Scalar-composition operators: ``add_columns`` and ``broadcast``.

Extracted from :mod:`ossie.algebra.operations` to keep the
per-file line budget (``ARCHITECTURE.md``, 600 lines). Both operators
preserve grain and only add columns, so they are grouped together here:

* ``add_columns`` — derive new scalar columns from existing ones
  (composite metrics: ratios of aggregates, etc.).
* ``broadcast`` — attach a scalar (grain-``frozenset()``) state as
  a column on every row of a non-scalar state.

Mutation-score target: **≥ 90%** (same as ``operations.py``). A
surviving mutation in these functions is a P0 bug.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ossie.algebra.state import CalculationState, Column, ColumnKind
from ossie.common.identifiers import Identifier
from ossie.errors import AlgebraError, ErrorCode


def add_columns(
    state: CalculationState, definitions: Sequence[Column]
) -> CalculationState:
    """Introduce derived scalar columns (no aggregation).

    In the Foundation this operator is wired to exactly one planner
    path — **composite metrics** (``core-spec/foundational_semantics.md §4.5``).
    A measure group that contains composite metrics (e.g. ratios of
    two aggregates) is lowered to an ``AGGREGATE`` step over the base
    aggregates followed by one ``ADD_COLUMNS`` step per composite.

    Preconditions
    -------------
    * every definition's ``kind`` is ``DIMENSION`` or ``FACT`` (no
      aggregates — see :func:`aggregate` for those, or emit the
      aggregate step first and reference it from here)
    * every definition's ``dependencies`` are known to ``state``
    * new column names do not collide with existing names

    Grain effect: **preserved**. UKs are preserved (the operator
    only adds columns; existing keys remain keys).
    """
    existing = state.column_names
    new_names: set[Identifier] = set()
    for col in definitions:
        if col.kind is ColumnKind.AGGREGATE:
            raise AlgebraError(
                ErrorCode.E3007_AGGREGATE_IN_SCALAR_CONTEXT,
                f"add_columns cannot introduce AGGREGATE column {col.name!r}",
                context={"column": col.name},
            )
        unknown = col.dependencies - (existing | new_names)
        if unknown:
            raise AlgebraError(
                ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                f"add_columns definition {col.name!r} depends on unknown "
                f"columns: {sorted(unknown)}",
                context={"column": col.name, "missing": sorted(unknown)},
            )
        if col.name in existing or col.name in new_names:
            raise AlgebraError(
                ErrorCode.E3005_COLUMN_NAME_COLLISION,
                f"add_columns definition {col.name!r} collides with existing " "column",
                context={"column": col.name},
            )
        new_names.add(col.name)
    return CalculationState(
        grain=state.grain,
        columns=state.columns + tuple(definitions),
        provenance=state.provenance,
        unique_keys=state.unique_keys,
    )


def broadcast(state: CalculationState, scalar: CalculationState) -> CalculationState:
    """Attach a single scalar value (grain-``frozenset()`` state) as a column.

    **Planner status: reserved.** ``broadcast`` is part of the closed
    algebra so scalar-per-row attach semantics have a stable operator,
    but today's planner
    never emits a :attr:`~ossie.planning.plan.PlanOperation.BROADCAST`
    step. Percent-of-total style calculations go through composite
    metrics + :func:`add_columns` instead. This function, and its
    ``E4004_BROADCAST_NOT_SCALAR`` precondition, remain so a future
    sprint can turn it on without a SPEC change — and so the algebra
    stays closed under the nine declared operators.

    Preconditions
    -------------
    * ``scalar.is_scalar`` (``grain == frozenset()``)
    * ``scalar`` has exactly one column
    * that column's name does not already exist in ``state``

    Grain effect: **preserved**. UKs are preserved (the operator
    only adds a single column; existing keys remain keys).
    """
    if not scalar.is_scalar:
        raise AlgebraError(
            ErrorCode.E4004_BROADCAST_NOT_SCALAR,
            "broadcast requires a scalar state (grain == frozenset())",
            context={"scalar_grain": sorted(scalar.grain)},
        )
    if len(scalar.columns) != 1:
        raise AlgebraError(
            ErrorCode.E4004_BROADCAST_NOT_SCALAR,
            "broadcast requires exactly one scalar column",
            context={"columns": [c.name for c in scalar.columns]},
        )
    new_col = scalar.columns[0]
    if new_col.name in state.column_names:
        raise AlgebraError(
            ErrorCode.E3005_COLUMN_NAME_COLLISION,
            f"broadcast column {new_col.name!r} collides with existing column",
            context={"column": new_col.name},
        )
    tagged = replace(new_col, is_single_valued=True)
    return CalculationState(
        grain=state.grain,
        columns=state.columns + (tagged,),
        provenance=state.provenance | scalar.provenance,
        unique_keys=state.unique_keys,
    )


__all__ = ["add_columns", "broadcast"]
