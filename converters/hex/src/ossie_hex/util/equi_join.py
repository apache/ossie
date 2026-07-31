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

from __future__ import annotations

import re

from ..ossie_types import OSSIE_QUALIFIED_FIELD_EXPR_RE, is_ossie_field


def parse_equi_join(
    join_sql: str,
    *,
    relation_id: str,
    target: str,
) -> tuple[list[str], list[str]] | None:
    """Best-effort parse of simple equi-join Hex ``join_sql``.

    Supports forms like::

        ${order_id} = ${customers.id}
        ${sender_id} = ${sender.id}
        ${a} = ${b} AND ${c} = ${d}

    Returns ``(local_columns, remote_columns)`` relative to the model declaring
    the relation, or ``None`` when the SQL is not a simple conjunction of equalities.
    """
    # Normalize whitespace and split on AND (case-insensitive).
    parts = re.split(r"\s+AND\s+", join_sql.strip(), flags=re.IGNORECASE)
    local_cols: list[str] = []
    remote_cols: list[str] = []

    for part in parts:
        eq = re.match(
            r"^\$\{\s*([^}]+?)\s*\}\s*=\s*\$\{\s*([^}]+?)\s*\}$",
            part.strip(),
            flags=re.IGNORECASE,
        )
        if not eq:
            return None
        left = eq.group(1).strip()
        right = eq.group(2).strip()

        left_col = _column_from_ref(left)
        right_col = _column_from_ref(right)
        if left_col is None or right_col is None:
            return None

        # Decide which side is local (base) vs remote (target).
        left_is_remote = _is_remote_ref(left, relation_id=relation_id, target=target)
        right_is_remote = _is_remote_ref(right, relation_id=relation_id, target=target)
        if left_is_remote == right_is_remote:
            # Ambiguous; treat left as local when neither/both look remote.
            if left_is_remote:
                return None
            local_cols.append(left_col)
            remote_cols.append(right_col)
        elif left_is_remote:
            local_cols.append(right_col)
            remote_cols.append(left_col)
        else:
            local_cols.append(left_col)
            remote_cols.append(right_col)

    return local_cols, remote_cols


def _column_from_ref(ref: str) -> str | None:
    if is_ossie_field(ref):
        return ref
    m = OSSIE_QUALIFIED_FIELD_EXPR_RE.match(ref)
    if m:
        return m.group(2)
    return None


def _is_remote_ref(ref: str, *, relation_id: str, target: str) -> bool:
    m = OSSIE_QUALIFIED_FIELD_EXPR_RE.match(ref)
    if not m:
        return False
    qualifier = m.group(1)
    return qualifier in {relation_id, target}


def synthesize_join_sql(
    *,
    local_columns: list[str],
    remote_columns: list[str],
    relation_id: str,
) -> str:
    """Build Hex ``join_sql`` from column pairs, as :func:`parse_equi_join` reads them.

    The local side belongs to the model declaring the relation.
    """
    if len(local_columns) != len(remote_columns):
        raise ValueError("local_columns and remote_columns must have equal length")
    clauses = [
        f"${{{local}}} = ${{{relation_id}.{remote}}}"
        for local, remote in zip(local_columns, remote_columns, strict=True)
    ]
    return " AND ".join(clauses)
