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

import re

import sqlglot
import sqlglot.expressions as exp

from ._common import quoted_char_mask, sub_outside_quotes

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
    mask = quoted_char_mask(text)
    depth = 0
    for i in range(open_at, len(text)):
        if mask[i]:
            continue
        ch = text[i]
        if ch == "(":
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
_IDEMPOTENT_NODES = (
    exp.Min, exp.Max, exp.ApproxDistinct,
    # BOOL_OR / BOOL_AND: a duplicated row cannot change whether *any* or *all* rows
    # satisfy the predicate.
    exp.LogicalOr, exp.LogicalAnd,
)

# Aggregates sqlglot leaves as an unmodelled call (`Anonymous`) but which duplication
# cannot affect either. Bitwise OR/AND of a value set is idempotent for the same reason
# the logical ones are.
_IDEMPOTENT_CALLS = frozenset({"BIT_OR", "BIT_AND", "BOOL_OR", "BOOL_AND"})


def _is_aggregate_scope(node):
    """True for a node that constitutes one aggregate, whatever shape sqlglot gave it.

    Three shapes, all of which have to count:
    - `AggFunc`, the modelled aggregates (SUM, COUNT, PERCENTILE_CONT, ...);
    - `WithinGroup`, an *ordered-set* aggregate -- the value-bearing column lives in the
      ORDER BY, on the wrapper rather than on the inner function, so examining only the
      inner one attributed `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY users.ltv)` to
      the declaring cube instead of `users`;
    - `Anonymous`, a call sqlglot does not model at all -- which is how LISTAGG,
      APPROX_PERCENTILE and BIT_OR arrive, and how `LISTAGG(...) WITHIN GROUP (...)`
      vanished from the analysis entirely.

    An `Anonymous` may equally be a scalar UDF, so treating it as an aggregate
    over-reports. That is the cheaper error: a warning by default (the fan-out policy
    warns rather than refuses), against a silently inflated number the other way.
    """
    return isinstance(node, (exp.AggFunc, exp.WithinGroup, exp.Anonymous))


def is_idempotent_aggregate(node):
    """True if duplicating input rows cannot change this aggregate's value."""
    if isinstance(node, exp.WithinGroup):
        # An ordered-set aggregate is exactly as safe as the function being ordered.
        return bool(node.this) and is_idempotent_aggregate(node.this)
    if isinstance(node, exp.Anonymous):
        # DISTINCT applies here for the same reason it does to a modelled aggregate:
        # `LISTAGG(DISTINCT name)` cannot be changed by a duplicated row.
        return (str(node.this or "").upper() in _IDEMPOTENT_CALLS
                or _aggregates_distinct(node))
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
    # sqlglot may hang the DISTINCT off the argument list instead -- and for a call it
    # does not model, `Anonymous.expressions` is where the arguments live.
    arguments = list(node.args.get("expressions") or [])
    if isinstance(node.this, list):
        arguments += node.this
    return any(isinstance(arg, exp.Distinct) for arg in arguments)


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
    for scope in _outermost_aggregate_scopes(tree):
        if is_idempotent_aggregate(scope):
            continue
        columns = list(scope.find_all(exp.Column))
        # Qualified and unqualified operands are tracked *independently*: an aggregate can
        # read both, and `SUM(amount + line_items.qty)` reported only `line_items` while
        # the declaring cube -- which `amount` belongs to -- went unmentioned.
        datasets |= {column.table for column in columns if column.table}
        if not columns or any(not column.table for column in columns):
            unqualified = True
    return datasets, unqualified


def _outermost_aggregate_scopes(tree):
    """Aggregate scopes that are not inside another one.

    Nesting is resolved so an ordered-set aggregate is counted once: `WithinGroup` and
    the `PercentileCont` inside it are one aggregate, and treating the inner one as its
    own scope would find no columns there and blame the declaring cube.
    """
    scopes = []
    for node in tree.walk():
        if not _is_aggregate_scope(node):
            continue
        if any(any(inner is node for inner in scope.walk()) for scope in scopes):
            continue
        scopes.append(node)
    return scopes


# A Cube `{...}` member reference, masked while sqlglot parses -- braces are not SQL.
_REF_RE = re.compile(r"\$?\{[^{}]*\}")
_REF_SENTINEL = "__ossie_ref_{}__"
# Any converter-owned sentinel identifier (the exporter masks metric references as
# `__ossie_mref_N__` before this module sees the text), never a column.
_SENTINEL_RE = re.compile(r"^__ossie_\w+__$")


def _mask_references(text):
    """Replace `{...}` references with sentinel identifiers sqlglot can parse.

    Returns (masked text, [original references]). `\\{`/`\\}` (Cube's escape for a
    literal brace) is masked too, so an escaped brace in raw SQL does not read as a
    reference -- both come back verbatim on unmask.
    """
    saved = []

    def keep(m):
        saved.append(m.group(0))
        return _REF_SENTINEL.format(len(saved) - 1)

    masked = _REF_RE.sub(keep, text.replace("\\{", "\x00lb\x00")
                         .replace("\\}", "\x00rb\x00"))
    return masked.replace("\x00lb\x00", "\\{").replace("\x00rb\x00", "\\}"), saved


def unqualified_column_names(sql_text):
    """The bare (unqualified, unquoted) column names in a SQL snippet, or None.

    Parser-based on purpose: only sqlglot can tell a column from a keyword, a
    function name, or an EXTRACT unit -- a regex over identifier tokens cannot.
    `{...}` references are masked first so Cube SQL parses too. None means the
    text does not parse, and the caller should leave it alone.
    """
    masked, _ = _mask_references(str(sql_text))
    tree = parse(masked)
    if tree is None:
        return None
    names = set()
    for column in tree.find_all(exp.Column):
        if column.table:
            continue
        ident = column.this
        if not isinstance(ident, exp.Identifier) or ident.args.get("quoted"):
            continue
        name = ident.name
        if _SENTINEL_RE.match(name) or not re.fullmatch(r"[A-Za-z_]\w*", name):
            continue
        names.add(name)
    return names


def replace_bare_identifiers(text, mapping):
    """Replace whole-word occurrences of `mapping`'s keys outside string literals.

    A match must stand alone: not part of a dotted reference (either side), not a
    function call, not adjacent to a quote or brace. The keys come from
    `unqualified_column_names`, so they are known to be column tokens -- the guards
    only keep a same-named token in another role (a table head, a call) untouched.
    """
    if not mapping:
        return text
    alternation = "|".join(re.escape(name) for name in sorted(mapping, key=len,
                                                              reverse=True))
    pattern = re.compile(
        rf'(?<![\w.$"{{])({alternation})(?!\s*[.(])(?![\w"}}])')
    return sub_outside_quotes(
        text, lambda run: pattern.sub(lambda m: mapping[m.group(1)], run))


def qualify_bare_columns(cube_sql):
    """Qualify bare column references in Cube SQL as `{CUBE}.column`.

    Cube compiles a member's `sql` as raw SQL of the data source, so a bare
    identifier is a physical column -- but an *ambiguous* one once the cube is
    joined, and in a model-level Ossie metric an unqualified name is not a column
    reference at all (the model-level namespace resolves bare identifiers as
    metrics). Qualifying here makes the translated expression say what Cube meant:
    `SUM(amount * 2)` becomes `SUM({CUBE}.amount * 2)`, which the reference
    machinery renders as `orders.amount * 2`.

    Unparseable SQL is returned unchanged -- the previous behaviour for every
    expression.
    """
    text = str(cube_sql)
    names = unqualified_column_names(text)
    if not names:
        return text
    return replace_bare_identifiers(
        text, {name: "{CUBE}." + name for name in names})


def has_top_level_operator(expr):
    """True if `expr` is not a single self-contained term.

    Used to decide whether inlining it back into a larger expression needs
    parentheses: a lone `SUM(x)` does not, `SUM(x) / 2` does.
    """
    depth, quote = 0, None
    # Stripped first: interior whitespace is what implies structure, so a trailing
    # newline off a YAML block scalar (`expression: |`) is not evidence of any, and
    # counting it wrapped a lone `SUM(x)\n` in parentheses it did not need.
    for ch in str(expr).strip():
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
