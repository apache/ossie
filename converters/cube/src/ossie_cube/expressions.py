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


# Any identifier that could be the name of a call. Scanning the source text rather
# than the parse tree is forced by the offsets: sqlglot renames some aggregates when
# it renders (`APPROX_COUNT_DISTINCT` comes back as `APPROX_DISTINCT`) and two calls
# of the same name render identically, so node text cannot locate them in the
# original string. What the scan must not be is *selective* -- every candidate is
# confirmed against sqlglot below, so this only has to be generous enough to miss
# nothing, which a list of known aggregate names could not be.
_CALL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# An ordered-set aggregate's `WITHIN GROUP (...)` tail, and a window function's
# `OVER`, both of which decide where a call's span really ends -- or whether it is a
# span at all.
_WITHIN_GROUP_RE = re.compile(r"\s*WITHIN\s+GROUP\s*\(", re.IGNORECASE)
_OVER_RE = re.compile(r"\s*OVER\b", re.IGNORECASE)


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
    quoted = quoted_char_mask(text)
    for match in _CALL_NAME_RE.finditer(text):
        start = match.start()
        if quoted[start]:
            continue
        # A call, not a bare name: the next thing has to be its opening paren. The
        # regex matches maximal identifier runs, so the boundary before is given.
        probe = match.end()
        while probe < len(text) and text[probe].isspace():
            probe += 1
        if probe >= len(text) or text[probe] != "(":
            continue
        close = _match_paren(text, probe)
        if close is None:
            continue
        end = _end_of_call(text, close + 1)
        # Confirm the slice against sqlglot: that is what separates an aggregate from
        # a scalar call (ROUND, COALESCE, CAST), a UDF, and a window function.
        if _is_modelled_aggregate(parse(text[start:end])) \
                and not _OVER_RE.match(text, end):
            candidates.append((start, end))

    # Drop any span contained within another: only the outermost becomes a measure.
    candidates.sort()
    out = []
    for start, end in candidates:
        if any(s <= start and end <= e for s, e in out):
            continue
        out.append((start, end))
    return out


def _end_of_call(text, end):
    """Where a call's span really ends: past a trailing `WITHIN GROUP (...)`, if any.

    An ordered-set aggregate carries its value-bearing column in the ORDER BY -- on
    the wrapper, not on the function -- so a span stopping at the function's own
    closing paren cuts `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY users.ltv)` down
    to `PERCENTILE_CONT(0.5)`, which names no dataset at all and would be lifted onto
    the wrong cube. It is the same trap `_is_aggregate_scope` documents for the
    classifier, arriving here as an offset rather than a node.
    """
    match = _WITHIN_GROUP_RE.match(text, end)
    if not match:
        return end
    close = _match_paren(text, match.end() - 1)
    return end if close is None else close + 1


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


def _is_modelled_aggregate(node):
    """True for a node sqlglot models as an aggregate outright.

    Two shapes:
    - `AggFunc`, every aggregate sqlglot has a class for -- SUM and COUNT through
      STDDEV, VARIANCE, MEDIAN, ARRAY_AGG, CORR, ANY_VALUE, STRING_AGG;
    - `WithinGroup`, an *ordered-set* aggregate -- the value-bearing column lives in the
      ORDER BY, on the wrapper rather than on the inner function, so examining only the
      inner one attributed `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY users.ltv)` to
      the declaring cube instead of `users`. The syntax itself proves the call is an
      aggregate, so this holds even when the function inside is one sqlglot does not
      model (`LISTAGG(...) WITHIN GROUP (...)`).

    This is the *exact* half of the answer: no scalar call reaches it -- ROUND,
    COALESCE, CONCAT, NULLIF, GREATEST, CAST, LOWER, DATE_TRUNC, ABS and FLOOR all
    have their own non-aggregate nodes. Being exact is what lets decomposition act on
    it, and not merely warn: splitting a call out into its own measure on another cube
    is a rewrite, and a false positive there emits a measure made of a scalar
    expression.
    """
    return isinstance(node, (exp.AggFunc, exp.WithinGroup))


def _is_aggregate_scope(node):
    """True for a node that constitutes one aggregate, whatever shape sqlglot gave it.

    The modelled shapes, plus `Anonymous` -- a call sqlglot does not model at all,
    which is how LISTAGG, APPROX_PERCENTILE and BIT_OR arrive, and how
    `LISTAGG(...) WITHIN GROUP (...)` vanished from the analysis entirely.

    An `Anonymous` may equally be a scalar UDF, so treating it as an aggregate
    over-reports. That is the cheaper error *here*: a warning by default (the fan-out
    policy warns rather than refuses), against a silently inflated number the other
    way. It is not the cheaper error for decomposition, which is why that path stops
    at `_is_modelled_aggregate` -- the two callers share the recognition and part
    company only on the one shape nothing can classify.
    """
    return _is_modelled_aggregate(node) or isinstance(node, exp.Anonymous)


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


def unsafe_aggregate_path_heads(expr):
    """The first part of every column path of three or more parts that a
    non-idempotent aggregate in `expr` reads, or None when `expr` does not parse.

    sqlglot reads `orders.payload.amount` as db `orders`, table `payload`, so
    `unsafe_aggregate_datasets` attributes it to `payload`. In a view projection every
    column is written `dataset.path` -- a struct field keeps its dataset in front -- so
    the head is the dataset, and this is what lets projection check the one it read.
    """
    tree = parse(expr)
    if tree is None:
        return None
    heads = set()
    for scope in _outermost_aggregate_scopes(tree):
        if is_idempotent_aggregate(scope):
            continue
        for column in scope.find_all(exp.Column):
            parts = column.parts
            if len(parts) >= 3 and isinstance(parts[0], exp.Identifier):
                heads.add(parts[0].name)
    return heads


def unsplittable_aggregate_datasets(expr):
    """Datasets read by an aggregate that decomposition has to leave where it is.

    The classifier recognizes one shape more than decomposition acts on, and the
    difference is exactly the residue this names:

    - `Anonymous`, a call sqlglot does not model -- LISTAGG and APPROX_PERCENTILE
      arrive this way, and so does any scalar UDF. Lifting one onto another cube would
      emit a measure built from what may be a scalar expression, so it stays inlined.
    - the aggregate half of a window function. `SUM(x) OVER (PARTITION BY y)` depends
      on its frame, so splitting the `SUM(x)` out and leaving `OVER (...)` behind in
      the glue text would produce something that means nothing.

    Either way the call keeps sitting on the measure's own cube while reading another,
    so Cube's per-measure row-multiplication correction is keyed on the wrong one. The
    caller cannot fix that -- but it can say so instead of leaving it silent, which is
    the whole reason the two paths were worth reconciling.

    Returns the dataset names as written. An empty set when nothing is stranded, or
    when the expression does not parse (the caller is already conservative there).
    """
    tree = parse(expr)
    if tree is None:
        return set()
    # Identity, not equality: two `SUM(x)` nodes in one expression compare equal, and
    # membership by value would strand both because one of them is windowed.
    windowed = {id(node) for window in tree.find_all(exp.Window)
                for node in window.walk()}
    stranded = set()
    for scope in _outermost_aggregate_scopes(tree):
        if _is_modelled_aggregate(scope) and id(scope) not in windowed:
            continue
        stranded |= {column.table for column in scope.find_all(exp.Column)
                     if column.table}
    return stranded


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


# The portable reading first, then grammars that quote identifiers with backticks
# (Databricks, BigQuery, MySQL) or brackets (T-SQL) and read unit arguments the way
# those warehouses do -- `DATEDIFF(day, a, b)`, `CONVERT(VARCHAR, x)`.
_READINGS = (None, "databricks", "tsql")

# Words a function takes as a date part or type name rather than a column. sqlglot's
# portable grammar reads `DATEDIFF(day, a, b)` with `day` as a column and `b` as the
# unit, and a qualification built on that reading would be wrong.
_UNIT_WORDS = frozenset({
    "YEAR", "YEARS", "QUARTER", "MONTH", "MONTHS", "WEEK", "WEEKS", "WEEKDAY",
    "ISOWEEK", "ISOYEAR", "DAY", "DAYS", "DAYOFWEEK", "DAYOFYEAR", "DOW", "DOY", "HOUR",
    "HOURS", "MINUTE", "MINUTES", "SECOND", "SECONDS", "MILLISECOND", "MILLISECONDS",
    "MICROSECOND", "MICROSECONDS", "NANOSECOND", "NANOSECONDS", "EPOCH", "DECADE",
    "CENTURY", "MILLENNIUM", "TIMEZONE", "TIMEZONE_HOUR", "TIMEZONE_MINUTE", "YYYY",
    "YY", "MM", "DD", "HH", "MI", "SS", "MS",
})
_TYPE_WORDS = frozenset({
    "VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "INT", "INTEGER", "BIGINT", "SMALLINT",
    "TINYINT", "DECIMAL", "NUMERIC", "FLOAT", "REAL", "DOUBLE", "DATETIME", "DATETIME2",
    "TIMESTAMP", "BOOLEAN",
})


class UnattributableSQL(ValueError):
    """Cube SQL whose columns cannot be attributed to a dataset statically."""


def _parse_as(text, dialect):
    """`text` parsed as `dialect`, or None when it does not parse as that."""
    try:
        return sqlglot.parse_one(text, read=dialect)
    except (sqlglot.errors.SqlglotError, ValueError, RecursionError):
        return None


def _misread(tree):
    """True when a reading takes a unit or type argument for a column, or a column for
    a unit -- what the portable grammar does to `DATEDIFF(day, a, b)`."""
    if any(var.name.upper() not in _UNIT_WORDS for var in tree.find_all(exp.Var)):
        return True
    return any(not column.table and isinstance(column.parent, exp.Func)
               and column.name.upper() in _UNIT_WORDS | _TYPE_WORDS
               for column in tree.find_all(exp.Column))


def strip_sql_comments(sql):
    """`sql` with its `--` and `/* */` comments replaced by a space.

    A trailing `-- note` is harmless in a member's own SQL, but inlined into another
    expression it comments out everything after it.
    """
    text = str(sql)
    mask = quoted_char_mask(text)
    out, i = [], 0
    while i < len(text):
        if not mask[i] and text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            out.append(" ")
        elif not mask[i] and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            out.append(" ")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def qualify_column_paths(cube_sql, own_cube=None, cube_names=()):
    """Qualify every physical column path in Cube SQL as `{CUBE}.path`.

    `qualify_bare_columns` leaves a dotted path as written, which suits the lossless
    import: the text is carried across, not reasoned about. View projection has to say
    which dataset every column belongs to, and a path in a cube's own SQL is that
    cube's: `payload.amount` is a struct field of the cube's table, so the whole path is
    qualified. A path rooted at a `{...}` reference is left alone. Cube aliases each
    cube's table by the cube's name, so a path starting with `own_cube` reads the own
    table (`orders.amount` becomes `{CUBE}.amount`), and one starting with another of
    `cube_names` could be that cube's table or a struct field, and is refused.

    Cube SQL is the data source's own, so other grammars are tried when the portable one
    fails or misreads a unit argument. Only positions are read and the text is never
    regenerated, so which grammar reads it does not matter. Comments are dropped.

    Raises UnattributableSQL saying why when the columns cannot be attributed statically.
    """
    masked, saved = _mask_references(strip_sql_comments(cube_sql))
    # The positions sqlglot reports are into the text it parsed, so it gets no other.
    masked = masked.strip()
    readings = [t for t in (_parse_as(masked, d) for d in _READINGS) if t is not None]
    if not readings:
        raise UnattributableSQL("it does not parse")
    tree = next((t for t in readings if not _misread(t)), None)
    if tree is None:
        raise UnattributableSQL(
            "a unit or type argument reads as a column (or a column as a unit); write "
            "a column that shares a unit's name as {CUBE}.name")
    if tree.find(exp.Select, exp.Lambda) is not None:
        raise UnattributableSQL("it binds names of its own (a subquery or a lambda)")
    others = {name.casefold() for name in cube_names if name != own_cube}
    edits = []
    for column in tree.find_all(exp.Column):
        head = column.parts[0] if column.parts else None
        if not isinstance(head, exp.Identifier):
            raise UnattributableSQL(f"'{column.sql()}' is not a column path")
        start = head.meta.get("start")
        if _SENTINEL_RE.match(head.name):
            continue
        if not isinstance(start, int):
            raise UnattributableSQL(f"sqlglot gives '{column.sql()}' no source position")
        folded = head.name if head.args.get("quoted") else head.name.casefold()
        if len(column.parts) > 1 and own_cube and folded in (own_cube, own_cube.casefold()):
            edits.append((start, head.meta["end"] + 1, "{CUBE}"))
        elif len(column.parts) > 1 and folded.casefold() in others:
            path = column.sql()
            rest = path.split(".", 1)[1]
            raise UnattributableSQL(
                f"'{path}' starts with the name of cube '{head.name}', which Cube reads "
                f"as that cube's table when it is joined; write '{{CUBE}}.{path}' for a "
                f"field of this cube's table, or '{{{head.name}}}.{rest}' for the "
                f"joined cube's column")
        else:
            edits.append((start, start, "{CUBE}."))
    for start, end, text in sorted(edits, reverse=True):
        masked = masked[:start] + text + masked[end:]
    return re.sub(_REF_SENTINEL.format(r"(\d+)"),
                  lambda m: saved[int(m.group(1))], masked)


def has_top_level_operator(expr):
    """True if `expr` is not a single self-contained term.

    Used to decide whether inlining it back into a larger expression needs
    parentheses: a lone `SUM(x)` does not, `SUM(x) / 2` does.

    One aggregate call spanning the whole text is a single term by definition, which
    the character scan cannot see for an ordered-set aggregate: the space in
    `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY users.ltv)` sits at depth 0 and reads
    as structure. Parenthesizing it was not merely untidy -- inlining happens once per
    round trip, so each cycle added another pair and the expression grew without
    bound, which is an idempotence break. Asking the scanner keeps the two readings of
    "one aggregate call" from drifting apart.

    Only *whitespace* at depth 0 is ambiguous that way. An operator there cannot occur
    inside a single call -- everything after the name is parenthesized, and an
    ordered-set aggregate puts only `WITHIN GROUP` between its two groups -- so it
    settles the question outright. That ordering is what keeps the scanner off the hot
    path: this runs once per reference while a measure is inlined, over text that
    doubles at every step of a reference chain, and `SUM(x)` and `SUM(a) + SUM(a)`
    both now answer without parsing anything.
    """
    text = str(expr).strip()
    depth, quote, spaced = 0, None, False
    # `text` is stripped: interior whitespace is what implies structure, so a trailing
    # newline off a YAML block scalar (`expression: |`) is not evidence of any, and
    # counting it wrapped a lone `SUM(x)\n` in parentheses it did not need.
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch in "+-*/%<>=|&":
            return True
        elif depth == 0 and ch.isspace():
            spaced = True
    if not spaced:
        return False
    # Whitespace alone, so this may still be one call: `SUM (x)`, or an ordered-set
    # aggregate. The scanner is the same authority decomposition uses for where a call
    # begins and ends, so the two cannot disagree about it.
    return _scan_aggregates(text) != [(0, len(text))]
