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

"""Reading the structure of an Ossie metric expression.

Cube expects a measure to *be* an aggregation -- `type: sum` over a column -- and
falls back to a calculated `type: number` measure whose sql carries the whole
aggregate. A composite Ossie metric such as

    SUM(store_sales.amount) / COUNT(DISTINCT customer.id)

can be emitted either way, and the difference matters: as one calculated measure
Cube sees a single opaque expression, whereas as two `public: false` measures on
their own cubes plus a ratio referencing them, **Cube applies its row-multiplication
correction to each aggregate independently**. So decomposition is a correctness
improvement for cross-dataset metrics, not a formatting choice.

Locating the aggregate calls is done with sqlglot rather than a regex, since an
expression can nest them (`SUM(x) / NULLIF(SUM(y), 0)`) and string matching cannot
tell a top-level call from one inside another argument. sqlglot is already a runtime
dependency of the dbt and NVIDIA GSF converters for the same purpose.
"""

import sqlglot
import sqlglot.expressions as exp

from ._common import quoted_char_mask

# sqlglot node types for the aggregates this converter maps to a Cube measure type.
# `Count` covers COUNT / COUNT(DISTINCT x); ApproxDistinct covers
# APPROX_COUNT_DISTINCT.
_AGGREGATE_NODES = (
    exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count, exp.ApproxDistinct,
)


def parse(expr):
    """Parse an Ossie expression, or None when sqlglot cannot.

    An unparseable expression is not an error: the converter falls back to treating
    it as one opaque calculated measure, which is what it did for everything before.
    """
    try:
        return sqlglot.parse_one(str(expr).strip())
    except Exception:
        return None


def is_single_aggregate(expr):
    """True if the whole expression is exactly one aggregate call.

    Those already map to a structured Cube measure (`type: sum` + `sql`), so they
    are never decomposed.
    """
    tree = parse(expr)
    return tree is not None and isinstance(tree, _AGGREGATE_NODES)


# The aggregate call names this converter maps to a Cube measure type. Scanned for
# in the source text: sqlglot renames some when it renders (`APPROX_COUNT_DISTINCT`
# comes back as `APPROX_DISTINCT`), and two calls of the same name render
# identically, so node text cannot be used to find them in the original string.
_AGGREGATE_NAMES = (
    "APPROX_COUNT_DISTINCT", "APPROX_DISTINCT",
    "COUNT", "SUM", "AVG", "MIN", "MAX",
)


def aggregate_spans(expr):
    """The outermost aggregate calls in `expr`, as (start, end) offsets.

    Offsets index the original string so a caller can substitute each span in place.
    That matters because the surrounding text may carry Cube `{...}` references,
    which sqlglot would not reproduce verbatim if the expression were re-rendered.

    Spans are found by scanning for an aggregate name followed by a balanced
    parenthesis group, then confirmed with sqlglot -- which is also what rules out a
    malformed expression. Nesting is resolved on the offsets themselves: a span
    inside another span is not returned, so `SUM(x) / NULLIF(SUM(y), 0)` gives two
    and `SUM(SUM(x))` gives one. Returns [] when the expression does not parse, or is
    itself a single aggregate needing no decomposition.

    A name inside a string literal is not a call: `SUM(x) || ' per COUNT(y) unit'`
    has one aggregate, not two. Taking the second would splice a measure reference
    into the literal.
    """
    text = str(expr)
    if parse(text) is None or is_single_aggregate(text):
        return []
    return _scan_aggregates(text)


def _scan_aggregates(text):
    """Every outermost aggregate call in `text`, as (start, end) offsets."""
    if parse(text) is None:
        return []
    candidates = []
    upper = text.upper()
    quoted = quoted_char_mask(text)
    for name in _AGGREGATE_NAMES:
        at = 0
        while True:
            at = upper.find(name, at)
            if at < 0:
                break
            start, after = at, at + len(name)
            at = after
            if quoted[start]:
                continue
            # A call, not part of a longer identifier: boundary before, `(` after.
            if start and (text[start - 1].isalnum() or text[start - 1] == "_"):
                continue
            probe = after
            while probe < len(text) and text[probe].isspace():
                probe += 1
            if probe >= len(text) or text[probe] != "(":
                continue
            close = _match_paren(text, probe)
            if close is None:
                continue
            end = close + 1
            # Confirm the slice really is an aggregate and not, say, a UDF that
            # happens to share a prefix.
            node = parse(text[start:end])
            if isinstance(node, _AGGREGATE_NODES):
                candidates.append((start, end))

    # Drop any span contained within another: only the outermost becomes a measure.
    candidates.sort()
    out = []
    for start, end in candidates:
        if any(s <= start and end <= e for s, e in out):
            continue
        out.append((start, end))
    return out


def _match_paren(text, open_at):
    """Index of the `)` closing the `(` at `open_at`, honouring quotes."""
    depth, quote = 0, None
    for i in range(open_at, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


# The only aggregates whose value survives duplicate input rows. Everything else is
# treated as unsafe -- an allowlist rather than a blocklist, because the set of
# aggregate functions is open-ended (STDDEV, VARIANCE, MEDIAN, ARRAY_AGG, PERCENTILE...)
# and listing the unsafe ones meant every unlisted one was silently declared safe.
_IDEMPOTENT_NODES = (exp.Min, exp.Max, exp.ApproxDistinct)


def is_idempotent_aggregate(node):
    """True if duplicating input rows cannot change this aggregate's value."""
    if isinstance(node, _IDEMPOTENT_NODES):
        return True
    # DISTINCT collapses duplicates before the aggregate sees them, so *any* aggregate
    # over a distinct set is duplication-invariant -- `SUM(DISTINCT ltv)` as much as
    # `COUNT(DISTINCT id)`. Honouring it only for COUNT rejected the others in strict
    # mode over a value fan-out cannot change.
    return _aggregates_distinct(node)


def _aggregates_distinct(node):
    """True when this aggregate is applied to a DISTINCT set."""
    if isinstance(node.this, exp.Distinct):
        return True
    if node.args.get("distinct"):
        return True
    # sqlglot may hang the DISTINCT off the function's argument list instead.
    return any(isinstance(arg, exp.Distinct)
               for arg in (node.args.get("expressions") or []))


def unsafe_aggregate_datasets(expr):
    """Which datasets each non-idempotent aggregate in `expr` reads.

    Returns `(datasets, unqualified)` -- the dataset names appearing inside an unsafe
    aggregate, and whether any unsafe aggregate named none (so it reads the cube the
    measure is declared on). Returns None when the expression does not parse, leaving the
    caller to be conservative.

    Walks the parse tree rather than matching aggregate names in the text, so an
    aggregate this converter has no Cube mapping for -- STDDEV, MEDIAN, ARRAY_AGG -- is
    attributed like any other. Scanning for known names meant one recognized aggregate
    was enough to stop the search, and an unrecognized one elsewhere in the same
    expression went unattributed: `SUM(orders.amount) + STDDEV(users.ltv)` reported only
    `orders`.
    """
    tree = parse(expr)
    if tree is None:
        return None
    datasets, unqualified = set(), False
    for node in tree.walk():
        if not isinstance(node, exp.AggFunc) or is_idempotent_aggregate(node):
            continue
        tables = {column.table for column in node.find_all(exp.Column)
                  if column.table}
        if tables:
            datasets |= tables
        else:
            unqualified = True
    return datasets, unqualified


def has_top_level_operator(expr):
    """True if `expr` is not a single self-contained term.

    Used to decide whether inlining it back into a larger expression needs
    parentheses: a lone `SUM(x)` does not, `SUM(x) / 2` does.
    """
    depth, quote = 0, None
    for ch in str(expr):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and (ch in "+-*/%<>=|&" or ch.isspace()):
            # Whitespace at depth 0 also implies structure (`CASE WHEN ...`).
            return True
    return False
