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

"""Rewrite Hex ``${...}`` references to/from Ossie plain SQL expressions."""

from __future__ import annotations

import re

from .hex_models import HEX_REF_RE
from .ossie_models import OSSIE_QUALIFIED_FIELD_EXPR_RE, is_ossie_field


def hex_refs_to_ossie(sql: str, *, model: str | None = None) -> str:
    """Rewrite Hex ``${dim}`` / ``${rel.dim}`` refs to Ossie SQL identifiers.

    - ``${field}`` → ``field`` (or ``model.field`` when ``model`` is given
      and the body is a simple identifier)
    - ``${relation.field}`` → ``relation.field``
    """

    def _replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        if is_ossie_field(body):
            if model:
                return f"{model}.{body}"
            return body
        return body

    return HEX_REF_RE.sub(_replace, sql)


def ossie_expr_to_hex_refs(sql: str, *, model: str | None = None) -> str:
    """Rewrite bare / qualified Ossie identifiers toward Hex ``${...}`` refs.

    Conservative: only rewrites a whole expression that is a single identifier
    or ``model.field``. More complex SQL is returned unchanged (callers may
    still store it as ``func_sql`` / ``expr_sql``).
    """
    text = sql.strip()
    if is_ossie_field(text):
        return "${" + text + "}"
    m = OSSIE_QUALIFIED_FIELD_EXPR_RE.match(text)
    if m:
        left, right = m.group(1), m.group(2)
        if model and left == model:
            return "${" + right + "}"
        return "${" + left + "." + right + "}"
    return sql


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

    Returns ``(from_columns, to_columns)`` relative to Ossie relationship
    orientation (caller applies cardinality inversion), or ``None`` when the
    SQL is not a simple conjunction of equalities.
    """
    # Normalize whitespace and split on AND (case-insensitive).
    parts = re.split(r"\s+AND\s+", join_sql.strip(), flags=re.IGNORECASE)
    from_cols: list[str] = []
    to_cols: list[str] = []

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
            from_cols.append(left_col)
            to_cols.append(right_col)
        elif left_is_remote:
            from_cols.append(right_col)
            to_cols.append(left_col)
        else:
            from_cols.append(left_col)
            to_cols.append(right_col)

    return from_cols, to_cols


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
    from_columns: list[str],
    to_columns: list[str],
    relation_id: str,
) -> str:
    """Build Hex ``join_sql`` from Ossie column pairs."""
    if len(from_columns) != len(to_columns):
        raise ValueError("from_columns and to_columns must have equal length")
    clauses = [
        f"${{{local}}} = ${{{relation_id}.{remote}}}"
        for local, remote in zip(from_columns, to_columns, strict=True)
    ]
    return " AND ".join(clauses)
