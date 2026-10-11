/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ossie.converter.databricks;

import static org.apache.ossie.converter.databricks.OssieConverterCommon.DIALECT_ANSI;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.DIALECT_DATABRICKS;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.DIALECT_OSSIE_SQL;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Labels a Databricks SQL expression with the most portable Apache Ossie dialect it can carry on
 * import: {@code OSSIE_SQL_2026} when it uses only the Ossie expression language
 * (core-spec/expression_language.md), else {@code ANSI_SQL} when it uses only standard SQL, else
 * {@code DATABRICKS}.
 *
 * <p>A lexical check against allowlists, not a parser, and deliberately conservative: a construct
 * counts as portable only if it is listed here and, where the Ossie spec defines its behavior,
 * Databricks evaluates it that way. Anything else stays {@code DATABRICKS}, which is always
 * correct because the expression came from a Metric View. The traps it guards against include
 * Databricks reading {@code "x"} as a string where portable SQL reads an identifier, a doubled
 * quote in {@code 'a''b'} that a legacy Databricks setting reads as two concatenated literals,
 * backslash escapes in strings, {@code TIMESTAMP} carrying the session time zone, and same-named
 * functions with other Databricks meanings. It does not check behavior the spec leaves open, such
 * as integer division, NULL handling in CONCAT or GREATEST, rounding of ties, or the timestamp
 * type DATE_TRUNC and DATEADD return.
 */
final class ExpressionDialect {

  // Functions in the Ossie expression language whose Databricks meaning matches. Left out on
  // purpose: TRUNC/TRUNCATE (date truncation in Databricks), DATEDIFF (counts whole elapsed units
  // in Databricks, unit boundaries crossed elsewhere), REGEXP_* (regex flavors differ), TO_CHAR
  // and the format-string TO_DATE (format tokens differ), TO_TIMESTAMP (session-time-zone result),
  // and window functions (OVER is not portable: Databricks sorts NULLs first by default, and a
  // window over a measure depends on the query's grain).
  private static final Set<String> OSSIE_FUNCTIONS = Set.of(
      "SUM", "COUNT", "AVG", "MIN", "MAX", "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "VARIANCE",
      "VAR_POP", "VAR_SAMP", "MEDIAN", "PERCENTILE_CONT", "PERCENTILE_DISC",
      "APPROX_COUNT_DISTINCT", "APPROX_PERCENTILE", "CURRENT_DATE", "CURRENT_TIMESTAMP", "YEAR",
      "QUARTER", "MONTH", "DAY", "DAYOFYEAR", "HOUR", "MINUTE", "SECOND", "EXTRACT", "DATE_PART",
      "DATE_TRUNC", "DATEADD", "TO_DATE", "CONCAT", "LENGTH", "LOWER", "UPPER", "TRIM", "LTRIM",
      "RTRIM", "LEFT", "RIGHT", "SUBSTRING", "REPLACE", "SPLIT_PART", "POSITION", "CHARINDEX",
      "CONTAINS", "STARTSWITH", "ENDSWITH", "ABS", "ROUND", "FLOOR", "CEIL", "CEILING", "MOD",
      "SIGN", "POWER", "SQRT", "EXP", "LN", "LOG", "LOG10", "SIN", "COS", "TAN", "ASIN", "ACOS",
      "ATAN", "ATAN2", "RADIANS", "DEGREES", "PI", "GREATEST", "LEAST", "IF", "IFF", "NULLIF",
      "COALESCE", "IFNULL", "NVL", "NVL2", "ZEROIFNULL", "NULLIFZERO", "CAST", "TRY_CAST");

  // Standard SQL functions whose Databricks meaning matches. Those outside OSSIE_FUNCTIONS
  // (CORR, COVAR_*, EVERY, the *_LENGTH family) make an expression ANSI_SQL rather than Ossie.
  private static final Set<String> ANSI_FUNCTIONS = Set.of(
      "SUM", "COUNT", "AVG", "MIN", "MAX", "STDDEV_POP", "STDDEV_SAMP", "VAR_POP", "VAR_SAMP",
      "CORR", "COVAR_POP", "COVAR_SAMP", "EVERY", "PERCENTILE_CONT", "PERCENTILE_DISC",
      "CURRENT_DATE", "CURRENT_TIMESTAMP", "EXTRACT", "CAST", "COALESCE", "NULLIF", "UPPER",
      "LOWER", "TRIM", "SUBSTRING", "POSITION", "CHAR_LENGTH", "CHARACTER_LENGTH", "OCTET_LENGTH",
      "ABS", "MOD", "LN", "EXP", "POWER", "SQRT", "FLOOR", "CEIL", "CEILING");

  // Aggregate calls (and the FILTER / WITHIN GROUP clauses that qualify one): in a measure, a bare
  // name inside one is a column, while outside one it refers to another measure.
  private static final Set<String> AGGREGATES = Set.of(
      "SUM", "COUNT", "AVG", "MIN", "MAX", "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "VARIANCE",
      "VAR_POP", "VAR_SAMP", "MEDIAN", "PERCENTILE_CONT", "PERCENTILE_DISC",
      "APPROX_COUNT_DISTINCT", "APPROX_PERCENTILE", "CORR", "COVAR_POP", "COVAR_SAMP", "EVERY",
      "FILTER", "GROUP");

  // Argument counts the portable form allows, for functions whose other Databricks forms mean
  // something else (one-argument LOG = LN, COUNT(a, b) counting rows where all are non-null,
  // CEIL(x, scale), and so on).
  private static final Map<String, int[]> ARITY = Map.ofEntries(
      Map.entry("COUNT", new int[] {1, 1}),
      Map.entry("CEIL", new int[] {1, 1}),
      Map.entry("CEILING", new int[] {1, 1}),
      Map.entry("FLOOR", new int[] {1, 1}),
      Map.entry("DATEADD", new int[] {3, 3}),
      Map.entry("LOG", new int[] {2, 2}),
      Map.entry("TO_DATE", new int[] {1, 1}),
      Map.entry("APPROX_COUNT_DISTINCT", new int[] {1, 1}),
      Map.entry("APPROX_PERCENTILE", new int[] {2, 2}),
      Map.entry("TRIM", new int[] {1, 1}),
      Map.entry("LTRIM", new int[] {1, 1}),
      Map.entry("RTRIM", new int[] {1, 1}),
      Map.entry("REPLACE", new int[] {3, 3}),
      Map.entry("CHARINDEX", new int[] {2, 2}));

  // CAST target types whose Databricks meaning matches. Left out: VARCHAR/CHAR (a plain STRING in
  // Databricks, without length semantics), FLOAT/REAL (32-bit in Databricks), the integer types
  // (Databricks truncates a fractional value where others round), TIME, TIMESTAMP (session time
  // zone), and DECIMAL/NUMERIC without (p, s) (DECIMAL(10, 0) in Databricks).
  private static final Set<String> OSSIE_TYPES = Set.of(
      "STRING", "DECIMAL", "NUMERIC", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP_NTZ");
  private static final Set<String> ANSI_TYPES = Set.of("DECIMAL", "NUMERIC", "BOOLEAN", "DATE");
  private static final Set<String> PARAMETERIZED_TYPES = Set.of("DECIMAL", "NUMERIC");

  // Keywords valid in both dialects wherever they appear (the grammar itself is not checked).
  private static final Set<String> KEYWORDS = Set.of(
      "CASE", "WHEN", "THEN", "ELSE", "END", "AND", "OR", "NOT", "IN", "BETWEEN", "LIKE", "IS",
      "NULL", "TRUE", "FALSE", "DISTINCT", "ORDER", "BY", "ASC", "DESC", "WITHIN");

  // Keywords that may directly precede an opening parenthesis.
  private static final Set<String> KEYWORDS_BEFORE_PAREN = Set.of(
      "AND", "OR", "NOT", "WHEN", "THEN", "ELSE", "CASE", "IN", "LIKE", "ILIKE", "BETWEEN",
      "DISTINCT", "BY", "FILTER", "GROUP");

  // Keywords only valid in a specific position, checked in Scan.word().
  private static final Set<String> CONTEXT_KEYWORDS = Set.of(
      "AS", "FROM", "FOR", "ESCAPE", "WHERE", "NULLS", "BOTH", "LEADING", "TRAILING");

  // Keywords that are themselves a complete operand.
  private static final Set<String> VALUE_KEYWORDS = Set.of("NULL", "TRUE", "FALSE");

  // Typed-literal prefixes: DATE '2024-01-15'. TIMESTAMP is left out (session time zone).
  private static final Set<String> LITERAL_PREFIXES = Set.of("DATE", "TIMESTAMP_NTZ");

  // Words that mean an expression is not a plain portable one (subqueries, intervals, windows,
  // session functions, Databricks-only operators and constructors).
  private static final Set<String> REJECTED_WORDS = Set.of(
      "SELECT", "WITH", "UNION", "EXCEPT", "INTERSECT", "HAVING", "JOIN", "LATERAL", "INTERVAL",
      "EXISTS", "ARRAY", "MAP", "STRUCT", "RLIKE", "REGEXP", "DIV", "ANY", "SOME", "ALL",
      "QUALIFY", "LIMIT", "WINDOW", "VALUES", "TABLE", "OVER", "FILTER", "GROUP", "CURRENT_USER",
      "USER", "SESSION_USER", "CURRENT_TIME", "LOCALTIME", "GROUPING__ID");

  private static final List<String> SYMBOLS = List.of(
      "<>", "<=", ">=", "!=", "||", "<", ">", "=", "+", "-", "*", "/", "%", "(", ")", ",", ".");
  // Databricks-only operators (shifts included) and comments, checked before SYMBOLS can match a
  // prefix of them.
  private static final List<String> REJECTED_SYMBOLS = List.of(
      "<=>", "==", "->", "=>", "<<", ">>", "--", "/*");
  private static final Pattern NUMBER =
      Pattern.compile("(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?");

  private ExpressionDialect() {}

  /**
   * The most portable dialect {@code expr} can carry: OSSIE_SQL_2026, ANSI_SQL, or DATABRICKS.
   *
   * @param datasets lower-cased dataset names of the Apache Ossie model; {@code a.b} is portable
   *     only when {@code a} is one of them (not {@code source.}, a join alias, or a struct)
   * @param opaqueNames lower-cased names whose meaning comes from the Metric View rather than a
   *     column (parameters, dimensions shadowing a column); a reference to one, bare or as
   *     {@code dataset.name}, is not portable
   * @param measure whether {@code expr} is a measure, where a name outside an aggregate call
   *     refers to another measure rather than a column, so it is not portable
   */
  static String classify(
      String expr, Set<String> datasets, Set<String> opaqueNames, boolean measure) {
    List<Token> tokens = expr == null ? null : tokenize(expr);
    if (tokens == null || tokens.isEmpty()) {
      return DIALECT_DATABRICKS;
    }
    Scan scan = new Scan(tokens, datasets, opaqueNames, measure);
    if (!scan.run()) {
      return DIALECT_DATABRICKS;
    }
    return scan.ossie ? DIALECT_OSSIE_SQL : scan.ansi ? DIALECT_ANSI : DIALECT_DATABRICKS;
  }

  private enum Kind { IDENT, NUMBER, STRING, SYMBOL }

  private record Token(Kind kind, String text) {
    boolean is(String symbol) {
      return kind == Kind.SYMBOL && text.equals(symbol);
    }

    boolean isWord(String word) {
      return kind == Kind.IDENT && text.equalsIgnoreCase(word);
    }

    String upper() {
      return text.toUpperCase(Locale.ROOT);
    }
  }

  /** Splits {@code s} into tokens, or returns null on any construct this check does not handle. */
  private static List<Token> tokenize(String s) {
    List<Token> out = new ArrayList<>();
    int n = s.length();
    int i = 0;
    while (i < n) {
      char c = s.charAt(i);
      if (Character.isWhitespace(c)) {
        i++;
      } else if (c == '\'') {
        int end = s.indexOf('\'', i + 1);
        if (end < 0) {
          return null;
        }
        String body = s.substring(i + 1, end);
        // A backslash escape reads differently, and so can a doubled quote: 'a''b' is one
        // literal with a quote in standard SQL, but two concatenated literals under a legacy
        // Databricks setting.
        if (body.indexOf('\\') >= 0 || (end + 1 < n && s.charAt(end + 1) == '\'')) {
          return null;
        }
        out.add(new Token(Kind.STRING, body));
        i = end + 1;
      } else if (Character.isLetter(c) || c == '_') {
        int j = i + 1;
        while (j < n && isIdentPart(s.charAt(j))) {
          j++;
        }
        out.add(new Token(Kind.IDENT, s.substring(i, j)));
        i = j;
      } else if (Character.isDigit(c)
          || (c == '.' && i + 1 < n && Character.isDigit(s.charAt(i + 1)))) {
        Matcher m = NUMBER.matcher(s).region(i, n);
        if (!m.lookingAt()) {
          return null;
        }
        int j = m.end();
        // A trailing letter is a Databricks typed-number suffix (1L, 2BD) or a malformed number.
        if (j < n && isIdentPart(s.charAt(j))) {
          return null;
        }
        out.add(new Token(Kind.NUMBER, s.substring(i, j)));
        i = j;
      } else {
        String sym = symbolAt(s, i);
        if (sym == null) {
          return null;
        }
        out.add(new Token(Kind.SYMBOL, sym));
        i += sym.length();
      }
    }
    return out;
  }

  private static boolean isIdentPart(char c) {
    return Character.isLetterOrDigit(c) || c == '_';
  }

  private static String symbolAt(String s, int i) {
    for (String rejected : REJECTED_SYMBOLS) {
      if (s.startsWith(rejected, i)) {
        return null;
      }
    }
    for (String sym : SYMBOLS) {
      if (s.startsWith(sym, i)) {
        return sym;
      }
    }
    return null; // backticks, double quotes, ':', '[', '!', '|', ...
  }

  private static boolean isKeyword(String u) {
    return KEYWORDS.contains(u) || KEYWORDS_BEFORE_PAREN.contains(u)
        || CONTEXT_KEYWORDS.contains(u);
  }

  /** One pass over the tokens, narrowing which portable dialects the expression still fits. */
  private static final class Scan {
    private final List<Token> tokens;
    private final Set<String> datasets;
    private final Set<String> opaqueNames;
    private final boolean measure;
    private final Deque<Frame> frames = new ArrayDeque<>();
    private String pendingOpener = "";
    boolean ossie = true;
    boolean ansi = true;

    /** A parenthesized group: the word that opened it, and its top-level argument shape. */
    private static final class Frame {
      final String opener;
      final boolean aggregate;
      int commas;
      boolean empty = true;
      boolean keywordForm; // EXTRACT ... FROM, POSITION ... IN, FILTER (WHERE ...), and so on

      Frame(String opener) {
        this.opener = opener;
        this.aggregate = AGGREGATES.contains(opener);
      }
    }

    Scan(List<Token> tokens, Set<String> datasets, Set<String> opaqueNames, boolean measure) {
      this.tokens = tokens;
      this.datasets = datasets;
      this.opaqueNames = opaqueNames;
      this.measure = measure;
    }

    boolean run() {
      for (int i = 0; i < tokens.size(); i++) {
        Token t = tokens.get(i);
        Token prev = i > 0 ? tokens.get(i - 1) : null;
        Token next = i + 1 < tokens.size() ? tokens.get(i + 1) : null;
        if (prev != null && endsOperand(prev) && startsOperand(t) && !isTypedLiteral(prev, t)) {
          return false; // two operands side by side: 'a' 'b', or an alias as in SUM(x) total
        }
        Frame top = frames.peek();
        if (top != null && !t.is(")")) {
          top.empty = false;
        }
        boolean ok = switch (t.kind) {
          case STRING, NUMBER -> true;
          case SYMBOL -> symbol(t, prev, next, top);
          case IDENT -> ident(i, t, prev, next, top);
        };
        if (!ok) {
          return false;
        }
      }
      return frames.isEmpty();
    }

    private static boolean endsOperand(Token t) {
      if (t.kind == Kind.IDENT) {
        String u = t.upper();
        return !isKeyword(u) || VALUE_KEYWORDS.contains(u) || u.equals("END");
      }
      return t.kind == Kind.NUMBER || t.kind == Kind.STRING || t.is(")");
    }

    private static boolean startsOperand(Token t) {
      if (t.kind == Kind.IDENT) {
        String u = t.upper();
        return !isKeyword(u) || VALUE_KEYWORDS.contains(u);
      }
      return t.kind == Kind.NUMBER || t.kind == Kind.STRING;
    }

    private static boolean isTypedLiteral(Token prev, Token t) {
      return t.kind == Kind.STRING && prev.kind == Kind.IDENT
          && LITERAL_PREFIXES.contains(prev.upper());
    }

    private boolean symbol(Token t, Token prev, Token next, Frame top) {
      switch (t.text) {
        case "(":
          frames.push(new Frame(pendingOpener));
          pendingOpener = "";
          return true;
        case ")":
          return !frames.isEmpty() && close(frames.pop());
        case ",":
          if (top == null) {
            return false;
          }
          top.commas++;
          return true;
        case ".":
          // Only between two identifiers; `t.*` and a dot after a call are Databricks-only here.
          return prev != null && prev.kind == Kind.IDENT && next != null && next.kind == Kind.IDENT;
        case "%":
        case "!=":
          ansi = false; // listed by the Ossie language but not standard SQL
          return true;
        default:
          return true;
      }
    }

    private boolean close(Frame f) {
      if (f.keywordForm && f.commas > 0) {
        return false; // a keyword form takes no extra arguments: TRIM(BOTH FROM s, 'x')
      }
      switch (f.opener) {
        case "POSITION":
          return f.keywordForm; // POSITION(sub IN str), not POSITION(sub, str)
        case "FILTER":
          return f.keywordForm; // FILTER (WHERE ...)
        case "SUBSTRING":
          if (!f.keywordForm) {
            ansi = false; // the comma form is the Ossie language; standard SQL uses FROM/FOR
          }
          break;
        default:
          break;
      }
      int[] arity = ARITY.get(f.opener);
      if (arity == null || f.keywordForm) {
        return true;
      }
      int args = f.empty ? 0 : f.commas + 1;
      return args >= arity[0] && args <= arity[1];
    }

    private boolean ident(int i, Token t, Token prev, Token next, Frame top) {
      if (prev != null && prev.is(".")) {
        return true; // a later part of a qualified name, already checked at its first part
      }
      String u = t.upper();
      if (next != null && next.is(".")) {
        return qualifiedName(i, t);
      }
      if (top != null && isCast(top) && prev != null && prev.isWord("AS")) {
        return castType(u, next);
      }
      if (next != null && next.is("(")) {
        return call(u, prev);
      }
      if (next != null && next.kind == Kind.STRING && !isKeyword(u)) {
        return typedLiteral(u);
      }
      return word(t, u, prev, next, top);
    }

    /** {@code dataset.field} is portable; any other head, deeper paths, or calls are not. */
    private boolean qualifiedName(int i, Token head) {
      if (!datasets.contains(head.text.toLowerCase(Locale.ROOT)) || !isColumnPosition()) {
        return false;
      }
      boolean twoParts = i + 2 < tokens.size() && tokens.get(i + 2).kind == Kind.IDENT;
      boolean deeper = i + 3 < tokens.size() && tokens.get(i + 3).is(".");
      boolean call = i + 3 < tokens.size() && tokens.get(i + 3).is("(");
      // `source.col` reads the raw column in Databricks, but `dataset.col` names the field when
      // a dimension of that name shadows it.
      return twoParts && !deeper && !call
          && !opaqueNames.contains(tokens.get(i + 2).text.toLowerCase(Locale.ROOT));
    }

    /** False for a name in a measure outside an aggregate call, which refers to a measure. */
    private boolean isColumnPosition() {
      if (!measure) {
        return true;
      }
      for (Frame f : frames) {
        if (f.aggregate) {
          return true;
        }
      }
      return false;
    }

    private boolean call(String u, Token prev) {
      boolean inOssie = OSSIE_FUNCTIONS.contains(u);
      boolean inAnsi = ANSI_FUNCTIONS.contains(u);
      if (inOssie || inAnsi) {
        ossie &= inOssie;
        // Standard SQL writes CURRENT_DATE and CURRENT_TIMESTAMP without parentheses.
        ansi &= inAnsi && !u.startsWith("CURRENT_");
        pendingOpener = u;
        return true;
      }
      if (!KEYWORDS_BEFORE_PAREN.contains(u)) {
        return false; // a function neither dialect lists, MEASURE(...) and window OVER included
      }
      if (u.equals("FILTER")) {
        if (prev == null || !prev.is(")")) {
          return false; // FILTER only qualifies an aggregate call: SUM(x) FILTER (WHERE ...)
        }
        ossie = false;
      } else if (u.equals("ILIKE")) {
        ansi = false;
      } else if (u.equals("GROUP") && (prev == null || !prev.isWord("WITHIN"))) {
        return false;
      }
      pendingOpener = u;
      return true;
    }

    private boolean typedLiteral(String u) {
      if (!LITERAL_PREFIXES.contains(u)) {
        return false; // TIMESTAMP '...' (session time zone), X'..', and other prefixes
      }
      ansi &= u.equals("DATE");
      return true;
    }

    private boolean castType(String u, Token next) {
      boolean params = next != null && next.is("(");
      if (PARAMETERIZED_TYPES.contains(u) != params) {
        return false; // DECIMAL needs (p, s); other types take none
      }
      if (params) {
        pendingOpener = "TYPE";
      }
      boolean inOssie = OSSIE_TYPES.contains(u);
      boolean inAnsi = ANSI_TYPES.contains(u);
      ossie &= inOssie;
      ansi &= inAnsi;
      return inOssie || inAnsi;
    }

    private boolean word(Token t, String u, Token prev, Token next, Frame top) {
      String opener = top == null ? "" : top.opener;
      switch (u) {
        case "AS":
          return isCast(top);
        case "FROM":
          if (opener.equals("EXTRACT")) {
            top.keywordForm = true;
            return true;
          }
          if (prev != null && prev.isWord("DISTINCT")) {
            return true; // IS [NOT] DISTINCT FROM
          }
          return standardKeywordForm(top, "SUBSTRING", "TRIM");
        case "FOR":
          return standardKeywordForm(top, "SUBSTRING");
        case "BOTH":
        case "LEADING":
        case "TRAILING":
          return standardKeywordForm(top, "TRIM");
        case "WHERE":
          if (opener.equals("FILTER") && prev != null && prev.is("(")) {
            top.keywordForm = true;
            return true;
          }
          return false;
        case "IN":
          if (opener.equals("POSITION")) {
            top.keywordForm = true;
          }
          return true;
        case "ILIKE":
          ansi = false;
          return true;
        case "ESCAPE":
          ossie = false;
          return true;
        case "NULLS":
          if (next != null && (next.isWord("FIRST") || next.isWord("LAST"))) {
            ossie = false;
            return true;
          }
          return false;
        default:
          if (REJECTED_WORDS.contains(u)) {
            return false;
          }
          // Any other identifier is a column, field, or date-part reference, unless its meaning
          // comes from the Metric View itself.
          return isKeyword(u)
              || (isColumnPosition() && !opaqueNames.contains(t.text.toLowerCase(Locale.ROOT)));
      }
    }

    /** FROM / FOR / BOTH inside SUBSTRING or TRIM: standard SQL, not the Ossie language. */
    private boolean standardKeywordForm(Frame top, String... openers) {
      if (top == null) {
        return false;
      }
      for (String opener : openers) {
        if (top.opener.equals(opener)) {
          top.keywordForm = true;
          ossie = false;
          return true;
        }
      }
      return false;
    }

    private static boolean isCast(Frame f) {
      return f != null && (f.opener.equals("CAST") || f.opener.equals("TRY_CAST"));
    }
  }
}
