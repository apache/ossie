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

"""Metric expression rewriting between Solid and Apache Ossie conventions.

Solid stores a metric formula against **bare** column names and records the owning table
separately (`tables: [shop.sales.orders]`); solid-server's `is_valid_metric_formula`
actively strips alias prefixes before saving. Apache Ossie instead expects a metric to be
self-contained, qualifying each column with its dataset
(`SUM(store_sales.ss_ext_sales_price)`), so the two directions here add and remove that
qualifier.

The rewrite is a **surgical splice**, not a re-render. Every column occurrence is located
by sqlglot's tokenizer, which reports character positions and keeps string literals and
quoted identifiers in token types of their own, and the qualifier is inserted or removed
at those offsets. Everything else in the expression is left character-for-character
intact.

That matters because round-tripping a parsed tree through `Expression.sql()` does not
return the author's SQL -- sqlglot canonicalizes as it generates, so
`CAST(x AS FLOAT)` comes back as `CAST(x AS DOUBLE)` and
`EXTRACT(year FROM d)` as `DATE_PART(YEAR, d)`. Those are semantically equal and
textually different, and a converter has no business rewriting SQL it was only asked to
qualify.

The parser is still used, as a cross-check: it decides which names are genuinely bare
column references, and a token scan that disagrees with it means some occurrence is
something else (`EXTRACT(year FROM ...)` where a column is also called `year`). On any
disagreement the expression is left exactly as written -- an unqualified metric is a far
smaller problem than a corrupted one.
"""

from collections import Counter

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from ._common import SQLGLOT_DIALECTS

# Token types that can carry an identifier: an unquoted name, or a quoted one whose
# offsets span the quote characters as well.
_NAME_TOKENS = (TokenType.VAR, TokenType.IDENTIFIER)

# Conversion outcomes, returned alongside the expression.
QUALIFIED = "qualified"
UNQUALIFIED = "unqualified"
UNCHANGED = "unchanged"
UNPARSED = "unparsed"
AMBIGUOUS = "ambiguous"


def _read(dialect):
    """Apache Ossie dialect name -> the sqlglot dialect to parse/tokenize with."""
    return SQLGLOT_DIALECTS.get(dialect)


def _parse(expression, dialect):
    """Parse a scalar/aggregate expression, or return None if it does not parse.

    Metric expressions are fragments rather than statements, so `parse_one` is used
    directly; a fragment sqlglot has no grammar for yields None and the caller leaves the
    text alone.
    """
    try:
        return sqlglot.parse_one(expression, read=_read(dialect)) or None
    except (SqlglotError, ValueError, RecursionError):
        return None


def _tokenize(expression, dialect):
    """Tokenize an expression, or return None if it cannot be tokenized."""
    try:
        return sqlglot.tokenize(expression, read=_read(dialect))
    except (SqlglotError, ValueError, RecursionError):
        return None


def _unquote(text):
    """Strip the quoting a tokenizer may have left on an identifier's text."""
    return text.strip().strip('"`[]')


def column_reference(name, dialect):
    """Render a column name as an Apache Ossie field expression.

    A Solid field's expression is just its own column, so the usual result is the bare
    name. A name that is not a plain identifier -- one with spaces, or one that collides
    with a reserved word -- is quoted with the target dialect's rules so the expression
    stays valid SQL.
    """
    if not isinstance(name, str) or not name.strip():
        return name
    bare = exp.to_identifier(name, quoted=False)
    if bare is not None:
        rendered = bare.sql(dialect=_read(dialect))
        parsed = _parse(rendered, dialect)
        # The bare form is usable only if it reads back as the same single column; a
        # reserved word parses as some other node type, or not at all.
        if isinstance(parsed, exp.Column) and parsed.name == name and not parsed.table:
            return rendered
    quoted = exp.to_identifier(name, quoted=True)
    return quoted.sql(dialect=_read(dialect)) if quoted is not None else name


def _bare_columns(tree, known):
    """Count, per name, the bare column references the AST reports.

    Only names in `known` are counted, since only those are candidates for qualification.
    """
    counts = Counter()
    for column in tree.find_all(exp.Column):
        if column.table:
            continue
        name = column.name.lower()
        if name in known:
            counts[name] += 1
    return counts


def _qualified_columns(tree, known):
    """Count, per (qualifier, name), the column references already qualified by a name in
    `known`. A three-part reference (`db.table.column`) is excluded: its qualifier is a
    schema, not a dataset."""
    counts = Counter()
    for column in tree.find_all(exp.Column):
        table = column.args.get("table")
        if table is None or column.args.get("db"):
            continue
        if column.table.lower() in known:
            counts[(column.table.lower(), column.name.lower())] += 1
    return counts


def _splice(expression, edits):
    """Apply (start, end, replacement) edits to `expression`, right to left.

    `end` is exclusive. Applying in reverse order keeps earlier offsets valid.
    """
    result = expression
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def qualify_metric(expression, dialect, dataset, columns):
    """Prefix every bare column in `expression` with `dataset`.

    `columns` is the set of column names the dataset owns, compared case-insensitively
    (Solid models routinely mix `ACCOUNT_ID` in the column list with `account_id` in the
    formula). A column that already carries a qualifier is left as the author wrote it.

    Returns (expression, status):
      "qualified"  -- at least one column was prefixed, by splicing into the original text
      "unchanged"  -- nothing needed prefixing; the text is returned verbatim
      "unparsed"   -- sqlglot could not read it; the text is returned verbatim
      "ambiguous"  -- the token scan and the parse disagree on which names are columns,
                      so the text is returned verbatim rather than risk a bad edit
    """
    if not expression or not expression.strip():
        return expression, UNCHANGED
    known = {c.lower() for c in columns}
    if not known:
        return expression, UNCHANGED

    tree = _parse(expression, dialect)
    tokens = _tokenize(expression, dialect)
    if tree is None or tokens is None:
        return expression, UNPARSED

    expected = _bare_columns(tree, known)
    if not expected:
        return expression, UNCHANGED

    targets = []
    for index, token in enumerate(tokens):
        if token.token_type not in _NAME_TOKENS:
            continue
        if _unquote(token.text).lower() not in known:
            continue
        previous = tokens[index - 1] if index else None
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        # Already qualified (`t.col`), itself a qualifier (`col.x`), or a function call
        # (`col(...)`) -- none of these is a bare column reference.
        if previous is not None and previous.token_type is TokenType.DOT:
            continue
        if following is not None and following.token_type in (
                TokenType.DOT, TokenType.L_PAREN):
            continue
        targets.append(token)

    if Counter(_unquote(t.text).lower() for t in targets) != expected:
        return expression, AMBIGUOUS

    prefix = f"{column_reference(dataset, dialect)}."
    return _splice(
        expression,
        [(t.start, t.start, prefix) for t in targets],
    ), QUALIFIED


def unqualify_metric(expression, dialect, datasets):
    """Strip a dataset qualifier off every column in `expression`.

    The inverse of `qualify_metric`, used when exporting back to Solid, whose formulas are
    stored bare. `datasets` is the set of dataset names that may legitimately appear as a
    qualifier; any other qualifier -- a genuine table alias an author wrote by hand -- is
    preserved.

    Returns (expression, status) with the same vocabulary as `qualify_metric`, except that
    a successful edit reports "unqualified".
    """
    if not expression or not expression.strip():
        return expression, UNCHANGED
    known = {d.lower() for d in datasets}
    if not known:
        return expression, UNCHANGED

    tree = _parse(expression, dialect)
    tokens = _tokenize(expression, dialect)
    if tree is None or tokens is None:
        return expression, UNPARSED

    expected = _qualified_columns(tree, known)
    if not expected:
        return expression, UNCHANGED

    edits, found = [], Counter()
    for index, token in enumerate(tokens):
        if token.token_type not in _NAME_TOKENS:
            continue
        if _unquote(token.text).lower() not in known:
            continue
        dot = tokens[index + 1] if index + 1 < len(tokens) else None
        name = tokens[index + 2] if index + 2 < len(tokens) else None
        if dot is None or dot.token_type is not TokenType.DOT:
            continue
        if name is None or name.token_type not in _NAME_TOKENS:
            continue
        previous = tokens[index - 1] if index else None
        # `db.table.column`: the leading name is a schema, so the middle one is not a
        # dataset qualifier this converter added.
        if previous is not None and previous.token_type is TokenType.DOT:
            continue
        after = tokens[index + 3] if index + 3 < len(tokens) else None
        if after is not None and after.token_type is TokenType.DOT:
            continue
        found[(_unquote(token.text).lower(), _unquote(name.text).lower())] += 1
        # Delete the qualifier and its dot; `end` is inclusive on a sqlglot token.
        edits.append((token.start, dot.end + 1, ""))

    if found != expected:
        return expression, AMBIGUOUS
    return _splice(expression, edits), UNQUALIFIED


def referenced_datasets(expression, dialect, datasets):
    """Return the dataset names a metric expression qualifies columns with.

    Used on export to reconstruct Solid's `metrics[].tables` when an Apache Ossie model
    carries no SOLID stash. Order follows `datasets`, so the result is deterministic.
    """
    tree = _parse(expression, dialect)
    if tree is None:
        return []
    seen = set()
    for column in tree.find_all(exp.Column):
        if column.table and not column.args.get("db"):
            seen.add(column.table.lower())
    return [d for d in datasets if d.lower() in seen]

