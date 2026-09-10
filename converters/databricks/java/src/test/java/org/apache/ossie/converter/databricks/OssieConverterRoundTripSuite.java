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

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.TreeSet;

import org.junit.jupiter.api.Test;

/**
 * Property-based round-trip tests. For any generated model in the round-trippable subset,
 * converting
 * one direction and back preserves content:
 *
 *   MV -> Ossie -> MV : source, every dimension/measure name+expr+metadata, every join
 *     (name/source/condition/cardinality/rely) and nesting, and model filter/comment/
 *     materialization.
 *   Ossie -> MV -> Ossie : dataset names+sources+fields, relationship from/to/columns,
 *     metric name+expr, and model description.
 *
 * Uses a seeded java.util.Random so no
 * property-testing library is needed; each of NUM_SEEDS seeds is one generated model.
 */
public class OssieConverterRoundTripSuite {

  private static final int NUM_SEEDS = 300;
  private static final String[] AGGS = {"SUM", "COUNT", "AVG", "MIN", "MAX"};

  // --- Rnd: the small interface the builders depend on ---------------------
  private static final class Rnd {
    private final Random r;
    Rnd(long seed) {
      this.r = new Random(seed);
    }
    boolean chance(double p) {
      return r.nextDouble() < p;
    }
    int count(int lo, int hi) {
      return lo + r.nextInt(hi - lo + 1);
    }
    <T> T pick(List<T> seq) {
      return seq.get(r.nextInt(seq.size()));
    }
    String text() {
      String alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
      int n = r.nextInt(11);
      StringBuilder b = new StringBuilder();
      b.append(alnum.charAt(r.nextInt(alnum.length())));
      String alnumSpace = alnum + " ";
      for (int i = 0; i < n; i++) {
        b.append(alnumSpace.charAt(r.nextInt(alnumSpace.length())));
      }
      String s = b.toString().trim();
      return s.isEmpty() ? "x" : s;
    }
    String colname() {
      String lower = "abcdefghijklmnopqrstuvwxyz_";
      String rest = lower + "0123456789";
      StringBuilder b = new StringBuilder();
      b.append(lower.charAt(r.nextInt(lower.length())));
      int n = r.nextInt(8);
      for (int i = 0; i < n; i++) {
        b.append(rest.charAt(r.nextInt(rest.length())));
      }
      return b.toString();
    }
  }

  private static final class Names {
    private final Map<String, Integer> n = new HashMap<>();
    String next(String prefix) {
      int i = n.getOrDefault(prefix, 0);
      n.put(prefix, i + 1);
      return prefix + i;
    }
  }

  private static String threePart(Rnd rnd) {
    return rnd.colname() + "." + rnd.colname() + "." + rnd.colname();
  }

  private static void maybeMeta(Rnd rnd, Map<String, Object> target) {
    if (rnd.chance(0.4)) {
      target.put("comment", rnd.text());
    }
    if (rnd.chance(0.3)) {
      target.put("display_name", rnd.text());
    }
    if (rnd.chance(0.3)) {
      List<Object> syns = new ArrayList<>();
      int k = rnd.count(1, 3);
      for (int i = 0; i < k; i++) {
        syns.add(rnd.text());
      }
      target.put("synonyms", syns);
    }
    if (rnd.chance(0.25)) {
      Map<String, Object> fmt = new LinkedHashMap<>();
      String type = rnd.pick(List.of("number", "currency", "date"));
      fmt.put("type", type);
      if (type.equals("currency")) {
        fmt.put("currency_code", "USD");
      }
      target.put("format", fmt);
    }
  }

  // --- Metric View builder (for MV -> Ossie -> MV) -------------------------
  /**
   * Builds one join subtree. {@code qualPaths} collects the alias path of every join produced
   * (`j0`, `j0.j1`, ...) so a measure can be generated over a joined column.
   *
   * <p>{@code otm} marks the whole branch one-to-many: a Metric View requires one cardinality per
   * top-level branch, and a column on a one-to-many-joined table cannot be a dimension (the
   * converter drops it, as Databricks would reject it), so an otm branch contributes measures but
   * no dimensions. An otm join is also what makes the import stash the fact hint, so these seeds
   * are the ones that exercise that path.
   */
  private static Object[] buildJoin(Rnd rnd, Names names, String parentAlias, int depth,
      List<String> ancestorPath, List<String> qualPaths, boolean otm) {
    String name = names.next("j");
    List<String> path = new ArrayList<>(ancestorPath);
    path.add(name);
    String qual = String.join(".", path);
    qualPaths.add(qual);
    Map<String, Object> join = new LinkedHashMap<>();
    join.put("name", name);
    join.put("source", threePart(rnd));
    if (rnd.chance(0.5)) {
      int ncols = rnd.count(1, 2);
      List<Object> using = new ArrayList<>();
      for (int i = 0; i < ncols; i++) {
        using.add("u" + i + "_" + rnd.colname());
      }
      join.put("using", using);
    } else {
      int ncols = rnd.count(1, 2);
      List<String> clauses = new ArrayList<>();
      for (int i = 0; i < ncols; i++) {
        String pc = "fk" + i + "_" + rnd.colname();
        String cc = "pk" + i + "_" + rnd.colname();
        clauses.add(parentAlias + "." + pc + " = " + name + "." + cc);
      }
      join.put("on", String.join(" AND ", clauses));
    }
    if (otm) {
      join.put("cardinality", "one_to_many");
    } else if (rnd.chance(0.4)) {
      join.put("cardinality", "many_to_one");
    }
    if (rnd.chance(0.3)) {
      Map<String, Object> rely = new LinkedHashMap<>();
      rely.put("at_most_one_match", true);
      join.put("rely", rely);
    }
    List<Map<String, Object>> dims = new ArrayList<>();
    int nd = otm ? 0 : rnd.count(0, 2);
    for (int i = 0; i < nd; i++) {
      String col = rnd.colname();
      String expr = rnd.chance(0.7) ? qual + "." + col
          : qual + "." + col + " + " + qual + "." + rnd.colname();
      Map<String, Object> dim = new LinkedHashMap<>();
      dim.put("name", names.next("c"));
      dim.put("expr", expr);
      maybeMeta(rnd, dim);
      dims.add(dim);
    }
    if (depth < 2 && rnd.chance(0.35)) {
      // Cardinality is inherited: a Metric View rejects a branch that mixes the two.
      Object[] childResult = buildJoin(rnd, names, name, depth + 1, path, qualPaths, otm);
      List<Object> childJoins = new ArrayList<>();
      childJoins.add(childResult[0]);
      join.put("joins", childJoins);
      @SuppressWarnings("unchecked")
      List<Map<String, Object>> childDims = (List<Map<String, Object>>) childResult[1];
      dims.addAll(childDims);
    }
    return new Object[] {join, dims};
  }

  private static Map<String, Object> buildMetricView(Rnd rnd) {
    Names names = new Names();
    Map<String, Object> mv = new LinkedHashMap<>();
    mv.put("version", OssieConverter.MV_VERSION);
    mv.put("source", threePart(rnd));
    if (rnd.chance(0.4)) {
      mv.put("comment", rnd.text());
    }
    if (rnd.chance(0.3)) {
      mv.put("filter", rnd.colname() + " > 0");
    }
    List<Map<String, Object>> fields = new ArrayList<>();
    List<Object> joins = new ArrayList<>();
    int nsrc = rnd.count(0, 3);
    for (int i = 0; i < nsrc; i++) {
      String col = rnd.colname();
      String expr = rnd.chance(0.7) ? col : "UPPER(" + col + ")";
      Map<String, Object> dim = new LinkedHashMap<>();
      dim.put("name", names.next("c"));
      dim.put("expr", expr);
      maybeMeta(rnd, dim);
      fields.add(dim);
    }
    int njoins = rnd.count(0, 2);
    List<String> qualPaths = new ArrayList<>();
    for (int i = 0; i < njoins; i++) {
      Object[] jr = buildJoin(rnd, names, "source", 0, new ArrayList<>(), qualPaths,
          rnd.chance(0.25));
      joins.add(jr[0]);
      @SuppressWarnings("unchecked")
      List<Map<String, Object>> jdims = (List<Map<String, Object>>) jr[1];
      fields.addAll(jdims);
    }
    List<Map<String, Object>> measures = new ArrayList<>();
    int nmeas = rnd.count(0, 2);
    for (int i = 0; i < nmeas; i++) {
      Map<String, Object> m = new LinkedHashMap<>();
      m.put("name", names.next("c"));
      // A measure over a JOINED column -- `j0.col`, and `j0.j1.col` for a nested join -- is the
      // shape the qualifier rewrite exists to handle: the import de-aliases the path down to the
      // dataset name and the export expands it back. Generating only `AGG(bare_col)` left both
      // sides of that rewrite untested. A bare column is left bare deliberately: the export
      // strips the fact qualifier (`SUM(source.x)` -> `SUM(x)`, the Metric View idiom), so a
      // `source.`-qualified measure is a normalization rather than a round trip.
      m.put("expr", !qualPaths.isEmpty() && rnd.chance(0.4)
          ? rnd.pick(List.of(AGGS)) + "(" + rnd.pick(qualPaths) + "." + rnd.colname() + ")"
          : rnd.pick(List.of(AGGS)) + "(" + rnd.colname() + ")");
      if (rnd.chance(0.4)) {
        m.put("comment", rnd.text());
      }
      if (rnd.chance(0.3)) {
        List<Object> syns = new ArrayList<>();
        int k = rnd.count(1, 3);
        for (int j = 0; j < k; j++) {
          syns.add(rnd.text());
        }
        m.put("synonyms", syns);
      }
      if (rnd.chance(0.3)) {
        Map<String, Object> w = new LinkedHashMap<>();
        w.put("order", rnd.colname());
        w.put("range", "trailing 7 day");
        List<Object> window = new ArrayList<>();
        window.add(w);
        m.put("window", window);
      }
      measures.add(m);
    }
    if (!joins.isEmpty()) {
      mv.put("joins", joins);
    }
    // A Metric View requires at least one dimension or measure, so a model with neither is outside
    // the round-trippable subset (the converter rejects it, as Databricks would). Both counts can
    // independently come out zero, so add one dimension when that happens.
    if (fields.isEmpty() && measures.isEmpty()) {
      Map<String, Object> dim = new LinkedHashMap<>();
      dim.put("name", names.next("c"));
      dim.put("expr", rnd.colname());
      fields.add(dim);
    }
    if (!fields.isEmpty()) {
      mv.put("fields", fields);
    }
    if (!measures.isEmpty()) {
      mv.put("measures", measures);
    }
    if (rnd.chance(0.2)) {
      Map<String, Object> mat = new LinkedHashMap<>();
      mat.put("schedule", "every 6 hours");
      mat.put("mode", rnd.pick(List.of("relaxed", "strict")));
      mv.put("materialization", mat);
    }
    return mv;
  }

  // --- Ossie builder (for Ossie -> MV -> Ossie) ----------------------------
  private static Map<String, Object> ossieField(String name, String expr) {
    Map<String, Object> dialect = new LinkedHashMap<>();
    dialect.put("dialect", "DATABRICKS");
    dialect.put("expression", expr);
    List<Object> dialects = new ArrayList<>();
    dialects.add(dialect);
    Map<String, Object> expression = new LinkedHashMap<>();
    expression.put("dialects", dialects);
    Map<String, Object> field = new LinkedHashMap<>();
    field.put("name", name);
    field.put("expression", expression);
    return field;
  }

  private static Map<String, Object> buildOssie(Rnd rnd) {
    Names names = new Names();
    String fact = "fact";
    List<Map<String, Object>> datasets = new ArrayList<>();
    Map<String, Object> factDs = new LinkedHashMap<>();
    factDs.put("name", fact);
    factDs.put("source", "c.s." + fact);
    datasets.add(factDs);
    List<Map<String, Object>> relationships = new ArrayList<>();

    int nDims = rnd.count(0, 3);
    List<String> reachable = new ArrayList<>();
    reachable.add(fact);
    for (int i = 0; i < nDims; i++) {
      String dname = names.next("dim");
      String parent = rnd.pick(reachable);
      Map<String, Object> ds = new LinkedHashMap<>();
      ds.put("name", dname);
      ds.put("source", "c.s." + rnd.colname() + i);
      datasets.add(ds);
      reachable.add(dname);
      Map<String, Object> rel = new LinkedHashMap<>();
      rel.put("name", names.next("r"));
      rel.put("from", parent);
      rel.put("to", dname);
      if (rnd.chance(0.5)) {
        List<Object> cols = new ArrayList<>();
        int k = rnd.count(1, 2);
        for (int j = 0; j < k; j++) {
          cols.add(rnd.colname());
        }
        rel.put("from_columns", new ArrayList<>(cols));
        rel.put("to_columns", new ArrayList<>(cols));
      } else {
        int n = rnd.count(1, 2);
        List<Object> fcols = new ArrayList<>();
        List<Object> tcols = new ArrayList<>();
        for (int j = 0; j < n; j++) {
          fcols.add("fk" + j + "_" + rnd.colname());
          tcols.add("pk" + j + "_" + rnd.colname());
        }
        rel.put("from_columns", fcols);
        rel.put("to_columns", tcols);
      }
      relationships.add(rel);
    }
    for (Map<String, Object> ds : datasets) {
      List<Object> flds = new ArrayList<>();
      int nf = rnd.count(0, 3);
      for (int j = 0; j < nf; j++) {
        flds.add(ossieField(names.next("c"), rnd.colname()));
      }
      if (!flds.isEmpty()) {
        ds.put("fields", flds);
      }
    }
    List<Object> metrics = new ArrayList<>();
    int nm = rnd.count(0, 2);
    // Datasets other than the fact, whose columns a metric addresses as `<dataset>.col`. The
    // export rewrites that to the join path the Metric View needs (`parent.child.col` when the
    // dataset is nested) and the import maps it back, so a qualified metric exercises both halves.
    List<String> joined = new ArrayList<>(reachable.subList(1, reachable.size()));
    for (int i = 0; i < nm; i++) {
      String expr = !joined.isEmpty() && rnd.chance(0.4)
          ? rnd.pick(List.of(AGGS)) + "(" + rnd.pick(joined) + "." + rnd.colname() + ")"
          : rnd.pick(List.of(AGGS)) + "(" + rnd.colname() + ")";
      metrics.add(ossieField(names.next("c"), expr));
    }
    // The converted Metric View needs at least one dimension or measure, so a model whose datasets
    // have no fields and which declares no metrics is outside the round-trippable subset. Give the
    // first dataset a field when nothing else would produce a column.
    boolean anyField = false;
    for (Map<String, Object> ds : datasets) {
      if (!asList(ds.get("fields")).isEmpty()) {
        anyField = true;
        break;
      }
    }
    if (!anyField && metrics.isEmpty()) {
      List<Object> flds = new ArrayList<>();
      flds.add(ossieField(names.next("c"), rnd.colname()));
      datasets.get(0).put("fields", flds);
    }
    Map<String, Object> model = new LinkedHashMap<>();
    model.put("name", names.next("m"));
    if (rnd.chance(0.4)) {
      model.put("description", rnd.text());
    }
    model.put("datasets", datasets);
    if (!relationships.isEmpty()) {
      model.put("relationships", relationships);
    }
    if (!metrics.isEmpty()) {
      model.put("metrics", metrics);
    }
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("version", OssieConverter.OSSIE_VERSION);
    List<Object> models = new ArrayList<>();
    models.add(model);
    out.put("semantic_model", models);
    return out;
  }

  // --- Round-trip assertions -----------------------------------------------

  @SuppressWarnings("unchecked")
  private static Map<String, Object> asMap(Object x) {
    return x instanceof Map ? (Map<String, Object>) x : new LinkedHashMap<>();
  }

  @SuppressWarnings("unchecked")
  private static List<Object> asList(Object x) {
    return x instanceof List ? (List<Object>) x : new ArrayList<>();
  }

  private static String dumpYaml(Map<String, Object> obj) {
    return OssieConverter.dumpYaml(obj);
  }

  private static String condCanon(Map<String, Object> join) {
    List<Object> using = asList(join.get("using"));
    if (!using.isEmpty()) {
      Set<String> sorted = new TreeSet<>();
      for (Object u : using) {
        sorted.add(u.toString());
      }
      return "using:" + sorted;
    }
    Object on = join.get("on");
    if (on == null) {
      return "none";
    }
    Set<String> pairs = new TreeSet<>();
    for (String clause : on.toString().split("(?i)\\s+AND\\s+")) {
      String[] lr = clause.split("=", 2);
      pairs.add(lr[0].trim() + "=" + lr[1].trim());
    }
    return "on:" + pairs;
  }

  private static void flattenJoins(
      List<Object> joins, String parent, Map<String, String> acc, Set<String> edges) {
    for (Object jObj : joins) {
      Map<String, Object> j = asMap(jObj);
      String name = (String) j.get("name");
      acc.put(name, j.get("source") + "|" + condCanon(j) + "|" + j.get("cardinality")
          + "|" + j.get("rely"));
      edges.add(parent + "->" + name);
      flattenJoins(asList(j.get("joins")), name, acc, edges);
    }
  }

  private static List<Object> dims(Map<String, Object> mv) {
    List<Object> d = asList(mv.get("dimensions"));
    return !d.isEmpty() ? d : asList(mv.get("fields"));
  }

  private static String dimNorm(Map<String, Object> d) {
    return d.get("expr") + "|" + d.get("comment") + "|" + d.get("display_name")
        + "|" + d.get("synonyms") + "|" + d.get("format");
  }

  private static String measNorm(Map<String, Object> m) {
    return m.get("expr") + "|" + m.get("comment") + "|" + m.get("synonyms")
        + "|" + m.get("format") + "|" + m.get("window");
  }

  private static Map<String, String> byName(List<Object> items, boolean measure) {
    Map<String, String> out = new LinkedHashMap<>();
    for (Object o : items) {
      Map<String, Object> m = asMap(o);
      out.put((String) m.get("name"), measure ? measNorm(m) : dimNorm(m));
    }
    return out;
  }

  private void assertMvRoundTrip(Map<String, Object> mv, long seed) {
    String ossieYaml = OssieConverter.convertMetricViewToOssie(dumpYaml(mv), null).yaml;
    Map<String, Object> mv2 =
        asMap(OssieConverter.parseYaml(OssieConverter.convertOssieToMetricView(ossieYaml, null).yaml));

    String ctx = " (seed " + seed + ")";
    assertEquals(mv.get("source"), mv2.get("source"), "source" + ctx);
    assertEquals(mv.get("comment"), mv2.get("comment"), "comment" + ctx);
    assertEquals(mv.get("filter"), mv2.get("filter"), "filter" + ctx);
    assertEquals(mv.get("materialization"), mv2.get("materialization"), "materialization" + ctx);
    assertEquals(byName(dims(mv), false), byName(dims(mv2), false), "fields" + ctx);
    assertEquals(byName(asList(mv.get("measures")), true),
        byName(asList(mv2.get("measures")), true), "measures" + ctx);

    Map<String, String> a1 = new LinkedHashMap<>();
    Set<String> e1 = new LinkedHashSet<>();
    flattenJoins(asList(mv.get("joins")), "source", a1, e1);
    Map<String, String> a2 = new LinkedHashMap<>();
    Set<String> e2 = new LinkedHashSet<>();
    flattenJoins(asList(mv2.get("joins")), "source", a2, e2);
    assertEquals(a1, a2, "joins" + ctx);
    assertEquals(e1, e2, "join nesting" + ctx);
  }

  private static String exprOf(Map<String, Object> obj) {
    for (Object dObj : asList(asMap(obj.get("expression")).get("dialects"))) {
      Map<String, Object> d = asMap(dObj);
      if ("DATABRICKS".equals(d.get("dialect"))) {
        return (String) d.get("expression");
      }
    }
    return null;
  }

  private static Map<String, String> fieldsMap(Map<String, Object> ds) {
    Map<String, String> out = new LinkedHashMap<>();
    for (Object fObj : asList(ds.get("fields"))) {
      Map<String, Object> f = asMap(fObj);
      out.put((String) f.get("name"), exprOf(f));
    }
    return out;
  }

  private static Set<String> relSet(Map<String, Object> model) {
    Set<String> out = new LinkedHashSet<>();
    for (Object rObj : asList(model.get("relationships"))) {
      Map<String, Object> r = asMap(rObj);
      out.add(r.get("from") + "->" + r.get("to") + "|" + asList(r.get("from_columns"))
          + "|" + asList(r.get("to_columns")));
    }
    return out;
  }

  private void assertOssieRoundTrip(Map<String, Object> ossie, long seed) {
    String mvYaml = OssieConverter.convertOssieToMetricView(dumpYaml(ossie), null).yaml;
    Map<String, Object> ossie2 =
        asMap(OssieConverter.parseYaml(OssieConverter.convertMetricViewToOssie(mvYaml, null).yaml));

    String ctx = " (seed " + seed + ")";
    Map<String, Object> m1 = asMap(asList(ossie.get("semantic_model")).get(0));
    Map<String, Object> m2 = asMap(asList(ossie2.get("semantic_model")).get(0));

    Map<String, String> d1 = new LinkedHashMap<>();
    for (Object dsObj : asList(m1.get("datasets"))) {
      Map<String, Object> ds = asMap(dsObj);
      d1.put((String) ds.get("name"), ds.get("source") + "|" + fieldsMap(ds));
    }
    Map<String, String> d2 = new LinkedHashMap<>();
    for (Object dsObj : asList(m2.get("datasets"))) {
      Map<String, Object> ds = asMap(dsObj);
      d2.put((String) ds.get("name"), ds.get("source") + "|" + fieldsMap(ds));
    }
    assertEquals(d1, d2, "datasets" + ctx);
    assertEquals(relSet(m1), relSet(m2), "relationships" + ctx);

    Map<String, String> met1 = new LinkedHashMap<>();
    for (Object x : asList(m1.get("metrics"))) {
      met1.put((String) asMap(x).get("name"), exprOf(asMap(x)));
    }
    Map<String, String> met2 = new LinkedHashMap<>();
    for (Object x : asList(m2.get("metrics"))) {
      met2.put((String) asMap(x).get("name"), exprOf(asMap(x)));
    }
    assertEquals(met1, met2, "metrics" + ctx);
    assertEquals(m1.get("description"), m2.get("description"), "description" + ctx);
  }

  @Test
  public void metricViewRoundTripAcrossSeeds() {
    for (long seed = 0; seed < NUM_SEEDS; seed++) {
      assertMvRoundTrip(buildMetricView(new Rnd(seed)), seed);
    }
  }

  @Test
  public void ossieRoundTripAcrossSeeds() {
    for (long seed = 0; seed < NUM_SEEDS; seed++) {
      assertOssieRoundTrip(buildOssie(new Rnd(seed)), seed);
    }
  }
}
