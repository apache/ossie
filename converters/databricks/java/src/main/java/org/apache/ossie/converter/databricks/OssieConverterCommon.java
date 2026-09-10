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

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Function;
import java.util.regex.MatchResult;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.fasterxml.jackson.dataformat.yaml.YAMLGenerator;

import org.yaml.snakeyaml.DumperOptions;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;
import org.yaml.snakeyaml.nodes.Tag;
import org.yaml.snakeyaml.representer.Representer;
import org.yaml.snakeyaml.resolver.Resolver;

import org.apache.ossie.converter.databricks.OssieConverter.ConversionException;

/**
 * Shared constants, YAML I/O, and typed accessors for the Apache Ossie &lt;-&gt; Databricks
 * Metric View converter. The direction-specific logic lives in {@link OssieToMetricView}
 * (export) and {@link MetricViewToOssie} (import), which static-import these members. The public
 * entry points and shared types are re-exported through {@link OssieConverter}.
 */
// Map-based YAML manipulation: casts of the parsed Object graph to Map/List are inherently
// unchecked; the asMap/asList helpers guard them, so unchecked warnings here are expected.
@SuppressWarnings("unchecked")
final class OssieConverterCommon {

  // -- constants -------------------------------------------------------------
  static final String OSSIE_VERSION = "0.2.0.dev0";
  static final String MV_VERSION = "1.1";
  static final String VENDOR = "DATABRICKS";
  static final String DIALECT_DATABRICKS = "DATABRICKS";
  static final String DIALECT_ANSI = "ANSI_SQL";
  static final int SYNONYM_LIMIT = 10;
  static final int STASH_VERSION = 1;
  static final String STASH_SOURCE_KEY = "source_dataset";
  static final String CARD_ONE_TO_MANY = "one_to_many";
  static final String CARD_MANY_TO_ONE = "many_to_one";
  static final int MAX_JOIN_NODES = 200;

  static final Pattern IDENTIFIER_RE = Pattern.compile("^[A-Za-z_][A-Za-z0-9_]*$");
  static final Pattern SELECT_WITH_RE =
      Pattern.compile("(?i)^(select|with)\\b");

  static final ObjectMapper MAPPER = buildMapper();
  private static final ObjectMapper JSON_READER = new ObjectMapper()
      .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
      .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
  // Writer for the custom_extensions stash blob. The exact byte format is pinned by the
  // checked-in fixtures: a space after ':' and ', ' between entries, as in
  // {"_v": 1, "filter": "x"}.
  static final com.fasterxml.jackson.databind.ObjectWriter JSON_WRITER = buildJsonWriter();

  /** A MinimalPrettyPrinter (no newlines) using the stash blob's separators: ": " / ", ". */
  private static final class JsonDumpsPrinter
      extends com.fasterxml.jackson.core.util.MinimalPrettyPrinter {
    @Override
    public void writeObjectFieldValueSeparator(com.fasterxml.jackson.core.JsonGenerator g)
        throws java.io.IOException {
      g.writeRaw(": ");
    }
    @Override
    public void writeObjectEntrySeparator(com.fasterxml.jackson.core.JsonGenerator g)
        throws java.io.IOException {
      g.writeRaw(", ");
    }
    @Override
    public void writeArrayValueSeparator(com.fasterxml.jackson.core.JsonGenerator g)
        throws java.io.IOException {
      g.writeRaw(", ");
    }
  }

  private static com.fasterxml.jackson.databind.ObjectWriter buildJsonWriter() {
    // ESCAPE_NON_ASCII keeps the blob pure ASCII (every non-ASCII
    // char is emitted as a \\uXXXX escape). Set on the JsonFactory so it takes effect on the
    // generator. Jackson emits the hex in UPPERCASE; writeStash lowercases it so the blob is
    // byte-identical to the checked-in fixtures.
    com.fasterxml.jackson.core.JsonFactory jf = new com.fasterxml.jackson.core.JsonFactory();
    jf.enable(com.fasterxml.jackson.core.JsonGenerator.Feature.ESCAPE_NON_ASCII);
    return new ObjectMapper(jf).writer(new JsonDumpsPrinter());
  }

  // Matches a real unicode escape in serialized JSON so writeStash can lowercase its hex digits.
  //
  // The escape must be preceded by an EVEN number of backslashes, otherwise the `u` belongs to an
  // escaped backslash rather than to an escape sequence: a stashed value holding a literal
  // backslash followed by "uABCD" serializes with a doubled backslash, where the `u` is ordinary
  // text and must be left alone (lowercasing it there would corrupt the value). Group 1 captures
  // the (possibly empty) run of escaped backslashes so it can be re-emitted verbatim; group 2 is
  // the hex to lowercase.
  private static final Pattern UNICODE_ESCAPE_RE =
      Pattern.compile("(?<!\\\\)((?:\\\\\\\\)*)\\\\u([0-9A-Fa-f]{4})");

  private static ObjectMapper buildMapper() {
    // Quote scalars (do NOT minimize quotes) so a string like "1.1" or a bool-like token
    // is emitted quoted and never re-parsed as a number/boolean. Used for the WRITE path only;
    // reads go through the YAML-1.2 SnakeYAML loader below.
    YAMLFactory yf = YAMLFactory.builder()
        .disable(YAMLGenerator.Feature.WRITE_DOC_START_MARKER)
        .build();
    return new ObjectMapper(yf);
  }

  // A SnakeYAML Resolver with YAML 1.2 boolean semantics: only true/false are booleans, NOT the
  // YAML 1.1 tokens on/off/yes/no/y/n. A metric view join's bare `on:` key, and any "on"/"off"
  // string value, must read back as a string rather than silently becoming a boolean (a YAML 1.1
  // reader would coerce it).
  private static final class Yaml12Resolver extends Resolver {
    @Override
    protected void addImplicitResolvers() {
      addImplicitResolver(Tag.MERGE, MERGE, "<");
      addImplicitResolver(Tag.NULL, NULL, "~nN ");
      addImplicitResolver(Tag.NULL, EMPTY, null);
      addImplicitResolver(Tag.BOOL, java.util.regex.Pattern.compile(
          "^(?:true|True|TRUE|false|False|FALSE)$"), "tTfF");
      addImplicitResolver(Tag.INT, INT, "-+0123456789");
      addImplicitResolver(Tag.FLOAT, FLOAT, "-+0123456789.");
      addImplicitResolver(Tag.TIMESTAMP, TIMESTAMP, "0123456789");
    }
  }

  // SnakeYAML readers are stateful; create one per conversion instead of retaining thread-local
  // state on caller threads. Yaml also copies settings from loaderOptions into its constructor, so
  // the constructor and reader must receive the same configured instance.
  private static Yaml newYamlReader() {
    LoaderOptions loaderOptions = new LoaderOptions();
    loaderOptions.setAllowDuplicateKeys(false);
    DumperOptions dumperOptions = new DumperOptions();
    return new Yaml(new SafeConstructor(loaderOptions), new Representer(dumperOptions),
        dumperOptions, loaderOptions, new Yaml12Resolver());
  }

  static Object loadYaml(String s) {
    return newYamlReader().load(s);
  }

  private OssieConverterCommon() {}

  // -- typed accessors over parsed YAML (Object) ----------------------------
  @SuppressWarnings("unchecked")
  static Map<String, Object> asMap(Object x) {
    if (x instanceof Map) {
      return (Map<String, Object>) x;
    }
    return new LinkedHashMap<>();
  }

  @SuppressWarnings("unchecked")
  static List<Object> asList(Object x) {
    if (x instanceof List) {
      return (List<Object>) x;
    }
    return new ArrayList<>();
  }

  static Object get(Map<String, Object> m, String k) {
    return m.get(k);
  }

  static String str(Object x) {
    if (x == null) {
      return null;
    }
    return x.toString();
  }

  static List<String> strList(Object x) {
    List<String> out = new ArrayList<>();
    for (Object o : asList(x)) {
      if (o != null) {
        out.add(o.toString());
      }
    }
    return out;
  }

  // -- helpers ---------------------------------------------------------------
  static boolean isSimpleIdentifier(Object expr) {
    return expr instanceof String && IDENTIFIER_RE.matcher(((String) expr).trim()).matches();
  }

  /**
   * Applies {@code pattern -> replacement} to {@code sql}, but only to the parts of the expression
   * that are actual SQL code -- spans inside string literals ({@code '...'}, {@code "..."}),
   * backquoted identifiers, {@code -- line} comments, and {@code /* block *}{@code /} comments are
   * copied through untouched.
   *
   * <p>Measure expressions are rewritten to add or strip a fact qualifier, and a blind
   * {@code replaceAll} over the raw text also rewrites any occurrence inside a literal: a measure
   * such as {@code SUM(IF(source.region = 'source.us', amt, 0))} would silently become
   * {@code ... = 'us'}, changing the predicate and therefore the measure's value. Only code spans
   * may be rewritten.
   *
   * <p>{@code replacement} is treated as a literal string, not as a regex replacement template.
   */
  static String replaceOutsideLiterals(String sql, Pattern pattern, String replacement) {
    return replaceOutsideLiterals(sql, pattern, m -> replacement);
  }

  /**
   * As {@link #replaceOutsideLiterals(String, Pattern, String)}, but each match is replaced with
   * {@code replacer.apply(match)} -- for a rewrite whose result depends on what matched. The
   * returned text is inserted literally, not as a regex replacement template.
   */
  static String replaceOutsideLiterals(
      String sql, Pattern pattern, Function<MatchResult, String> replacer) {
    StringBuilder out = new StringBuilder(sql.length());
    int codeStart = 0;
    for (int[] span : literalSpans(sql)) {
      // Rewrite the code span that precedes this literal/comment, then copy the span verbatim.
      out.append(rewriteLiterally(sql.substring(codeStart, span[0]), pattern, replacer));
      out.append(sql, span[0], span[1]);
      codeStart = span[1];
    }
    out.append(rewriteLiterally(sql.substring(codeStart), pattern, replacer));
    return out.toString();
  }

  /**
   * True when {@code pattern} matches the SQL code of {@code sql} -- the find-only counterpart of
   * {@link #replaceOutsideLiterals}, with the same string-literal/comment blindness.
   *
   * <p>Needed wherever a decision is made from an expression's text: a bare
   * {@code pattern.matcher(sql).find()} also sees inside literals, so a column name that only
   * occurs in a string (as in {@code SUM(IF(region = 'us', amt, 0))}) reads as a reference to a
   * column named {@code us}.
   */
  static boolean findOutsideLiterals(String sql, Pattern pattern) {
    List<int[]> spans = literalSpans(sql);
    if (spans.isEmpty()) {
      return pattern.matcher(sql).find();
    }
    // Blank the literal/comment spans rather than removing them, so the surrounding code keeps its
    // token boundaries (the pattern may rely on \b or a look-around).
    StringBuilder code = new StringBuilder(sql);
    for (int[] span : spans) {
      for (int i = span[0]; i < span[1]; i++) {
        code.setCharAt(i, ' ');
      }
    }
    return pattern.matcher(code).find();
  }

  /**
   * Start (inclusive) and end (exclusive) offsets of every span in {@code sql} that is not SQL
   * code: string literals ({@code '...'}, {@code "..."}), backquoted identifiers, {@code -- line}
   * comments, and {@code /* block *}{@code /} comments.
   */
  private static List<int[]> literalSpans(String sql) {
    List<int[]> spans = new ArrayList<>();
    int i = 0;
    while (i < sql.length()) {
      char c = sql.charAt(i);
      int skipTo = -1;
      if (c == '\'' || c == '"' || c == '`') {
        skipTo = endOfQuoted(sql, i, c);
      } else if (c == '-' && i + 1 < sql.length() && sql.charAt(i + 1) == '-') {
        int nl = sql.indexOf('\n', i);
        skipTo = nl < 0 ? sql.length() : nl;
      } else if (c == '/' && i + 1 < sql.length() && sql.charAt(i + 1) == '*') {
        int end = sql.indexOf("*/", i + 2);
        skipTo = end < 0 ? sql.length() : end + 2;
      }
      if (skipTo < 0) {
        i++;
        continue;
      }
      spans.add(new int[] {i, skipTo});
      i = skipTo;
    }
    return spans;
  }

  /** Index just past the quoted span starting at {@code start}; handles doubled-quote escapes. */
  private static int endOfQuoted(String sql, int start, char quote) {
    int i = start + 1;
    while (i < sql.length()) {
      char c = sql.charAt(i);
      if (c == '\\' && quote != '`' && i + 1 < sql.length()) {
        i += 2;
        continue;
      }
      if (c == quote) {
        // A doubled quote is an escaped quote, not the end of the span.
        if (i + 1 < sql.length() && sql.charAt(i + 1) == quote) {
          i += 2;
          continue;
        }
        return i + 1;
      }
      i++;
    }
    // Unterminated literal: treat the remainder as part of the span rather than rewriting it.
    return sql.length();
  }

  private static String rewriteLiterally(
      String code, Pattern pattern, Function<MatchResult, String> replacer) {
    return pattern.matcher(code)
        .replaceAll(m -> Matcher.quoteReplacement(replacer.apply(m)));
  }

  /**
   * A pattern matching a maximal dotted run of {@code names} that qualifies a column reference --
   * {@code parent.child.} -- with group 1 holding the run without its trailing dot. Null when
   * {@code names} is empty.
   *
   * <p>The whole run is matched at once, rather than one name at a time, so a rewrite driven by
   * this pattern can never re-match a qualifier it has just produced: rewriting
   * {@code customer.region.} name-by-name would see the {@code region.} inside its own output and
   * qualify it a second time. The required trailing dot keeps a column that merely shares a name
   * with a dataset ({@code SUM(customer.region)}) from being read as a qualifier.
   */
  static Pattern qualifierChainPattern(Collection<String> names) {
    if (names.isEmpty()) {
      return null;
    }
    // Longest name first: regex alternation is ordered, so at a given position the longest name
    // wins (`nation_x` must never match as `nation`).
    List<String> sorted = new ArrayList<>(names);
    sorted.sort((a, b) -> b.length() - a.length());
    StringBuilder alternation = new StringBuilder();
    for (String name : sorted) {
      if (alternation.length() > 0) {
        alternation.append('|');
      }
      alternation.append(Pattern.quote(name));
    }
    String one = "(?:" + alternation + ")";
    return Pattern.compile("\\b(" + one + "(?:\\." + one + ")*)\\.");
  }

  /**
   * Rewrites each qualifier run {@code chain} matches by mapping the run's LAST name through
   * {@code resolve} -- the name closest to the column is the one being addressed, as
   * {@code resolveColumn} treats a dimension's alias path. A run whose leaf {@code resolve} does
   * not know is left alone. Literals and comments are skipped, and the pass is single, so the
   * result is independent of the order the names happen to be in.
   */
  static String rewriteQualifiers(String expr, Pattern chain, Function<String, String> resolve) {
    if (chain == null) {
      return expr;
    }
    return replaceOutsideLiterals(expr, chain, m -> {
      String leaf = lastIdentifier(m.group(1));
      String resolved = leaf == null ? null : resolve.apply(leaf);
      return resolved == null ? m.group() : resolved + ".";
    });
  }

  // Presence is tested by key, so a legitimately falsy non-string value (0, false) is returned;
  // a missing key, a null, or an empty/whitespace-only string is rejected.
  static Object require(Map<String, Object> obj, String key, String what) {
    if (!obj.containsKey(key) || obj.get(key) == null) {
      throw new ConversionException(what + " is missing required '" + key + "'");
    }
    Object value = obj.get(key);
    if (value instanceof String && ((String) value).trim().isEmpty()) {
      throw new ConversionException(what + " has an empty '" + key + "'");
    }
    return value;
  }

  // Like require(), but the value must be a string.
  static String requireStr(Map<String, Object> obj, String key, String what) {
    Object v = require(obj, key, what);
    if (v instanceof String) {
      return (String) v;
    }
    throw new ConversionException(
        what + ": '" + key + "' must be a string, got " + v.getClass().getSimpleName());
  }

  static String validateSource(Object source, String datasetName) {
    String s = source == null ? "" : source.toString().trim();
    if (s.isEmpty()) {
      throw new ConversionException("Dataset '" + datasetName + "': missing/empty 'source'");
    }
    if (SELECT_WITH_RE.matcher(s).find()) {
      return s;
    }
    String[] parts = s.split("\\.", -1);
    boolean ok = parts.length == 3;
    if (ok) {
      for (String p : parts) {
        if (p.isEmpty() || containsWhitespace(p)) {
          ok = false;
          break;
        }
      }
    }
    if (ok) {
      return s;
    }
    throw new ConversionException("Dataset '" + datasetName + "': source '" + source
        + "' must be a 3-part catalog.schema.table identifier or a SELECT/WITH subquery");
  }

  private static boolean containsWhitespace(String p) {
    for (int i = 0; i < p.length(); i++) {
      if (Character.isWhitespace(p.charAt(i))) {
        return true;
      }
    }
    return false;
  }

  static String pickExpression(Object osiExpression, String scope) {
    // Keep the raw (possibly non-string) values so
    // the type check below can fire; select DATABRICKS-or-ANSI by truthiness (`or`), so a
    // null/empty DATABRICKS expr falls through to ANSI; and raise on a non-string chosen
    // value rather than silently coercing it.
    if (osiExpression == null) {
      return null;
    }
    if (!(osiExpression instanceof Map)) {
      String reason = scope + ": 'expression' must be a mapping";
      throw ConversionException.invalidInput(reason, reason);
    }
    Map<String, Object> expression = asMap(osiExpression);
    Object dialectValues = get(expression, "dialects");
    if (dialectValues == null) {
      return null;
    }
    if (!(dialectValues instanceof List)) {
      String reason = scope + ": 'expression.dialects' must be a list";
      throw ConversionException.invalidInput(reason, reason);
    }
    Map<String, Object> dialects = new LinkedHashMap<>();
    int index = 0;
    for (Object d : (List<Object>) dialectValues) {
      if (!(d instanceof Map)) {
        String reason = scope + ": 'expression.dialects[" + index + "]' must be a mapping";
        throw ConversionException.invalidInput(reason, reason);
      }
      Map<String, Object> dm = (Map<String, Object>) d;
      dialects.put(str(get(dm, "dialect")), get(dm, "expression"));
      index++;
    }
    Object chosen = truthy(dialects.get(DIALECT_DATABRICKS))
        ? dialects.get(DIALECT_DATABRICKS) : dialects.get(DIALECT_ANSI);
    if (chosen != null && !(chosen instanceof String)) {
      throw new ConversionException(
          "expression must be a string, got " + chosen.getClass().getSimpleName());
    }
    return (String) chosen; // null when neither dialect present -> caller warns and skips
  }

  /**
   * Emptiness test used throughout the converter for optional YAML values: null, the empty string,
   * an empty list/map, numeric zero, and boolean false are all treated as absent; everything else
   * is present. Used for the DATABRICKS-or-ANSI expression fallthrough (so an empty or absent
   * DATABRICKS expr falls through to ANSI) and for optional-field mapping (so an empty
   * `comment`/`synonyms` is dropped rather than emitted as an empty value).
   */
  static boolean truthy(Object v) {
    if (v == null) {
      return false;
    }
    if (v instanceof String) {
      return !((String) v).isEmpty();
    }
    if (v instanceof java.util.Collection) {
      return !((java.util.Collection<?>) v).isEmpty();
    }
    if (v instanceof Map) {
      return !((Map<?, ?>) v).isEmpty();
    }
    if (v instanceof Number) {
      return ((Number) v).doubleValue() != 0.0;
    }
    if (v instanceof Boolean) {
      return (Boolean) v;
    }
    return true;
  }

  static List<String> synonymsOf(Object aiContext) {
    if (aiContext instanceof Map) {
      return strList(get(asMap(aiContext), "synonyms"));
    }
    return new ArrayList<>();
  }

  static String mergeDescription(Object description, Object aiContext) {
    String desc = str(description);
    if (aiContext instanceof String && !((String) aiContext).trim().isEmpty()) {
      String s = (String) aiContext;
      // When both are present they are joined with a newline; otherwise the
      // `if description` is a truthiness test, so an empty (or null) description returns the
      // ai_context alone rather than prepending a stray newline.
      return (desc != null && !desc.isEmpty()) ? desc + "\n" + s : s;
    }
    return desc;
  }

  private static List<Map<String, Object>> customExtensions(Map<String, Object> obj) {
    Object value = get(obj, "custom_extensions");
    if (value == null) {
      return new ArrayList<>();
    }
    if (!(value instanceof List)) {
      String reason = "'custom_extensions' must be a list";
      throw ConversionException.invalidInput(reason, reason);
    }
    List<Map<String, Object>> extensions = new ArrayList<>();
    int index = 0;
    for (Object extension : (List<Object>) value) {
      if (!(extension instanceof Map)) {
        String reason = "'custom_extensions[" + index + "]' must be a mapping";
        throw ConversionException.invalidInput(reason, reason);
      }
      extensions.add((Map<String, Object>) extension);
      index++;
    }
    return extensions;
  }

  static Map<String, Object> readStash(
      Map<String, Object> obj, OssieConverter.Notices notices, String scope) {
    Map<String, Object> stash = null;
    boolean sawExtra = false;
    for (Map<String, Object> ext : customExtensions(obj)) {
      if (VENDOR.equals(str(get(ext, "vendor_name")))) {
        if (stash != null) {
          // First-wins: keep the first DATABRICKS entry and ignore later ones, but warn so a
          // dropped entry is not lost silently.
          sawExtra = true;
          continue;
        }
        // A null or empty-string `data` is treated as an empty object rather than a parse error.
        Object dataValue = get(ext, "data");
        if (dataValue != null && !(dataValue instanceof String)) {
          String reason = "DATABRICKS custom_extensions data must be a string";
          throw ConversionException.invalidInput(reason, reason);
        }
        String data = (String) dataValue;
        if (data == null || data.isEmpty()) {
          data = "{}";
        }
        Object parsedValue;
        try {
          parsedValue = JSON_READER.readValue(data, Object.class);
        } catch (Exception e) {
          String reason = "DATABRICKS custom_extensions data is not valid JSON: " + e.getMessage();
          throw ConversionException.invalidInput(reason, reason, e);
        }
        if (!(parsedValue instanceof Map)) {
          String reason = "DATABRICKS custom_extensions data must be a JSON object";
          throw ConversionException.invalidInput(reason, reason);
        }
        stash = (Map<String, Object>) parsedValue;
        stash.remove("_v");
      }
    }
    if (sawExtra) {
      notices.warn(scope, "found more than one DATABRICKS custom_extensions entry; "
          + "using the first and ignoring the rest");
    }
    return stash != null ? stash : new LinkedHashMap<>();
  }

  static List<Object> foreignVendorExtensions(Map<String, Object> obj) {
    List<Object> out = new ArrayList<>();
    for (Map<String, Object> extension : customExtensions(obj)) {
      if (!VENDOR.equals(str(get(extension, "vendor_name")))) {
        out.add(extension);
      }
    }
    return out;
  }

  /** Attach a DATABRICKS custom_extensions entry holding `data`; no-op when empty. */
  @SuppressWarnings("unchecked")
  static void writeStash(Map<String, Object> obj, Map<String, Object> data) {
    if (data.isEmpty()) {
      return;
    }
    Map<String, Object> payload = new LinkedHashMap<>();
    payload.put("_v", STASH_VERSION);
    payload.putAll(data);
    String blob;
    try {
      blob = JSON_WRITER.writeValueAsString(payload);
    } catch (Exception e) {
      throw ConversionException.internalError("failed to serialize stash: " + e.getMessage(), e);
    }
    // Jackson emits unicode escapes with uppercase hex; the stash format uses lowercase. Lowercase
    // just the 4 hex digits of each real escape, preserving any escaped-backslash run in front of
    // it (see UNICODE_ESCAPE_RE). quoteReplacement is required: replaceAll still reads backslashes
    // in the returned string as replacement-template escapes, which would halve the run in
    // group(1) rather than re-emitting it verbatim.
    blob = UNICODE_ESCAPE_RE.matcher(blob)
        .replaceAll(m -> Matcher.quoteReplacement(
            m.group(1) + "\\u" + m.group(2).toLowerCase(Locale.ROOT)));
    List<Object> exts = (List<Object>) obj.computeIfAbsent("custom_extensions", k -> new ArrayList<>());
    for (Object extObj : exts) {
      Map<String, Object> ext = asMap(extObj);
      if (VENDOR.equals(str(get(ext, "vendor_name")))) {
        ext.put("data", blob);
        return;
      }
    }
    Map<String, Object> ext = new LinkedHashMap<>();
    ext.put("vendor_name", VENDOR);
    ext.put("data", blob);
    exts.add(ext);
  }

  /** Last dotted part of a table reference: `samples.tpch.lineitem` -> `lineitem`.
   * Trim the whole reference, take the final dotted
   * segment, then strip any surrounding backticks (so `cat.sch.`t`` -> `t`). */
  static String lastIdentifier(Object source) {
    if (source == null) {
      return null;
    }
    String s = source.toString().trim();
    int dot = s.lastIndexOf('.');
    String last = dot >= 0 ? s.substring(dot + 1) : s;
    int start = 0;
    int end = last.length();
    while (start < end && last.charAt(start) == '`') {
      start++;
    }
    while (end > start && last.charAt(end - 1) == '`') {
      end--;
    }
    return last.substring(start, end);
  }

  /** Parse YAML text into a plain value (YAML 1.2 booleans, matching the converter). */
  static Object parseYaml(String s) {
    try {
      return loadYaml(s);
    } catch (Exception e) {
      String message = "failed to parse YAML: " + e.getMessage();
      throw ConversionException.invalidInput(message, message, e);
    }
  }

  /** Serialize a value to YAML using the converter's write mapper (for tests without their
   * own jackson-yaml import). */
  static String dumpYaml(Object obj) {
    try {
      return MAPPER.writeValueAsString(obj);
    } catch (Exception e) {
      throw ConversionException.internalError("failed to serialize YAML: " + e.getMessage(), e);
    }
  }
}
