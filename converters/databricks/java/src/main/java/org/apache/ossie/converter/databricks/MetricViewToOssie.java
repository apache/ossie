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

import static org.apache.ossie.converter.databricks.OssieConverterCommon.CARD_MANY_TO_ONE;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.CARD_ONE_TO_MANY;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.DIALECT_DATABRICKS;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.MAPPER;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.MAX_JOIN_NODES;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.MV_VERSION;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.OSSIE_VERSION;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.SELECT_WITH_RE;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.STASH_SOURCE_KEY;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.asList;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.asMap;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.get;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.isSimpleIdentifier;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.loadYaml;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.lastIdentifier;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.qualifierChainPattern;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.requireStr;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.rewriteQualifiers;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.str;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.strList;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.truthy;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.validateSource;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.writeStash;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.apache.ossie.converter.databricks.OssieConverter.ConversionException;
import org.apache.ossie.converter.databricks.OssieConverter.Notices;
import org.apache.ossie.converter.databricks.OssieConverter.Result;

/**
 * IMPORT direction: Metric View v1.1 YAML -&gt; Apache Ossie semantic model. Shared helpers come
 * from {@link OssieConverterCommon}; the public entry point is re-exported through
 * {@link OssieConverter}.
 */
// Map-based YAML manipulation: casts of the parsed Object graph to Map/List are inherently
// unchecked; the asMap/asList helpers guard them, so unchecked warnings here are expected.
@SuppressWarnings("unchecked")
final class MetricViewToOssie {

  private static final String[] MODEL_STASH_KEYS = {"filter", "parameters", "materialization"};
  private static final String[] JOIN_STASH_KEYS = {"rely", "cardinality"};
  // Metric-View-only column fields with no Apache Ossie representation, preserved verbatim in the
  // DATABRICKS stash so an import can restore them. `window` and `partition` are measure-only
  // (they live on MeasureExpression); a dimension never carries them, so the shared key list is
  // simply never hit for those on the dimension path.
  private static final String[] COLUMN_STASH_KEYS = {"format", "window", "partition"};
  // Measure-only fields with no Apache Ossie Metric representation. A dimension's display_name
  // maps to the Field `label`, but the Ossie Metric schema has no `label`, so a measure's
  // display_name is preserved in the DATABRICKS stash instead. Kept separate from
  // COLUMN_STASH_KEYS so the dimension path (which already maps display_name to `label`) does not
  // also stash it.
  private static final String[] MEASURE_STASH_KEYS = {"display_name"};
  private static final Pattern NON_EQUI_RE = Pattern.compile("[<>!]=|<>|[<>]");
  private static final Pattern AND_SPLIT_RE = Pattern.compile("\\s+AND\\s+", Pattern.CASE_INSENSITIVE);
  private static final Pattern EQ_CLAUSE_RE = Pattern.compile("^\\s*(.+?)\\s*=\\s*(.+?)\\s*$");

  private MetricViewToOssie() {}

  static Result convertMetricViewToOssie(String mvYamlStr, String modelName) {
    Notices notices = new Notices();
    if (mvYamlStr == null || mvYamlStr.trim().isEmpty()) {
      throw ConversionException.invalidInput(
          "Invalid Metric View YAML: expected a mapping at the root", "the input is empty");
    }
    Object parsed;
    try {
      parsed = loadYaml(mvYamlStr);
    } catch (Exception e) {
      throw ConversionException.invalidInput(
          "Invalid Metric View YAML: " + e.getMessage(),
          "failed to parse YAML: " + e.getMessage(),
          e);
    }
    if (!(parsed instanceof Map)) {
      throw ConversionException.invalidInput(
          "Invalid Metric View YAML: expected a mapping at the root",
          "it is not a mapping at the root");
    }
    Map<String, Object> view = asMap(parsed);
    Object versionValue = get(view, "version");
    String version = str(versionValue);
    if (versionValue == null || version.trim().isEmpty()) {
      throw ConversionException.invalidInput(
          "Invalid Metric View YAML: missing required 'version' field",
          "it is missing the required 'version' field");
    }
    if (!MV_VERSION.equals(version)) {
      throw ConversionException.unsupportedVersion(
          "Unsupported Metric View version '" + version
              + "'. This converter targets v" + MV_VERSION + " only.",
          version);
    }
    Map<String, Object> model = convertView(view, modelName, notices);
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("version", OSSIE_VERSION);
    List<Object> models = new ArrayList<>();
    models.add(model);
    out.put("semantic_model", models);
    return serializeOssie(out, notices);
  }

  static Result serializeOssie(Map<String, Object> out, Notices notices) {
    try {
      return new Result(MAPPER.writeValueAsString(out), notices.toList());
    } catch (Exception e) {
      throw ConversionException.internalError(
          "failed to serialize Apache Ossie YAML: " + e.getMessage(), e);
    }
  }

  private static Map<String, Object> convertView(
      Map<String, Object> view, String modelName, Notices notices) {
    Object source = get(view, "source");
    if (source == null || source.toString().isEmpty()) {
      throw new ConversionException("Metric View is missing required 'source'");
    }
    // Same subquery test validateSource applies below, so the two cannot drift.
    boolean isSql = SELECT_WITH_RE.matcher(source.toString().trim()).find();
    String lastId = lastIdentifier(source);
    String factName;
    if (modelName != null) {
      factName = modelName;
    } else if (!isSql && lastId != null && isSimpleIdentifier(lastId)) {
      factName = lastId;
    } else {
      factName = "metric_view";
    }
    validateSource(source, factName);

    List<Map<String, Object>> datasets = new ArrayList<>();
    Map<String, Object> factDs = new LinkedHashMap<>();
    factDs.put("name", factName);
    factDs.put("source", source);
    datasets.add(factDs);
    List<Map<String, Object>> relationships = new ArrayList<>();
    // Joins with no schema-valid Ossie relationship (a non-decomposable `on`) are collected here
    // and stashed under the model's DATABRICKS custom_extensions instead of `relationships`.
    List<Map<String, Object>> complexJoins = new ArrayList<>();
    Map<String, String> aliasToDataset = new HashMap<>();
    aliasToDataset.put("source", factName);
    aliasToDataset.put(factName, factName);
    Set<String> seenNames = new HashSet<>();
    seenNames.add(factName.trim().toLowerCase(Locale.ROOT));

    walk(factName, "source", asList(get(view, "joins")), datasets, relationships,
        complexJoins, aliasToDataset, seenNames, notices);

    // The reverse conversion bounds how many datasets it will build (see buildJoinTree), so a
    // Metric View that expands past that bound could not be imported again. Reject it here to keep
    // MV -> Ossie -> MV symmetric rather than emitting a model that fails on the way back in.
    if (datasets.size() > MAX_JOIN_NODES) {
      throw new ConversionException("Metric View '" + factName + "' has " + datasets.size()
          + " datasets; at most " + MAX_JOIN_NODES + " are supported.");
    }

    // `fields` is a v1.1 alias for `dimensions`: an empty `dimensions: []` falls through to
    // `fields`, and the "both set" warning fires only when BOTH are non-empty (not merely
    // present).
    List<Object> dimList = asList(get(view, "dimensions"));
    List<Object> fieldList = asList(get(view, "fields"));
    if (!dimList.isEmpty() && !fieldList.isEmpty()) {
      notices.warn("view", "both 'dimensions' and 'fields' are set; 'fields' is a v1.1 alias "
          + "for 'dimensions', so the 'fields' list is ignored");
    }
    Map<String, List<Object>> fieldsByDataset = new LinkedHashMap<>();
    for (Map<String, Object> d : datasets) {
      fieldsByDataset.put((String) d.get("name"), new ArrayList<>());
    }
    List<Object> dims = !dimList.isEmpty() ? dimList : fieldList;
    for (Object dimObj : dims) {
      Map<String, Object> dim = asMap(dimObj);
      if (isWildcard(dim)) {
        notices.warn("dimension", "wildcard column '" + str(get(dim, "expr"))
            + "' has no Apache Ossie field representation; skipped");
        continue;
      }
      Object[] converted = convertDimension(dim, aliasToDataset, factName);
      fieldsByDataset.get((String) converted[0]).add(converted[1]);
    }
    for (Map<String, Object> d : datasets) {
      List<Object> flds = fieldsByDataset.get((String) d.get("name"));
      if (!flds.isEmpty()) {
        d.put("fields", flds);
      }
    }

    List<Object> metrics = new ArrayList<>();
    // Fixed once the join tree is walked, so compile it once for all measures.
    Pattern aliasHead = qualifierChainPattern(aliasToDataset.keySet());
    for (Object mObj : asList(get(view, "measures"))) {
      Map<String, Object> m = asMap(mObj);
      if (isWildcard(m)) {
        notices.warn("measure", "wildcard measure '" + str(get(m, "expr"))
            + "' has no Apache Ossie metric representation; skipped");
        continue;
      }
      metrics.add(convertMeasure(m, aliasHead, aliasToDataset));
    }

    Map<String, Object> model = new LinkedHashMap<>();
    model.put("name", factName);
    if (truthy(get(view, "comment"))) {
      model.put("description", get(view, "comment"));
    }
    model.put("datasets", datasets);
    if (!relationships.isEmpty()) {
      model.put("relationships", relationships);
    }
    if (!metrics.isEmpty()) {
      model.put("metrics", metrics);
    }

    Map<String, Object> modelStash = new LinkedHashMap<>();
    for (String k : MODEL_STASH_KEYS) {
      if (view.containsKey(k)) {
        modelStash.put(k, view.get(k));
      }
    }
    if (hasOtm(asList(get(view, "joins")))) {
      modelStash.put(STASH_SOURCE_KEY, factName);
    }
    if (!complexJoins.isEmpty()) {
      modelStash.put("complex_joins", complexJoins);
    }
    writeStash(model, modelStash);
    return model;
  }

  private static void walk(String parentName, String parentAlias, List<Object> joins,
      List<Map<String, Object>> datasets, List<Map<String, Object>> relationships,
      List<Map<String, Object>> complexJoins, Map<String, String> aliasToDataset,
      Set<String> seenNames, Notices notices) {
    for (Object joinObj : joins) {
      Map<String, Object> join = asMap(joinObj);
      String child = requireStr(join, "name", "join");
      if (child.trim().equalsIgnoreCase("source")) {
        throw new ConversionException(
            "Join name 'source' is reserved for the fact source; rename the join.");
      }
      if (!seenNames.add(child.trim().toLowerCase(Locale.ROOT))) {
        throw new ConversionException("Duplicate dataset/join name '" + child
            + "'; Metric View join names and the source must be distinct (case-insensitively).");
      }
      Map<String, Object> childDs = new LinkedHashMap<>();
      childDs.put("name", child);
      // Validated with the same rule the export applies to a dataset source, so a Metric View that
      // imports cleanly is always exportable again (a 2-part join source used to import fine and
      // then fail on the way back out).
      String childSource = requireStr(join, "source", "join '" + child + "'");
      validateSource(childSource, child);
      childDs.put("source", childSource);
      datasets.add(childDs);
      aliasToDataset.put(child, child);
      Map<String, Object> rel = convertJoin(join, parentName, parentAlias, child, notices);
      // convertJoin omits from/to columns for a non-decomposable `on`; those entries have no valid
      // Ossie relationship form, so they go to the model-level stash instead of `relationships`.
      if (rel.containsKey("from_columns")) {
        relationships.add(rel);
      } else {
        complexJoins.add(rel);
      }
      // rely.at_most_one_match on a many_to_one join -> recover a unique_key on the child.
      Map<String, Object> rely = asMap(get(join, "rely"));
      List<String> toCols = strList(get(rel, "to_columns"));
      if (child.equals(str(get(rel, "to"))) && !toCols.isEmpty()
          && Boolean.TRUE.equals(rely.get("at_most_one_match"))) {
        List<Object> uk = new ArrayList<>();
        uk.add(new ArrayList<>(toCols));
        childDs.put("unique_keys", uk);
      }
      walk(child, child, asList(get(join, "joins")), datasets, relationships,
          complexJoins, aliasToDataset, seenNames, notices);
    }
  }

  private static boolean hasOtm(List<Object> joins) {
    for (Object jObj : joins) {
      Map<String, Object> j = asMap(jObj);
      if (CARD_ONE_TO_MANY.equalsIgnoreCase(str(get(j, "cardinality")))) {
        return true;
      }
      if (hasOtm(asList(get(j, "joins")))) {
        return true;
      }
    }
    return false;
  }

  private static Map<String, Object> convertJoin(
      Map<String, Object> join, String parentName, String parentAlias, String child,
      Notices notices) {
    boolean hasUsing = get(join, "using") != null && !asList(get(join, "using")).isEmpty();
    boolean hasOn = str(get(join, "on")) != null && !str(get(join, "on")).isEmpty();
    if (!hasUsing && !hasOn) {
      throw new ConversionException("Join '" + child + "' has no join condition (empty or "
          + "absent 'on'/'using'); condition-less (cross) joins have no Apache Ossie "
          + "relationship representation.");
    }
    Object[] decomposed = decomposeOn(join, parentAlias, parentName, child);
    List<String> parentCols = (List<String>) decomposed[0];
    List<String> childCols = (List<String>) decomposed[1];
    String rawOn = (String) decomposed[2];
    if (rawOn != null) {
      // The `on` is not an equi-join of simple `alias.column` pairs (it has a non-equi operator, a
      // SQL-function-wrapped key, or an extra filter predicate), so an Apache Ossie relationship
      // cannot represent it (from/to columns are required). Rather than abort the whole
      // conversion, warn here; the columns-less entry built below is stashed under the model's
      // custom_extensions (complex_joins) so it round-trips, instead of an invalid relationship.
      notices.warn("join '" + child + "'", "non-equi or unsupported join condition ('on: " + rawOn
          + "') has no Apache Ossie relationship representation; preserved verbatim in the model's "
          + "custom_extensions");
    } else if (!hasOn && hasUsing && parentCols.isEmpty()) {
      // Only fall back to `using` when there is no `on` to decompose (see decomposeOn: `on` wins).
      List<String> using = strList(get(join, "using"));
      parentCols = new ArrayList<>(using);
      childCols = new ArrayList<>(using);
    }

    String cardinality = str(get(join, "cardinality"));
    if (cardinality == null) {
      cardinality = CARD_MANY_TO_ONE;
    }
    Map<String, Object> rel = new LinkedHashMap<>();
    boolean childIsFrom = cardinality.toLowerCase(Locale.ROOT).equals(CARD_ONE_TO_MANY);
    if (childIsFrom) {
      rel.put("name", child + "_to_" + parentName);
      rel.put("from", child);
      rel.put("to", parentName);
    } else {
      rel.put("name", parentName + "_to_" + child);
      rel.put("from", parentName);
      rel.put("to", child);
    }
    if (rawOn != null) {
      // An Apache Ossie relationship requires from/to columns, so a non-decomposable `on` has no
      // valid relationship form. Return a columns-less entry carrying the raw clause and the
      // MV-only join attributes (rely/cardinality) as flat keys; walk() routes it to the
      // model-level DATABRICKS stash (complex_joins), and the reverse converter rebuilds it.
      rel.put("on", rawOn);
      for (String k : JOIN_STASH_KEYS) {
        if (join.containsKey(k)) {
          rel.put(k, join.get(k));
        }
      }
      return rel;
    }
    rel.put("from_columns", childIsFrom ? childCols : parentCols);
    rel.put("to_columns", childIsFrom ? parentCols : childCols);
    Map<String, Object> stash = new LinkedHashMap<>();
    for (String k : JOIN_STASH_KEYS) {
      if (join.containsKey(k)) {
        stash.put(k, join.get(k));
      }
    }
    // Preserve the original `on` verbatim only when rebuilding it from the from/to columns would
    // not reproduce it: a fact side qualified by the source table name rather than `source`, or an
    // `on` over equal columns that rebuilds as `using`. For the canonical
    // `source.<a> = <child>.<b>` form the rebuild is exact, so nothing is stashed. buildJoin
    // restores a stashed `on` in preference to rebuilding from columns.
    if (hasOn) {
      String originalOn = str(get(join, "on"));
      if (!originalOn.equals(rebuiltOn(parentAlias, child, parentCols, childCols))) {
        stash.put("on", originalOn);
      }
    }
    writeStash(rel, stash);
    return rel;
  }

  /**
   * The `on` clause the reverse converter (buildJoin) would rebuild from these equi-join columns,
   * or {@code null} when it would instead emit `using` (equal column lists). Used to decide whether
   * an equi-join's original `on` must be stashed to round-trip verbatim.
   */
  private static String rebuiltOn(
      String parentAlias, String child, List<String> parentCols, List<String> childCols) {
    if (parentCols.equals(childCols)) {
      return null;
    }
    List<String> clauses = new ArrayList<>();
    for (int i = 0; i < parentCols.size(); i++) {
      clauses.add(parentAlias + "." + parentCols.get(i) + " = " + child + "." + childCols.get(i));
    }
    return String.join(" AND ", clauses);
  }

  /** Returns {parentCols, childCols, rawOn}; rawOn non-null means reject. */
  private static Object[] decomposeOn(
      Map<String, Object> join, String parentAlias, String parentName, String childAlias) {
    // A join may carry both `on` and `using` (Metric View validation only requires that at least
    // one is present). `on` takes precedence, matching how the engine resolves the join criteria
    // in DataModelUtils.getJoinCriteriaExpression: `case (Some(on), _) => ...`. Falling back to
    // `using` when `on` is present would silently join on different columns than the view does.
    String on = str(get(join, "on"));
    if (on == null || on.isEmpty()) {
      // No usable `on`; the caller derives the columns from `using`.
      return new Object[] {new ArrayList<String>(), new ArrayList<String>(), null};
    }
    Set<String> parentAliases = new HashSet<>();
    parentAliases.add(parentAlias);
    parentAliases.add(parentName);
    boolean allowBare = "source".equals(parentAlias);
    List<String> fromCols = new ArrayList<>();
    List<String> toCols = new ArrayList<>();
    for (String clause : AND_SPLIT_RE.split(on)) {
      if (NON_EQUI_RE.matcher(clause).find()) {
        return new Object[] {null, null, on};
      }
      Matcher m = EQ_CLAUSE_RE.matcher(clause);
      if (!m.matches()) {
        return new Object[] {null, null, on};
      }
      String[] left = splitAlias(m.group(1));
      String[] right = splitAlias(m.group(2));
      String la = left[0];
      String lc = left[1];
      String ra = right[0];
      String rc = right[1];
      if (!(isSimpleIdentifier(lc) && isSimpleIdentifier(rc))) {
        return new Object[] {null, null, on};
      }
      boolean lParent = parentAliases.contains(la) || (la == null && allowBare);
      boolean rParent = parentAliases.contains(ra) || (ra == null && allowBare);
      if (childAlias.equals(la) && rParent) {
        fromCols.add(rc);
        toCols.add(lc);
      } else if (childAlias.equals(ra) && lParent) {
        fromCols.add(lc);
        toCols.add(rc);
      } else {
        return new Object[] {null, null, on};
      }
    }
    return new Object[] {fromCols, toCols, null};
  }

  /** `customer.c_custkey` -> {"customer","c_custkey"}; `x` -> {null,"x"}. */
  private static String[] splitAlias(String operand) {
    operand = operand.trim();
    int dot = operand.indexOf('.');
    if (dot >= 0) {
      return new String[] {operand.substring(0, dot).trim(), operand.substring(dot + 1).trim()};
    }
    return new String[] {null, operand};
  }

  private static Object[] convertDimension(
      Map<String, Object> dim, Map<String, String> aliasToDataset, String factName) {
    String name = requireStr(dim, "name", "dimension");
    String expr = requireStr(dim, "expr", "dimension '" + name + "'");
    String[] resolved = resolveColumn(expr, aliasToDataset, factName);
    String dsName = resolved[0];
    String ossieExpr = resolved[1];

    Map<String, Object> field = new LinkedHashMap<>();
    field.put("name", name);
    field.put("expression", dialectExpr(ossieExpr));
    if (truthy(get(dim, "comment"))) {
      field.put("description", get(dim, "comment"));
    }
    if (truthy(get(dim, "display_name"))) {
      field.put("label", get(dim, "display_name"));
    }
    if (truthy(get(dim, "synonyms"))) {
      Map<String, Object> ai = new LinkedHashMap<>();
      ai.put("synonyms", new ArrayList<>(asList(get(dim, "synonyms"))));
      field.put("ai_context", ai);
    }
    Map<String, Object> stash = new LinkedHashMap<>();
    for (String k : COLUMN_STASH_KEYS) {
      if (dim.containsKey(k)) {
        stash.put(k, dim.get(k));
      }
    }
    writeStash(field, stash);
    return new Object[] {dsName, field};
  }

  /** Map a dimension expression to {dataset_name, de-aliased_expression}. */
  private static String[] resolveColumn(
      String expr, Map<String, String> aliasToDataset, String factName) {
    // The -1 limit keeps trailing empty segments, so a malformed `customer.` files under
    // `customer` rather than under the fact.
    String[] segments = expr.split("\\.", -1);
    for (int i = 0; i < segments.length; i++) {
      segments[i] = segments[i].trim();
    }
    String ds = null;
    int i = 0;
    while (i < segments.length - 1 && aliasToDataset.containsKey(segments[i])) {
      ds = aliasToDataset.get(segments[i]);
      i++;
    }
    if (ds == null) {
      return new String[] {factName, expr};
    }
    StringBuilder rest = new StringBuilder();
    for (int j = i; j < segments.length; j++) {
      if (j > i) {
        rest.append(".");
      }
      rest.append(segments[j]);
    }
    String restStr = rest.toString();
    return isSimpleIdentifier(restStr) ? new String[] {ds, restStr} : new String[] {ds, expr};
  }

  /**
   * Converts a measure to an Apache Ossie metric, de-aliasing the expression's column qualifiers:
   * a Metric View addresses a joined column by its full join path, an Apache Ossie metric by the
   * dataset name, so {@code parent.child.col} becomes {@code child.col} and {@code source.col}
   * becomes {@code <fact>.col}. This is the inverse of {@code OssieToMetricView.qualifyMeasure},
   * and it is what {@code resolveColumn} already does for a dimension -- measures used to keep the
   * raw path, so a nested one survived only by accident.
   */
  private static Map<String, Object> convertMeasure(
      Map<String, Object> measure, Pattern aliasHead, Map<String, String> aliasToDataset) {
    String name = requireStr(measure, "name", "measure");
    String rawExpr = requireStr(measure, "expr", "measure '" + name + "'");
    String expr = rewriteQualifiers(rawExpr, aliasHead, aliasToDataset::get);
    Map<String, Object> metric = new LinkedHashMap<>();
    metric.put("name", name);
    metric.put("expression", dialectExpr(expr));
    if (truthy(get(measure, "comment"))) {
      metric.put("description", get(measure, "comment"));
    }
    if (truthy(get(measure, "synonyms"))) {
      Map<String, Object> ai = new LinkedHashMap<>();
      ai.put("synonyms", new ArrayList<>(asList(get(measure, "synonyms"))));
      metric.put("ai_context", ai);
    }
    Map<String, Object> stash = new LinkedHashMap<>();
    for (String k : COLUMN_STASH_KEYS) {
      if (measure.containsKey(k)) {
        stash.put(k, measure.get(k));
      }
    }
    for (String k : MEASURE_STASH_KEYS) {
      if (measure.containsKey(k)) {
        stash.put(k, measure.get(k));
      }
    }
    writeStash(metric, stash);
    return metric;
  }

  private static Map<String, Object> dialectExpr(String expr) {
    Map<String, Object> dialect = new LinkedHashMap<>();
    dialect.put("dialect", DIALECT_DATABRICKS);
    dialect.put("expression", expr);
    List<Object> dialects = new ArrayList<>();
    dialects.add(dialect);
    Map<String, Object> expression = new LinkedHashMap<>();
    expression.put("dialects", dialects);
    return expression;
  }

  private static boolean isWildcard(Map<String, Object> col) {
    return !col.containsKey("name");
  }
}
