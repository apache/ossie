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
from collections.abc import Callable

from ..hex_types import HEX_REF_RE
from ..ossie_types import (
    OSSIE_QUALIFIED_FIELD_EXPR_RE,
    OSSIE_QUALIFIED_FIELD_EXPR_SCAN_RE,
    is_ossie_field,
)

# Maps an Ossie ``(dataset, field)`` pair to the body of the Hex ``${...}`` ref
# that reaches it, or ``None`` when Hex has no way to address it.
RefResolver = Callable[[str, str], "str | None"]


def qualify_hex_ref(ref: str, *, model: str | None = None) -> str:
    """Turn the body of a Hex reference into an Ossie SQL identifier.

    - ``field`` → ``field``, or ``model.field`` when ``model`` is given
    - ``relation.field`` → ``relation.field``

    A reference that already names where it reads from is left alone. Ossie
    identifiers are ``dataset.field``, so prefixing one would produce a
    three-part name the spec has nowhere to put.
    """
    body = ref.strip()
    if model and is_ossie_field(body):
        return f"{model}.{body}"
    return body


def hex_refs_to_ossie(sql: str, *, model: str | None = None) -> str:
    """Rewrite Hex ``${dim}`` / ``${rel.dim}`` refs to Ossie SQL identifiers."""

    def _replace(match: re.Match[str]) -> str:
        return qualify_hex_ref(match.group(1), model=model)

    return HEX_REF_RE.sub(_replace, sql)


_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


def ossie_refs_to_hex(sql: str, *, resolve: RefResolver) -> str:
    """Rewrite ``dataset.field`` references inside SQL into Hex ``${...}`` refs.

    The inverse of :func:`hex_refs_to_ossie` for expressions too complex for
    :func:`ossie_expr_to_hex_refs`. Hex would read the Ossie qualifier in
    ``orders.amount`` as a table name, so it becomes ``${amount}`` or, for
    another model, ``${relation.field}``.

    References ``resolve`` cannot place are left verbatim rather than guessed at,
    and string literals are never rewritten.
    """

    def _rewrite(fragment: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            body = resolve(match.group(1), match.group(2))
            return "${" + body + "}" if body is not None else match.group(0)

        return OSSIE_QUALIFIED_FIELD_EXPR_SCAN_RE.sub(_replace, fragment)

    out: list[str] = []
    pos = 0
    for literal in _STRING_LITERAL_RE.finditer(sql):
        out.append(_rewrite(sql[pos : literal.start()]))
        out.append(literal.group(0))
        pos = literal.end()
    out.append(_rewrite(sql[pos:]))
    return "".join(out)


def ossie_expr_to_hex_refs(sql: str, *, model: str, resolve: RefResolver) -> str:
    """Rewrite a whole-expression Ossie identifier into a Hex ``${...}`` ref.

    Conservative: only rewrites an expression that is a single ``field`` or
    ``dataset.field``. More complex SQL is returned unchanged for the caller to
    hand to :func:`ossie_refs_to_hex`.
    """
    ref = resolve_field_ref(sql, model=model, resolve=resolve)
    return "${" + ref + "}" if ref is not None else sql


def rebuild_hex_expr_sql(
    ossie_sql: str,
    *,
    model: str,
    field: str,
    dimension_id: str,
    resolve: RefResolver,
) -> str | None:
    """Rebuild a dimension's Hex ``expr_sql`` from its Ossie expression.

    Returns ``None`` when the expression only names the field itself, which is
    the dimension Hex writes without an ``expr_sql`` at all.

    Both directions call this. The import calls it to do the rewrite. The export
    calls it to ask whether the expression it is about to write would come back
    as authored, and records the expression in a custom extension only when it
    would not. Asking one function keeps the two from disagreeing about what
    survives.
    """
    hex_expr = ossie_expr_to_hex_refs(ossie_sql, model=model, resolve=resolve)
    addresses_itself = {
        "${" + dimension_id + "}",
        dimension_id,
        field,
        f"{model}.{field}",
    }
    if hex_expr in addresses_itself or ossie_sql.strip() in addresses_itself:
        return None
    if hex_expr.startswith("${"):
        return hex_expr
    return ossie_refs_to_hex(ossie_sql, resolve=resolve)


def resolve_field_ref(expr: str, *, model: str, resolve: RefResolver) -> str | None:
    """Return the Hex reference when ``expr`` addresses exactly one field.

    Accepts a bare ``field`` on the owning dataset or a ``dataset.field`` pair
    that ``resolve`` can reach. Anything else -- a computed expression, an
    unreachable dataset, or the unbalanced fragment left behind by a regex that
    matched across two aggregates -- yields ``None``.
    """
    text = expr.strip()
    if is_ossie_field(text):
        return resolve(model, text)
    m = OSSIE_QUALIFIED_FIELD_EXPR_RE.match(text)
    if m:
        return resolve(m.group(1), m.group(2))
    return None
