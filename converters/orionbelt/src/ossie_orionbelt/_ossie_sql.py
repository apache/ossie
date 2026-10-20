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

"""Whether an expression stays inside Apache Ossie's ``OSSIE_SQL_2026`` language.

The export tags an expression ``OSSIE_SQL_2026`` only when it is written in the
language ``core-spec/expression_language.md`` defines, so the tag is a claim
another tool can rely on. The check has two layers:

* **as written**, on the tokens: every function called is one the spec defines
  at the REQUIRED or RECOMMENDED level, every ``CAST`` target is a listed type,
  a typed literal is ``DATE``, ``TIME`` or ``TIMESTAMP_NTZ``, and no operator
  the spec lacks (``::``, ``->``, ``[ ]``, ...) appears. This layer exists
  because sqlglot normalizes while parsing: it reads ``SAFE_CAST`` as
  ``TRY_CAST`` and ``INT64`` as ``BIGINT``, yet the export writes the original.
* **as parsed**, on the sqlglot tree: every node is a construct the spec lists
  or one its functions parse into, so a subquery, an ``INTERVAL`` or a bare
  ``CURRENT_USER`` is rejected; ``DISTINCT`` appears only in ``SUM`` and
  ``COUNT``; date parts are listed ones.
"""

from __future__ import annotations

import re
from functools import cache

import sqlglot
from sqlglot import expressions as exp
from sqlglot.tokens import Token, TokenType

# Every function the spec defines at the REQUIRED or RECOMMENDED level, with a
# sample call. The names are what an expression may call, as written; parsing
# the samples gives the sqlglot nodes those calls produce. EXPERIMENTAL forms
# (TO_CHAR, TO_DATE / TO_TIMESTAMP with a format) are not in the language.
_FUNCTIONS: dict[str, str] = {
    # Aggregation
    "SUM": "SUM(x)",
    "COUNT": "COUNT(x) + COUNT(*) + COUNT(DISTINCT x)",
    "AVG": "AVG(x)",
    "MIN": "MIN(x)",
    "MAX": "MAX(x)",
    "STDDEV": "STDDEV(x)",
    "STDDEV_POP": "STDDEV_POP(x)",
    "STDDEV_SAMP": "STDDEV_SAMP(x)",
    "VARIANCE": "VARIANCE(x)",
    "VAR_POP": "VAR_POP(x)",
    "VAR_SAMP": "VAR_SAMP(x)",
    "MEDIAN": "MEDIAN(x)",
    "PERCENTILE_CONT": "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x)",
    "PERCENTILE_DISC": "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY x DESC)",
    "APPROX_COUNT_DISTINCT": "APPROX_COUNT_DISTINCT(x)",
    "APPROX_PERCENTILE": "APPROX_PERCENTILE(x, 0.5)",
    # Date and time
    "CURRENT_DATE": "CURRENT_DATE() + CURRENT_DATE",
    "CURRENT_TIMESTAMP": "CURRENT_TIMESTAMP() + CURRENT_TIMESTAMP",
    "CURRENT_TIME": "CURRENT_TIME() + CURRENT_TIME",
    "YEAR": "YEAR(d)",
    "QUARTER": "QUARTER(d)",
    "MONTH": "MONTH(d)",
    "DAY": "DAY(d)",
    "DAYOFYEAR": "DAYOFYEAR(d)",
    "HOUR": "HOUR(d)",
    "MINUTE": "MINUTE(d)",
    "SECOND": "SECOND(d)",
    "EXTRACT": "EXTRACT(YEAR FROM d)",
    "DATE_PART": "DATE_PART('year', d)",
    "DATE_TRUNC": "DATE_TRUNC('month', d)",
    "DATEADD": "DATEADD(day, 7, d)",
    "DATEDIFF": "DATEDIFF(day, a, b)",
    "TO_DATE": "TO_DATE(s)",
    "TO_TIMESTAMP": "TO_TIMESTAMP(s)",
    # Strings
    "CONCAT": "CONCAT(a, b) || c",
    "LENGTH": "LENGTH(s)",
    "LOWER": "LOWER(s)",
    "UPPER": "UPPER(s)",
    "TRIM": "TRIM(s)",
    "LTRIM": "LTRIM(s)",
    "RTRIM": "RTRIM(s)",
    "LEFT": "LEFT(s, 2)",
    "RIGHT": "RIGHT(s, 2)",
    "SUBSTRING": "SUBSTRING(s, 1, 2)",
    "REPLACE": "REPLACE(s, 'a', 'b')",
    "SPLIT_PART": "SPLIT_PART(s, ',', 1)",
    "POSITION": "POSITION('a' IN s)",
    "CHARINDEX": "CHARINDEX('a', s)",
    "CONTAINS": "CONTAINS(s, 'a')",
    "STARTSWITH": "STARTSWITH(s, 'a')",
    "ENDSWITH": "ENDSWITH(s, 'a')",
    "REGEXP_LIKE": "REGEXP_LIKE(s, 'a')",
    "REGEXP_EXTRACT": "REGEXP_EXTRACT(s, 'a')",
    "REGEXP_REPLACE": "REGEXP_REPLACE(s, 'a', 'b')",
    "REGEXP_COUNT": "REGEXP_COUNT(s, 'a')",
    # Math
    "ABS": "ABS(x)",
    "ROUND": "ROUND(x, 2)",
    "FLOOR": "FLOOR(x)",
    "CEIL": "CEIL(x)",
    "CEILING": "CEILING(x)",
    "TRUNC": "TRUNC(x, 2)",
    "TRUNCATE": "TRUNCATE(x, 2)",
    "MOD": "MOD(x, y)",
    "SIGN": "SIGN(x)",
    "POWER": "POWER(x, y)",
    "SQRT": "SQRT(x)",
    "EXP": "EXP(x)",
    "LN": "LN(x)",
    "LOG": "LOG(2, x)",
    "LOG10": "LOG10(x)",
    "SIN": "SIN(x)",
    "COS": "COS(x)",
    "TAN": "TAN(x)",
    "ASIN": "ASIN(x)",
    "ACOS": "ACOS(x)",
    "ATAN": "ATAN(x)",
    "ATAN2": "ATAN2(y, x)",
    "RADIANS": "RADIANS(x)",
    "DEGREES": "DEGREES(x)",
    "PI": "PI()",
    "GREATEST": "GREATEST(a, b)",
    "LEAST": "LEAST(a, b)",
    # Conditional
    "IF": "IF(c, a, b)",
    "IFF": "IFF(c, a, b)",
    "NULLIF": "NULLIF(a, b)",
    "COALESCE": "COALESCE(a, b)",
    "IFNULL": "IFNULL(a, b)",
    "NVL": "NVL(a, b)",
    "NVL2": "NVL2(a, b, c)",
    "ZEROIFNULL": "ZEROIFNULL(a)",
    "NULLIFZERO": "NULLIFZERO(a)",
    # Window
    "ROW_NUMBER": "ROW_NUMBER() OVER ()",
    "RANK": "RANK() OVER (ORDER BY x)",
    "DENSE_RANK": "DENSE_RANK() OVER (ORDER BY x)",
    "NTILE": "NTILE(4) OVER (ORDER BY x)",
    "PERCENT_RANK": "PERCENT_RANK() OVER (ORDER BY x)",
    "CUME_DIST": "CUME_DIST() OVER (ORDER BY x)",
    "LAG": "LAG(x, 1, 0) OVER (ORDER BY d)",
    "LEAD": "LEAD(x, 1, 0) OVER (ORDER BY d)",
    "FIRST_VALUE": "FIRST_VALUE(x) OVER (ORDER BY d)",
    "LAST_VALUE": "LAST_VALUE(x) OVER (ORDER BY d)",
    "NTH_VALUE": "NTH_VALUE(x, 2) OVER (ORDER BY d)",
    # Type conversion
    "CAST": "CAST(x AS INT)",
    "TRY_CAST": "TRY_CAST(x AS INT)",
}

# Syntax the spec lists that is not a function call: references, literals,
# operators, CASE, IN, BETWEEN, IS [NOT] DISTINCT FROM, the window clauses.
_CONSTRUCTS = (
    "SUM(CASE WHEN a = 1 AND NOT b <> 2 OR c IS NULL THEN -d * 2 / 3 % 4 - 5 + 6 END)"
    " + COUNT(CASE WHEN a > 1 AND b >= 2 AND c < 3 AND d <= 4 THEN 1 ELSE NULL END)"
    " + (a BETWEEN 1 AND 2) + (a IN (1, 2)) + (a LIKE 'x') + (a ILIKE 'x')"
    " + (a IS NOT DISTINCT FROM b) + (a IS DISTINCT FROM b) + (a = TRUE)"
    " + SUM(x) OVER (PARTITION BY a ORDER BY b DESC NULLS LAST"
    " ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    " + CAST(x AS DECIMAL(10, 2)) + DATE '2024-01-01'"
)

# Keywords that may stand before "(" without being a function call, or before
# a string without making it a typed literal.
_NOT_CALLS = {"OVER", "IN", "GROUP", "AND", "OR", "NOT", "WHEN", "THEN", "ELSE", "CASE", "BY"}
_NOT_CALLS |= {"DISTINCT", "BETWEEN", "LIKE", "ILIKE", "IS", "FROM", "AS"}

# CAST target types and typed literals the spec lists, as written.
_CAST_TYPES = {"VARCHAR", "STRING", "INTEGER", "INT", "BIGINT", "DECIMAL", "NUMERIC", "FLOAT"}
_CAST_TYPES |= {"DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "TIME"}
_TYPED_LITERALS = {"DATE", "TIME", "TIMESTAMP_NTZ"}

# Operators the spec does not have.
_FOREIGN_OPERATORS = {
    TokenType.DCOLON: "::",
    TokenType.ARROW: "->",
    TokenType.DARROW: "->>",
    TokenType.HASH_ARROW: "#>",
    TokenType.DHASH_ARROW: "#>>",
    TokenType.L_BRACKET: "[",
    TokenType.COLON: ":",
    TokenType.NULLSAFE_EQ: "<=>",
    TokenType.PLACEHOLDER: "?",
    TokenType.PARAMETER: "@",
    TokenType.SEMICOLON: ";",
}

_DATE_TRUNC_PARTS = {"year", "quarter", "month", "week", "day", "hour", "minute", "second"}
_EXTRACT_PARTS = _DATE_TRUNC_PARTS | {"dayofweek", "dayofyear", "millisecond"}

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# Nodes sqlglot writes under a name the spec does not have, and the spec's name.
_SPEC_NAMES: dict[type[exp.Expr], str] = {
    exp.VariancePop: "VAR_POP",
    exp.ApproxDistinct: "APPROX_COUNT_DISTINCT",
    exp.DayOfYear: "DAYOFYEAR",
    exp.StartsWith: "STARTSWITH",
    exp.EndsWith: "ENDSWITH",
}
_SPEC_TYPES = {exp.DataType.Type.TEXT: "STRING", exp.DataType.Type.TIMESTAMPNTZ: "TIMESTAMP_NTZ"}


def _parse(sql: str) -> exp.Expr:
    """*sql* as one expression, parsed where a SELECT list item stands.

    A leading ``TRUNCATE(`` would otherwise read as a TRUNCATE statement. Any
    clause besides the one expression is refused, so ``SUM(x) FROM t`` fails.
    """
    select = sqlglot.parse_one(f"SELECT {sql}")
    clauses = {key for key, value in select.args.items() if value}
    if not isinstance(select, exp.Select) or clauses != {"expressions"}:
        raise sqlglot.errors.ParseError("not a single expression")
    if len(select.expressions) != 1:
        raise sqlglot.errors.ParseError("not a single expression")
    return select.expressions[0]


def ossie_sql(sql: str) -> str:
    """*sql* with the spec's spelling where sqlglot's portable one differs.

    An imported formula is kept in sqlglot's spelling, which OrionBelt executes
    (``STARTS_WITH``, ``VARIANCE_POP``, ``STR_POSITION``, ``CAST(x AS TEXT)``);
    none of those is in the language, so the export respells them
    (``STARTSWITH``, ``VAR_POP``, ``CHARINDEX``, ``STRING``). A type keeps its
    parameters, so ``TIMESTAMP_NTZ(3)`` keeps its precision. *sql* that does
    not parse is returned as it is.
    """
    try:
        node = _parse(sql)
    except sqlglot.errors.SqlglotError:
        return sql

    def spell(n: exp.Expr) -> exp.Expr:
        name = _SPEC_NAMES.get(type(n))
        if name is not None:
            args = [a for a in (n.this, n.args.get("expression")) if a is not None]
            return exp.Anonymous(this=name, expressions=args)
        if isinstance(n, exp.StrPosition) and not n.args.get("position"):
            # CHARINDEX(substr, str) is the spec's alias for POSITION.
            return exp.Anonymous(this="CHARINDEX", expressions=[n.args["substr"], n.this])
        if isinstance(n, exp.DataType) and n.this in _SPEC_TYPES:
            return exp.DataType(
                this=exp.DataType.Type.USERDEFINED,
                kind=_SPEC_TYPES[n.this],
                expressions=n.expressions,
                nested=n.args.get("nested"),
            )
        return n

    return node.transform(spell).sql()


@cache
def _allowed_nodes() -> frozenset[type[exp.Expr]]:
    """The sqlglot node types the spec's functions and constructs parse into."""
    samples = [*_FUNCTIONS.values(), _CONSTRUCTS]
    return frozenset(type(node) for s in samples for node in _parse(s).walk())


def subset_violations(sql: str) -> list[str]:
    """What in *sql* falls outside ``OSSIE_SQL_2026``; empty when nothing does."""
    try:
        tokens = sqlglot.tokenize(sql)
        tree = _parse(sql)
    except sqlglot.errors.SqlglotError:
        return ["the expression does not parse"]
    return _written_violations(tokens) + _parsed_violations(tree)


def _written_violations(tokens: list[Token]) -> list[str]:
    found: list[str] = []
    for i, token in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        prev = tokens[i - 1] if i else None
        name = token.text.upper()
        # Only a bare word is a keyword: a string 'AS' or a quoted "Escape" is not.
        bare = token.token_type not in (TokenType.STRING, TokenType.IDENTIFIER)
        if token.token_type in _FOREIGN_OPERATORS:
            operator = _FOREIGN_OPERATORS[token.token_type]
            found.append(f"operator {operator} is not in OSSIE_SQL_2026")
        elif token.token_type is TokenType.ESCAPE:
            found.append("LIKE ... ESCAPE is not in OSSIE_SQL_2026")
        elif prev is not None and prev.token_type is TokenType.ALIAS:
            if not bare or name not in _CAST_TYPES:
                found.append(f"type {token.text} is not in OSSIE_SQL_2026")
        elif nxt is not None and nxt.token_type is TokenType.L_PAREN:
            quoted_call = token.token_type is TokenType.IDENTIFIER
            bare_call = bare and _NAME.fullmatch(token.text) and name not in _NOT_CALLS
            if quoted_call or (bare_call and name not in _FUNCTIONS):
                found.append(f"function {token.text} is not in OSSIE_SQL_2026")
            elif name in ("TO_DATE", "TO_TIMESTAMP") and _argument_count(tokens, i + 1) != 1:
                found.append(f"{name} with a format is EXPERIMENTAL, not in OSSIE_SQL_2026")
        elif (
            bare
            and nxt is not None
            and nxt.token_type is TokenType.STRING
            and _NAME.fullmatch(token.text)
            and name not in _TYPED_LITERALS | _NOT_CALLS
        ):
            found.append(f"typed literal {token.text} '...' is not in OSSIE_SQL_2026")
    return found


def _argument_count(tokens: list[Token], open_paren: int) -> int:
    """The number of arguments in the call whose "(" is at *open_paren*."""
    depth, commas, empty = 1, 0, True
    for token in tokens[open_paren + 1 :]:
        if token.token_type is TokenType.R_PAREN and depth == 1:
            break
        empty = False
        if token.token_type is TokenType.L_PAREN:
            depth += 1
        elif token.token_type is TokenType.R_PAREN:
            depth -= 1
        elif token.token_type is TokenType.COMMA and depth == 1:
            commas += 1
    return 0 if empty else commas + 1


def _parsed_violations(tree: exp.Expr) -> list[str]:
    found: list[str] = []
    allowed = _allowed_nodes()
    for node in tree.walk():
        if type(node) not in allowed:
            found.append(f"{type(node).__name__} is not in OSSIE_SQL_2026")
        elif isinstance(node, exp.Distinct) and not isinstance(node.parent, exp.Sum | exp.Count):
            found.append("DISTINCT is defined only for SUM and COUNT")
        elif isinstance(node, exp.DateTrunc) and node.text("unit").lower() not in _DATE_TRUNC_PARTS:
            found.append(f"DATE_TRUNC part '{node.text('unit')}' is not in OSSIE_SQL_2026")
        elif isinstance(node, exp.Extract) and node.text("this").lower() not in _EXTRACT_PARTS:
            found.append(f"EXTRACT part '{node.text('this')}' is not in OSSIE_SQL_2026")
    return found
