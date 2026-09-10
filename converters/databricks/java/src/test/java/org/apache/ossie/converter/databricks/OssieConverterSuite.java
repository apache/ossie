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
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

/**
 * Example-based tests for the Apache Ossie &lt;-&gt; Metric View converter, plus the fixture
 * comparisons that pin the expected output of both directions.
 */
public class OssieConverterSuite {

  private static Object export(String osi, String source) {
    return OssieConverter.parseYaml(
        OssieConverter.convertOssieToMetricView(osi, source).yaml);
  }

  private static final class ThrowingBean {
    public String getValue() {
      throw new IllegalStateException("expected serialization failure");
    }
  }

  private static Map<String, Object> valueThatFailsSerialization() {
    Map<String, Object> value = new HashMap<>();
    value.put("bad", new ThrowingBean());
    return value;
  }

  private static String ossieModelWithBody(String body) {
    return "version: '" + OssieConverter.OSSIE_VERSION + "'\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + body;
  }

  private static String cascadeNotice(String kind, String name, String reference) {
    return "[" + kind + " '" + name + "'] references dropped '" + reference
        + "'; dropping (downstream of a dropped field/metric)";
  }

  private static void assertInvalidOssieInput(String yaml, String expectedReason) {
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(yaml, null));
    assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, e.getKind());
    assertEquals(expectedReason, e.getReason());
  }

  @Test
  public void fixtureAStarSchemaExportsToExpectedMetricView() {
    String osi =
        "version: \"0.2.0.dev0\"\n"
        + "semantic_model:\n"
        + "  - name: sales\n"
        + "    description: Sales orders with customer attributes\n"
        + "    datasets:\n"
        + "      - name: orders\n"
        + "        source: samples.tpch.orders\n"
        + "        primary_key: [o_orderkey]\n"
        + "        description: One row per order\n"
        + "        fields:\n"
        + "          - name: o_orderkey\n"
        + "            expression:\n"
        + "              dialects:\n"
        + "                - dialect: DATABRICKS\n"
        + "                  expression: o_orderkey\n"
        + "            description: Order identifier\n"
        + "          - name: o_orderdate\n"
        + "            expression:\n"
        + "              dialects:\n"
        + "                - dialect: DATABRICKS\n"
        + "                  expression: o_orderdate\n"
        + "            label: Order Date\n"
        + "            ai_context:\n"
        + "              synonyms: [order date, date]\n"
        + "      - name: customer\n"
        + "        source: samples.tpch.customer\n"
        + "        primary_key: [c_custkey]\n"
        + "        fields:\n"
        + "          - name: c_name\n"
        + "            expression:\n"
        + "              dialects:\n"
        + "                - dialect: DATABRICKS\n"
        + "                  expression: c_name\n"
        + "            description: Customer name\n"
        + "    relationships:\n"
        + "      - name: orders_to_customer\n"
        + "        from: orders\n"
        + "        to: customer\n"
        + "        from_columns: [o_custkey]\n"
        + "        to_columns: [c_custkey]\n"
        + "    metrics:\n"
        + "      - name: total_revenue\n"
        + "        expression:\n"
        + "          dialects:\n"
        + "            - dialect: DATABRICKS\n"
        + "              expression: SUM(o_totalprice)\n"
        + "        description: Total order revenue\n"
        + "        ai_context:\n"
        + "          synonyms: [revenue, total revenue, sales]\n"
        + "      - name: order_count\n"
        + "        expression:\n"
        + "          dialects:\n"
        + "            - dialect: DATABRICKS\n"
        + "              expression: COUNT(*)\n"
        + "        description: Number of orders\n";

    String expected =
        "version: '1.1'\n"
        + "source: samples.tpch.orders\n"
        + "comment: Sales orders with customer attributes\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: samples.tpch.customer\n"
        + "  on: source.o_custkey = customer.c_custkey\n"
        + "  rely:\n"
        + "    at_most_one_match: true\n"
        + "dimensions:\n"
        + "- name: o_orderkey\n"
        + "  expr: o_orderkey\n"
        + "  comment: Order identifier\n"
        + "- name: o_orderdate\n"
        + "  expr: o_orderdate\n"
        + "  display_name: Order Date\n"
        + "  synonyms:\n"
        + "  - order date\n"
        + "  - date\n"
        + "- name: c_name\n"
        + "  expr: customer.c_name\n"
        + "  comment: Customer name\n"
        + "measures:\n"
        + "- name: total_revenue\n"
        + "  expr: SUM(o_totalprice)\n"
        + "  comment: Total order revenue\n"
        + "  synonyms:\n"
        + "  - revenue\n"
        + "  - total revenue\n"
        + "  - sales\n"
        + "- name: order_count\n"
        + "  expr: COUNT(*)\n"
        + "  comment: Number of orders\n";

    assertEquals(OssieConverter.parseYaml(expected), export(osi, null));
  }

  @Test
  public void measureDisplayNameSurvivesRoundTripViaStash() {
    // A dimension's display_name maps to the Ossie Field `label`, but the Ossie Metric schema has
    // no `label`, so a measure's display_name is preserved in the DATABRICKS custom_extensions
    // stash (like format/window) and restored on the way back.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.fact\n"
        + "dimensions:\n"
        + "- name: region\n"
        + "  expr: region\n"
        + "measures:\n"
        + "- name: total_revenue\n"
        + "  expr: SUM(amount)\n"
        + "  display_name: Total Revenue\n";

    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    // The only display_name in the input is on the measure; it rides in the metric's stash, since
    // there is no native Ossie field for it.
    assertTrue(ossie.contains("display_name"),
        "expected the measure display_name preserved in the stash, got: " + ossie);

    String mv2 = OssieConverter.convertOssieToMetricView(ossie, null).yaml;
    assertEquals(OssieConverter.parseYaml(mv), OssieConverter.parseYaml(mv2));
  }

  @Test
  public void unsupportedVersionIsRejected() {
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView("version: '9.9'\nsemantic_model: []\n", null));
    assertTrue(e.getMessage().contains("Unsupported Apache Ossie version"));
  }

  @Test
  public void multipleCandidateFactsWithoutSourceIsRejected() {
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - {name: orders, source: c.s.orders}\n"
        + "  - {name: returns, source: c.s.returns}\n"
        + "  - {name: customer, source: c.s.customer, primary_key: [c_custkey]}\n"
        + "  relationships:\n"
        + "  - {name: oc, from: orders, to: customer, from_columns: [o_custkey], to_columns: [c_custkey]}\n"
        + "  - {name: rc, from: returns, to: customer, from_columns: [re_custkey], to_columns: [c_custkey]}\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, null));
    assertTrue(e.getMessage().contains("multiple candidate fact datasets"));
  }

  @Test
  @SuppressWarnings("unchecked")
  public void oneToManyEmitsCardinality() {
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: orders\n"
        + "    source: c.s.orders\n"
        + "    primary_key: [o_orderkey]\n"
        + "    fields:\n"
        + "    - {name: o_orderstatus, expression: {dialects: [{dialect: DATABRICKS, expression: o_orderstatus}]}}\n"
        + "  - name: lineitem\n"
        + "    source: c.s.lineitem\n"
        + "  relationships:\n"
        + "  - {name: lio, from: lineitem, to: orders, from_columns: [l_orderkey], to_columns: [o_orderkey]}\n"
        + "  metrics:\n"
        + "  - {name: qty, expression: {dialects: [{dialect: DATABRICKS, expression: SUM(lineitem.l_quantity)}]}}\n";
    Map<String, Object> view = (Map<String, Object>) export(osi, "orders");
    List<Object> joins = (List<Object>) view.get("joins");
    Map<String, Object> join = (Map<String, Object>) joins.get(0);
    assertEquals("one_to_many", join.get("cardinality"));
  }

  @Test
  public void oneToManyNestedUnderManyToOneIsRejected() {
    // A Metric View requires one cardinality per top-level branch, so a one-to-many join nested
    // under a many-to-one parent is rejected by Databricks just as the reverse nesting is. Fact
    // `d`; `f -> d` points at d (many-to-one from d's perspective), and `g -> f` points away from
    // f (one-to-many), which would nest one_to_many inside the many-to-one branch.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "    fields:\n"
        + "    - {name: dcol, expression: {dialects: [{dialect: DATABRICKS, expression: dcol}]}}\n"
        + "  - name: f\n"
        + "    source: c.s.f\n"
        + "    primary_key: [fk]\n"
        + "  - name: g\n"
        + "    source: c.s.g\n"
        + "  relationships:\n"
        + "  - {name: df, from: d, to: f, from_columns: [fk], to_columns: [fk]}\n"
        + "  - {name: gf, from: g, to: f, from_columns: [fk], to_columns: [fk]}\n"
        + "  metrics:\n"
        + "  - {name: c, expression: {dialects: [{dialect: DATABRICKS, expression: COUNT(1)}]}}\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, "d"));
    assertTrue(e.getMessage().contains("share the same cardinality"),
        "expected a mixed-cardinality rejection, got: " + e.getMessage());
  }

  @Test
  public void directedCycleWithNoEquidistantEdgeIsRejected() {
    // a -> b -> c -> d -> e -> b with fact `a`: every edge spans adjacent BFS levels, so the
    // equidistance heuristic sees nothing, and `a` has zero incoming edges so pickFact finds a
    // root. Without a real acyclicity check this expanded into duplicate join paths (`d` under
    // both `c` and `e`), fabricating a tree from a cyclic model.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: a\n"
        + "    source: c.s.a\n"
        + "    fields:\n"
        + "    - {name: acol, expression: {dialects: [{dialect: DATABRICKS, expression: acol}]}}\n"
        + "  - name: b\n"
        + "    source: c.s.b\n"
        + "  - name: c\n"
        + "    source: c.s.c\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "  - name: e\n"
        + "    source: c.s.e\n"
        + "  relationships:\n"
        + "  - {name: ab, from: a, to: b, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: bc, from: b, to: c, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: cd, from: c, to: d, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: de, from: d, to: e, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: eb, from: e, to: b, from_columns: [k], to_columns: [k]}\n";
    OssieConverter.ConversionException ex = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, "a"));
    assertTrue(ex.getMessage().contains("directed cycle"),
        "expected a directed-cycle rejection, got: " + ex.getMessage());
  }

  @Test
  @SuppressWarnings("unchecked")
  public void diamondIsStillAcceptedByTheCycleCheck() {
    // A diamond (`a -> b -> d` plus `a -> c -> d`) is an UNDIRECTED cycle but directed-acyclic, and
    // is supported via the fan-out aliases. The cycle check must not reject it.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: a\n"
        + "    source: c.s.a\n"
        + "  - name: b\n"
        + "    source: c.s.b\n"
        + "  - name: c\n"
        + "    source: c.s.c\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "    fields:\n"
        + "    - {name: dcol, expression: {dialects: [{dialect: DATABRICKS, expression: dcol}]}}\n"
        + "  relationships:\n"
        + "  - {name: ab, from: a, to: b, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: ac, from: a, to: c, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: bd, from: b, to: d, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: cd, from: c, to: d, from_columns: [k], to_columns: [k]}\n";
    Map<String, Object> view = (Map<String, Object>) export(osi, "a");
    assertEquals("c.s.a", view.get("source"));
    // `d` is reached by two paths, so its column is emitted once per fan-out alias.
    List<Object> dims = (List<Object>) view.get("dimensions");
    assertEquals(2, dims.size(), "diamond should fan out to one dimension per path, got: " + dims);
  }

  @Test
  @SuppressWarnings("unchecked")
  public void measureNamingADiamondDatasetIsDroppedWithNotice() {
    // Same diamond as above (`d` reached via `a -> b -> d` and `a -> c -> d`), plus a measure that
    // names `d` by its bare dataset name. A measure addresses a dataset by name, so it cannot be
    // split per fan-out path the way the dimension on `d` is; it is dropped with a notice rather
    // than silently bound to one arbitrary branch (as the dimension complex-expression path does).
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: a\n"
        + "    source: c.s.a\n"
        + "  - name: b\n"
        + "    source: c.s.b\n"
        + "  - name: c\n"
        + "    source: c.s.c\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "    fields:\n"
        + "    - {name: dcol, expression: {dialects: [{dialect: DATABRICKS, expression: dcol}]}}\n"
        + "  relationships:\n"
        + "  - {name: ab, from: a, to: b, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: ac, from: a, to: c, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: bd, from: b, to: d, from_columns: [k], to_columns: [k]}\n"
        + "  - {name: cd, from: c, to: d, from_columns: [k], to_columns: [k]}\n"
        + "  metrics:\n"
        + "  - {name: dsum, expression: {dialects: [{dialect: DATABRICKS, expression: SUM(d.dcol)}]}}\n";
    OssieConverter.Result result = OssieConverter.convertOssieToMetricView(osi, "a");
    Map<String, Object> view = (Map<String, Object>) OssieConverter.parseYaml(result.yaml);
    // The diamond dimension on `d` still fans out to one dimension per path.
    assertEquals(2, ((List<Object>) view.get("dimensions")).size());
    // The measure that names `d` is dropped rather than bound to an arbitrary branch.
    assertFalse(view.containsKey("measures"),
        "a measure naming a diamond dataset must be dropped, got: " + view.get("measures"));
    String expected = "expression references dataset 'd', reached by more than one join path "
        + "(diamond); cannot be unambiguously qualified; dropped";
    assertTrue(result.notices.stream().anyMatch(n -> n.contains(expected)),
        result.notices.toString());
  }

  @Test
  @SuppressWarnings("unchecked")
  public void nestedJoinColumnInMeasureGetsFullAliasPath() {
    // orders -> customer -> nation: `nation` nests under `customer`, so its columns are addressed
    // as `customer.nation.col`. A bare `nation.` head would be read as struct access on a
    // parameter, so the measure must be re-qualified -- and identically to the dimension path,
    // which already emits `customer.nation.population` for the same column.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: orders\n"
        + "    source: c.s.orders\n"
        + "  - name: customer\n"
        + "    source: c.s.customer\n"
        + "    primary_key: [c_custkey]\n"
        + "  - name: nation\n"
        + "    source: c.s.nation\n"
        + "    primary_key: [n_nationkey]\n"
        + "    fields:\n"
        + "    - {name: population, expression: {dialects: [{dialect: DATABRICKS, expression: population}]}}\n"
        + "  relationships:\n"
        + "  - {name: oc, from: orders, to: customer, from_columns: [c_custkey], to_columns: [c_custkey]}\n"
        + "  - {name: cn, from: customer, to: nation, from_columns: [n_nationkey], to_columns: [n_nationkey]}\n"
        + "  metrics:\n"
        + "  - {name: pop, expression: {dialects: [{dialect: DATABRICKS, expression: SUM(nation.population)}]}}\n";
    Map<String, Object> view = (Map<String, Object>) export(osi, "orders");

    List<Object> dims = (List<Object>) view.get("dimensions");
    Map<String, Object> dim = (Map<String, Object>) dims.get(0);
    assertEquals("customer.nation.population", dim.get("expr"),
        "dimension path should qualify with the full alias path");

    List<Object> measures = (List<Object>) view.get("measures");
    Map<String, Object> measure = (Map<String, Object>) measures.get(0);
    assertEquals("SUM(customer.nation.population)", measure.get("expr"),
        "measure must use the same full alias path as the dimension, not a bare nested alias");
  }

  @Test
  public void modelWithNoFieldsOrMetricsIsRejected() {
    // A Metric View requires at least one dimension or measure, so emitting a version+source-only
    // view would just fail at CREATE. Fail at conversion time instead.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.d\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, null));
    assertTrue(e.getMessage().contains("no dimensions or measures"),
        "expected an empty-view rejection, got: " + e.getMessage());
  }

  @Test
  public void emptyAfterDropsNamesTheDroppedColumns() {
    // Here the input is non-empty but everything drops: the only metric has no DATABRICKS/ANSI_SQL
    // dialect. The error must name the dropped column so the cause is actionable, since after the
    // cascade the emptiness is a consequence of the drop rather than an empty input.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "  metrics:\n"
        + "  - {name: only_metric, expression: {dialects: [{dialect: SNOWFLAKE, expression: SUM(x)}]}}\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, null));
    assertTrue(e.getMessage().contains("no dimensions or measures"),
        "expected an empty-view rejection, got: " + e.getMessage());
    assertTrue(e.getMessage().contains("only_metric"),
        "the message must name the dropped column, got: " + e.getMessage());
  }

  @Test
  public void duplicateDimensionNameIsRejected() {
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: orders\n"
        + "    source: c.s.orders\n"
        + "    fields:\n"
        + "    - {name: id, expression: {dialects: [{dialect: DATABRICKS, expression: id}]}}\n"
        + "  - name: customer\n"
        + "    source: c.s.customer\n"
        + "    fields:\n"
        + "    - {name: id, expression: {dialects: [{dialect: DATABRICKS, expression: id}]}}\n"
        + "  relationships:\n"
        + "  - {name: r, from: orders, to: customer, from_columns: [cid], to_columns: [id]}\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, null));
    assertTrue(e.getMessage().contains("collides"));
  }

  @Test
  public void foreignVendorExtensionDroppedWithNotice() {
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  custom_extensions:\n"
        + "  - {vendor_name: SNOWFLAKE, data: '{}'}\n"
        + "  datasets:\n"
        + "  - name: orders\n"
        + "    source: c.s.orders\n"
        + "    fields:\n"
        + "    - {name: s, expression: {dialects: [{dialect: DATABRICKS, expression: s}]}}\n"
        + "  metrics:\n"
        + "  - {name: n, expression: {dialects: [{dialect: DATABRICKS, expression: COUNT(*)}]}}\n";
    OssieConverter.Result r = OssieConverter.convertOssieToMetricView(osi, null);
    assertTrue(r.notices.stream().anyMatch(m -> m.contains("foreign-vendor custom_extensions dropped")));
  }

  // -- import direction (Metric View -> Apache Ossie) -----------------------

  private static Object importMv(String mv) {
    return OssieConverter.parseYaml(
        OssieConverter.convertMetricViewToOssie(mv, null).yaml);
  }

  /** A Metric View YAML with {@code joinCount} sibling equi-joins on the fact source. */
  private static String metricViewWithJoins(int joinCount) {
    StringBuilder mv = new StringBuilder("version: '1.1'\nsource: c.s.fact\njoins:\n");
    for (int i = 1; i <= joinCount; i++) {
      mv.append("- name: j").append(i).append("\n")
          .append("  source: c.s.j").append(i).append("\n")
          .append("  on: source.k = j").append(i).append(".k\n");
    }
    return mv.toString();
  }

  @Test
  @SuppressWarnings("unchecked")
  public void importDecomposesJoinIntoRelationship() {
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey = customer.c_custkey\n"
        + "  rely: {at_most_one_match: true}\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus}\n"
        + "- {name: c_name, expr: customer.c_name}\n"
        + "measures:\n"
        + "- {name: revenue, expr: SUM(o_totalprice)}\n";
    Map<String, Object> out = (Map<String, Object>) importMv(mv);
    List<Object> models = (List<Object>) out.get("semantic_model");
    Map<String, Object> model = (Map<String, Object>) models.get(0);
    List<Object> rels = (List<Object>) model.get("relationships");
    Map<String, Object> rel = (Map<String, Object>) rels.get(0);
    assertEquals("orders", rel.get("from"));
    assertEquals("customer", rel.get("to"));
    assertEquals(List.of("o_custkey"), rel.get("from_columns"));
    assertEquals(List.of("c_custkey"), rel.get("to_columns"));
  }

  @Test
  public void exportRejectsMoreJoinsThanImportCanRebuild() {
    // MV -> Ossie has no join bound of its own, but Ossie -> MV rejects a model with more than
    // MAX_JOIN_NODES datasets. Without a matching bound here, a Metric View with too many joins
    // would export to a model that could never be imported again; reject it at export instead.
    int max = OssieConverterCommon.MAX_JOIN_NODES;
    String mv = metricViewWithJoins(max);
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertMetricViewToOssie(mv, null));
    assertTrue(e.getMessage().contains("at most " + max + " are supported"),
        "expected a join-count rejection, got: " + e.getMessage());
  }

  @Test
  @SuppressWarnings("unchecked")
  public void exportAtTheJoinLimitStillSucceeds() {
    // The fact plus (MAX_JOIN_NODES - 1) joins is exactly MAX_JOIN_NODES datasets, the boundary the
    // reverse conversion accepts, so export must still succeed here.
    int max = OssieConverterCommon.MAX_JOIN_NODES;
    Map<String, Object> out = (Map<String, Object>) importMv(metricViewWithJoins(max - 1));
    List<Object> models = (List<Object>) out.get("semantic_model");
    Map<String, Object> model = (Map<String, Object>) models.get(0);
    List<Object> datasets = (List<Object>) model.get("datasets");
    assertEquals(max, datasets.size(), "fact plus joins should be exactly the limit");
  }

  @Test
  @SuppressWarnings("unchecked")
  public void importPreservesNonEquiJoinInStash() {
    // A non-equi `on` has no schema-valid Ossie relationship (from/to columns are required), so the
    // converter warns and stashes the whole join under the model's DATABRICKS custom_extensions
    // rather than emitting an invalid stub relationship (issue #321).
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey >= customer.c_custkey\n";
    OssieConverter.Result result = OssieConverter.convertMetricViewToOssie(mv, null);
    assertTrue(result.notices.stream().anyMatch(n -> n.contains("non-equi or unsupported")),
        result.notices.toString());
    Map<String, Object> out = (Map<String, Object>) OssieConverter.parseYaml(result.yaml);
    Map<String, Object> model =
        (Map<String, Object>) ((List<Object>) out.get("semantic_model")).get(0);
    // No stub relationship is emitted; the join lives only in the model's custom_extensions.
    assertFalse(model.containsKey("relationships"), result.yaml);
    assertTrue(model.containsKey("custom_extensions"), result.yaml);
    assertTrue(result.yaml.contains("source.o_custkey >= customer.c_custkey"), result.yaml);
  }

  @Test
  public void nonEquiJoinSurvivesRoundTripViaStash() {
    // The preserved raw `on` is restored verbatim on the way back, so a metric view whose join
    // carries business logic round-trips instead of being rejected (issue #321).
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey >= customer.c_custkey\n"
        + "dimensions:\n"
        + "- name: cust\n"
        + "  expr: customer.c_name\n"
        + "measures:\n"
        + "- name: revenue\n"
        + "  expr: SUM(o_totalprice)\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    String mv2 = OssieConverter.convertOssieToMetricView(ossie, null).yaml;
    assertTrue(mv2.contains("source.o_custkey >= customer.c_custkey"), mv2);
  }

  @Test
  @SuppressWarnings("unchecked")
  public void nestedNonEquiJoinKeepsAliasesOnRoundTrip() {
    // A nested join's non-equi `on` references the parent join's alias. On import the alias is
    // assigned from the (unique) dataset name our exporter emits, so the verbatim-restored `on`
    // still names the aliases the importer actually assigned -- no stale-qualifier rewrite needed.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey = customer.c_custkey\n"
        + "  joins:\n"
        + "  - name: nation\n"
        + "    source: c.s.nation\n"
        + "    on: customer.c_nationkey <> nation.n_nationkey\n"
        + "measures:\n"
        + "- name: revenue\n"
        + "  expr: SUM(o_totalprice)\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    Map<String, Object> mv2 = (Map<String, Object>) OssieConverter.parseYaml(
        OssieConverter.convertOssieToMetricView(ossie, null).yaml);
    Map<String, Object> customer = (Map<String, Object>) ((List<Object>) mv2.get("joins")).get(0);
    assertEquals("customer", customer.get("name"));
    Map<String, Object> nation =
        (Map<String, Object>) ((List<Object>) customer.get("joins")).get(0);
    // The nested join keeps its alias, and the restored `on` references it and the parent alias
    // `customer` -- exactly the aliases the importer assigned, so the clause resolves.
    assertEquals("nation", nation.get("name"));
    assertEquals("customer.c_nationkey <> nation.n_nationkey", nation.get("on"));
  }

  @Test
  public void equiJoinOnPreservedVerbatimWhenNonCanonical() {
    // An equi-join is a valid Ossie relationship (from/to columns), but rebuilding its `on` from
    // those columns canonicalizes the fact qualifier to `source`. When the original used the source
    // table name instead, the raw `on` is stashed so the round trip reproduces it exactly.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: orders.o_custkey = customer.c_custkey\n"
        + "measures:\n"
        + "- name: revenue\n"
        + "  expr: SUM(o_totalprice)\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    String mv2 = OssieConverter.convertOssieToMetricView(ossie, null).yaml;
    assertTrue(mv2.contains("orders.o_custkey = customer.c_custkey"), mv2);
  }

  @Test
  public void complexJoinPreservesCardinalityOnRoundTrip() {
    // A complex (non-decomposable) join on the one_to_many branch stashes `on` and `cardinality`
    // as flat keys on the columns-less entry; both are restored verbatim on the way back.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: lineitem\n"
        + "  source: c.s.lineitem\n"
        + "  on: source.o_orderkey <> lineitem.l_orderkey\n"
        + "  cardinality: one_to_many\n"
        + "measures:\n"
        + "- name: cnt\n"
        + "  expr: COUNT(1)\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    String mv2 = OssieConverter.convertOssieToMetricView(ossie, null).yaml;
    assertTrue(mv2.contains("source.o_orderkey <> lineitem.l_orderkey"), mv2);
    assertTrue(mv2.contains("one_to_many"), mv2);
  }

  @Test
  @SuppressWarnings("unchecked")
  public void complexJoinNestedUnderComplexJoinRoundTrips() {
    // A complex join nested under another complex join: both edges live in the model stash, and the
    // reverse merge rebuilds the tree so each raw `on` and the nesting are restored.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey <> customer.c_custkey\n"
        + "  joins:\n"
        + "  - name: nation\n"
        + "    source: c.s.nation\n"
        + "    on: customer.c_nationkey <> nation.n_nationkey\n"
        + "measures:\n"
        + "- name: cnt\n"
        + "  expr: COUNT(1)\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    Map<String, Object> mv2 = (Map<String, Object>) OssieConverter.parseYaml(
        OssieConverter.convertOssieToMetricView(ossie, null).yaml);
    Map<String, Object> customer = (Map<String, Object>) ((List<Object>) mv2.get("joins")).get(0);
    assertEquals("source.o_custkey <> customer.c_custkey", customer.get("on"));
    Map<String, Object> nation =
        (Map<String, Object>) ((List<Object>) customer.get("joins")).get(0);
    assertEquals("nation", nation.get("name"));
    assertEquals("customer.c_nationkey <> nation.n_nationkey", nation.get("on"));
  }

  @Test
  public void equiJoinOnOverEqualColumnsStaysOnNotUsing() {
    // An equi-join `on` over equal column names rebuilds from columns as `using`, so the original
    // `on` is stashed and restored -- the round trip keeps `on`, it does not collapse to `using`.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.custkey = customer.custkey\n"
        + "measures:\n"
        + "- name: cnt\n"
        + "  expr: COUNT(1)\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    String mv2 = OssieConverter.convertOssieToMetricView(ossie, null).yaml;
    assertTrue(mv2.contains("source.custkey = customer.custkey"), mv2);
    assertFalse(mv2.contains("using"), mv2);
  }

  @Test
  public void importRejectsCrossJoin() {
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertMetricViewToOssie(mv, null));
    assertTrue(e.getMessage().contains("no join condition"));
  }

  @Test
  public void importRejectsUnsupportedVersion() {
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertMetricViewToOssie("version: '0.1'\nsource: c.s.t\n", null));
    assertTrue(e.getMessage().contains("Unsupported Metric View version"));
  }

  @Test
  public void mvToOssieToMvRoundTripsStash() {
    // A view with MV-only features (filter/rely/format/window) must survive
    // MV -> Ossie -> MV unchanged (the custom_extensions stash carries them).
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "filter: o_orderstatus = 'F'\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey = customer.c_custkey\n"
        + "  rely:\n"
        + "    at_most_one_match: true\n"
        + "dimensions:\n"
        + "- name: net\n"
        + "  expr: o_totalprice\n"
        + "  format:\n"
        + "    type: currency\n"
        + "    currency_code: USD\n"
        + "measures:\n"
        + "- name: running\n"
        + "  expr: SUM(o_totalprice)\n"
        + "  window:\n"
        + "  - order: net\n"
        + "    semiadditive: last\n"
        + "    range: cumulative\n";
    String ossie = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    String back = OssieConverter.convertOssieToMetricView(ossie, null).yaml;
    assertEquals(OssieConverter.parseYaml(mv), OssieConverter.parseYaml(back));
  }

  // -- fixture comparisons ---------------------------------------------------
  // The fixtures under src/test/resources/ossie_*.yaml pin the expected outputs
  // checked into apache/ossie. Asserting the Java output parses equal to them (structure
  // + stash blob STRINGS) is the real "one behavior, two implementations" guarantee --
  // it catches divergences like stash JSON spacing that a Java->Java round-trip misses.

  private static String loadFixture(String name) {
    try (InputStream in =
        OssieConverterSuite.class.getClassLoader().getResourceAsStream("ossie_" + name)) {
      if (in == null) {
        throw new IllegalStateException("fixture not found: ossie_" + name);
      }
      return new String(in.readAllBytes(), StandardCharsets.UTF_8);
    } catch (java.io.IOException e) {
      throw new RuntimeException(e);
    }
  }

  @Test
  public void fixtureAExportMatchesFixture() {
    String out = OssieConverter.convertOssieToMetricView(loadFixture("fixtureA_ossie.yaml"), null).yaml;
    assertEquals(OssieConverter.parseYaml(loadFixture("fixtureA_metric_view.yaml")),
        OssieConverter.parseYaml(out));
  }

  @Test
  public void fixtureBImportMatchesFixture() {
    // fixtureB exercises the custom_extensions stash (format/rely/filter) -- the parse
    // includes the blob strings, so this is what pins the stash blob's exact spacing.
    String out = OssieConverter.convertMetricViewToOssie(loadFixture("fixtureB_metric_view.yaml"), null).yaml;
    assertEquals(OssieConverter.parseYaml(loadFixture("fixtureB_ossie.yaml")),
        OssieConverter.parseYaml(out));
  }

  @Test
  public void tpcdsExportMatchesFixture() {
    String out = OssieConverter.convertOssieToMetricView(loadFixture("tpcds_ossie.yaml"), null).yaml;
    assertEquals(OssieConverter.parseYaml(loadFixture("tpcds_metric_view.yaml")),
        OssieConverter.parseYaml(out));
  }

  @Test
  @SuppressWarnings("unchecked")
  public void stashBlobUsesExpectedSpacing() {
    // The strongest byte-level check: the emitted stash blob string (the `data` value of a
    // custom_extensions entry) must use the stash format's separators
    // (", " / ": "). Pull the blob out of the parsed model rather than substring-matching
    // the outer YAML (where it appears escaped).
    Object out = OssieConverter.parseYaml(
        OssieConverter.convertMetricViewToOssie(loadFixture("fixtureB_metric_view.yaml"), null).yaml);
    Map<String, Object> model =
        (Map<String, Object>) ((List<Object>) ((Map<String, Object>) out).get("semantic_model")).get(0);
    List<Object> exts = (List<Object>) model.get("custom_extensions");
    String blob = (String) ((Map<String, Object>) exts.get(0)).get("data");
    assertTrue(blob.startsWith("{\"_v\": 1, "),
        "stash blob must use the spacing '{\"_v\": 1, ...}', got: " + blob);
  }

  // -- parity edge cases found in review round 3 ----------------------------

  @Test
  public void pickExpressionFallsThroughEmptyDatabricksToAnsi() {
    // An empty DATABRICKS dialect must fall through to ANSI_SQL,
    // not be selected as the (empty) expression.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: f\n"
        + "    source: c.s.f\n"
        + "    fields:\n"
        + "    - name: d\n"
        + "      expression:\n"
        + "        dialects:\n"
        + "        - {dialect: DATABRICKS, expression: ''}\n"
        + "        - {dialect: ANSI_SQL, expression: ansi_col}\n"
        + "  metrics:\n"
        + "  - {name: n, expression: {dialects: [{dialect: DATABRICKS, expression: COUNT(*)}]}}\n";
    Object out = export(osi, null);
    @SuppressWarnings("unchecked")
    List<Object> dims = (List<Object>) ((Map<String, Object>) out).get("dimensions");
    @SuppressWarnings("unchecked")
    Map<String, Object> dim = (Map<String, Object>) dims.get(0);
    assertEquals("ansi_col", dim.get("expr"));
  }

  @Test
  public void pickExpressionRejectsNonStringExpression() {
    // A non-string dialect expression (e.g. a YAML number) must raise, not be coerced.
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: f\n"
        + "    source: c.s.f\n"
        + "    fields:\n"
        + "    - name: d\n"
        + "      expression:\n"
        + "        dialects:\n"
        + "        - {dialect: DATABRICKS, expression: 123}\n"
        + "  metrics:\n"
        + "  - {name: n, expression: {dialects: [{dialect: DATABRICKS, expression: COUNT(*)}]}}\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView(osi, null));
    assertTrue(e.getMessage().contains("expression must be a string"));
  }

  @Test
  public void bareOnOffValuesStayStringsNotBooleans() {
    // YAML 1.1 would read a bare `on`/`off`/`yes`/`no` value as a boolean, silently losing
    // a join condition or turning a synonym into `true`. Confirm the converter's parser
    // keeps them as strings (the reader uses YAML 1.2 boolean semantics).
    Object parsed = OssieConverter.parseYaml("a: on\nb: off\nc: yes\nd: no\n");
    @SuppressWarnings("unchecked")
    Map<String, Object> m = (Map<String, Object>) parsed;
    assertEquals("on", m.get("a"));
    assertEquals("off", m.get("b"));
    assertEquals("yes", m.get("c"));
    assertEquals("no", m.get("d"));
  }

  @Test
  @SuppressWarnings("unchecked")
  public void importDropsEmptyOptionalFields() {
    // Optional fields are mapped only when non-empty, so an empty
    // comment / empty synonyms list are omitted, not emitted as `description: ""` or an
    // empty ai_context. The Java port must match (empty string / empty list are falsy).
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "comment: ''\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus, comment: '', display_name: '', synonyms: []}\n"
        + "measures:\n"
        + "- {name: revenue, expr: SUM(o_totalprice), comment: '', synonyms: []}\n";
    Map<String, Object> out = (Map<String, Object>) importMv(mv);
    List<Object> models = (List<Object>) out.get("semantic_model");
    Map<String, Object> model = (Map<String, Object>) models.get(0);
    assertFalse(model.containsKey("description"), "empty comment must not become a description");
    Map<String, Object> ds = (Map<String, Object>) ((List<Object>) model.get("datasets")).get(0);
    Map<String, Object> field = (Map<String, Object>) ((List<Object>) ds.get("fields")).get(0);
    assertFalse(field.containsKey("description"), "empty comment must not map to description");
    assertFalse(field.containsKey("label"), "empty display_name must not map to label");
    assertFalse(field.containsKey("ai_context"), "empty synonyms must not map to ai_context");
    Map<String, Object> metric = (Map<String, Object>) ((List<Object>) model.get("metrics")).get(0);
    assertFalse(metric.containsKey("description"), "empty comment must not map to description");
    assertFalse(metric.containsKey("ai_context"), "empty synonyms must not map to ai_context");
  }

  @Test
  public void stashEscapesNonAsciiAsLowercaseHex() {
    // The stash blob is pure ASCII: non-ASCII is escaped to \\uXXXX. The
    // stash blob must be byte-identical, so a non-ASCII stashed value (here a `filter`
    // literal) escapes rather than emitting raw UTF-8.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "filter: \"region = 'café'\"\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus}\n";
    String ossieYaml = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    // Extract the stash blob and assert the exact expected bytes:
    // the non-ASCII char is escaped as lowercase \\u00e9 (a single backslash + 5 chars),
    // not emitted raw. (Comparing the parsed `data` string sidesteps YAML's own quoting.)
    @SuppressWarnings("unchecked")
    Map<String, Object> out = (Map<String, Object>) OssieConverter.parseYaml(ossieYaml);
    @SuppressWarnings("unchecked")
    List<Object> models = (List<Object>) out.get("semantic_model");
    Map<String, Object> model = (Map<String, Object>) models.get(0);
    @SuppressWarnings("unchecked")
    List<Object> exts = (List<Object>) model.get("custom_extensions");
    @SuppressWarnings("unchecked")
    String blob = (String) ((Map<String, Object>) exts.get(0)).get("data");
    assertEquals("{\"_v\": 1, \"filter\": \"region = 'caf\\u00e9'\"}", blob,
        "stash blob must use a lowercase \\u escape");
    // And it still round-trips back to the original view.
    String back = OssieConverter.convertOssieToMetricView(ossieYaml, null).yaml;
    assertEquals(OssieConverter.parseYaml(mv), OssieConverter.parseYaml(back));
  }

  @Test
  public void stashPreservesAValueContainingALiteralUnicodeEscape() {
    // A stashed value may itself contain the text of a unicode escape. Serialized, that is a
    // DOUBLED backslash, so the hex-lowercasing pass must not treat it as a real escape --
    // doing so silently lowercases the value's own characters.
    // Single-quoted YAML: a backslash is an ordinary character there, so `filter` really holds
    // the six characters \ u A B C D rather than the character U+ABCD.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "filter: 'tag = \\uABCD'\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus}\n";
    @SuppressWarnings("unchecked")
    Map<String, Object> parsedIn = (Map<String, Object>) OssieConverter.parseYaml(mv);
    String original = (String) parsedIn.get("filter");
    // Guard the fixture itself: the value must contain a real backslash for this to be a test.
    assertTrue(original.indexOf('\\') >= 0,
        "test setup: filter must hold a literal backslash, got: " + original);

    String ossieYaml = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    String back = OssieConverter.convertOssieToMetricView(ossieYaml, null).yaml;
    @SuppressWarnings("unchecked")
    Map<String, Object> restored = (Map<String, Object>) OssieConverter.parseYaml(back);
    assertEquals(original, restored.get("filter"),
        "a literal unicode-escape sequence in a stashed value must survive unchanged");
  }

  @Test
  public void joinOnTakesPrecedenceOverUsing() {
    // Metric View validation requires only that one of `on`/`using` is present, so both may be
    // set. Databricks resolves the criteria from `on` when it is present, so the converter must
    // decompose `on` and ignore `using` -- otherwise the relationship joins on other columns.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "joins:\n"
        + "- name: cust\n"
        + "  source: c.s.customer\n"
        + "  on: source.o_custkey = cust.c_custkey\n"
        + "  using: [nation_key]\n"
        + "dimensions:\n"
        + "- {name: c_name, expr: cust.c_name}\n"
        + "measures:\n"
        + "- {name: cnt, expr: COUNT(1)}\n";
    @SuppressWarnings("unchecked")
    Map<String, Object> out = (Map<String, Object>) OssieConverter.parseYaml(
        OssieConverter.convertMetricViewToOssie(mv, null).yaml);
    @SuppressWarnings("unchecked")
    List<Object> models = (List<Object>) out.get("semantic_model");
    @SuppressWarnings("unchecked")
    Map<String, Object> model = (Map<String, Object>) models.get(0);
    @SuppressWarnings("unchecked")
    List<Object> rels = (List<Object>) model.get("relationships");
    @SuppressWarnings("unchecked")
    Map<String, Object> rel = (Map<String, Object>) rels.get(0);
    assertEquals(List.of("o_custkey"), rel.get("from_columns"),
        "`on` must win over `using`: expected the o_custkey/c_custkey pair");
    assertEquals(List.of("c_custkey"), rel.get("to_columns"),
        "`on` must win over `using`: expected the o_custkey/c_custkey pair");
  }

  @Test
  public void measureRewriteLeavesStringLiteralsAlone() {
    // The fact qualifier is added/stripped by rewriting the measure expression. That rewrite must
    // skip string literals: rewriting inside one changes the predicate and therefore the value.
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus}\n"
        + "measures:\n"
        + "- name: tagged\n"
        + "  expr: \"SUM(IF(source.region = 'source.us', 1, 0))\"\n";
    String ossieYaml = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    assertTrue(ossieYaml.contains("'source.us'"),
        "a literal mentioning the qualifier must not be rewritten, got:\n" + ossieYaml);
    // The literal also survives the trip back. Note the *code* qualifier is normalized on the
    // way through (`source.region` -> bare `region`, the Metric View idiom for fact columns);
    // only the literal is required to come back byte-identical.
    String back = OssieConverter.convertOssieToMetricView(ossieYaml, null).yaml;
    assertTrue(back.contains("'source.us'"),
        "the literal must survive the round trip, got:\n" + back);
  }

  /** A fact with `customer` joined to it and `region` nested under `customer`, plus one metric. */
  private static String nestedJoinModel(String metricExpr) {
    return "version: \"0.2.0.dev0\"\n"
        + "semantic_model:\n"
        + "  - name: sales\n"
        + "    datasets:\n"
        + "      - name: lineitem\n"
        + "        source: cat.sch.lineitem\n"
        + "        fields:\n"
        + "          - name: l_key\n"
        + "            expression:\n"
        + "              dialects: [{dialect: DATABRICKS, expression: l_key}]\n"
        + "      - name: customer\n"
        + "        source: cat.sch.customer\n"
        + "        primary_key: [c_key]\n"
        + "      - name: region\n"
        + "        source: cat.sch.region\n"
        + "        primary_key: [r_key]\n"
        + "    relationships:\n"
        + "      - name: l_to_c\n"
        + "        from: lineitem\n"
        + "        to: customer\n"
        + "        from_columns: [c_key]\n"
        + "        to_columns: [c_key]\n"
        + "      - name: c_to_r\n"
        + "        from: customer\n"
        + "        to: region\n"
        + "        from_columns: [r_key]\n"
        + "        to_columns: [r_key]\n"
        + "    metrics:\n"
        + "      - name: pop\n"
        + "        expression:\n"
        + "          dialects: [{dialect: DATABRICKS, expression: \"" + metricExpr + "\"}]\n";
  }

  @SuppressWarnings("unchecked")
  private static String firstMeasureExpr(Object view) {
    Map<String, Object> mv = (Map<String, Object>) view;
    List<Object> measures = (List<Object>) mv.get("measures");
    assertFalse(measures == null || measures.isEmpty(), "expected one measure, got: " + mv);
    return (String) ((Map<String, Object>) measures.get(0)).get("expr");
  }

  @Test
  public void exportQualifiesANestedMeasureWithTheFullJoinPath() {
    // A Metric View addresses a nested join column by its full path, so a metric naming the
    // dataset must come out as `customer.region.population`.
    assertEquals("SUM(customer.region.population)",
        firstMeasureExpr(export(nestedJoinModel("SUM(region.population)"), null)));
  }

  @Test
  public void exportDoesNotQualifyAMeasurePathTwice() {
    // The rewrite used to run one dataset name at a time, so it matched the `region.` inside the
    // path it had just written and produced `SUM(customer.customer.region.population)` -- silently,
    // with no notice. An expression that already carries the full path (what an import leaves
    // behind) must come out unchanged.
    assertEquals("SUM(customer.region.population)",
        firstMeasureExpr(export(nestedJoinModel("SUM(customer.region.population)"), null)));
  }

  @Test
  public void nestedQualifiedMeasureRoundTripsBothWays() {
    String mv =
        "version: '1.1'\n"
        + "source: cat.sch.lineitem\n"
        + "joins:\n"
        + "- name: customer\n"
        + "  source: cat.sch.customer\n"
        + "  using: [c_key]\n"
        + "  joins:\n"
        + "  - name: region\n"
        + "    source: cat.sch.region\n"
        + "    using: [r_key]\n"
        + "dimensions:\n"
        + "- {name: l_key, expr: l_key}\n"
        + "measures:\n"
        + "- {name: pop, expr: SUM(customer.region.population)}\n";
    String ossieYaml = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    // The import de-aliases the path down to the dataset that owns the column...
    assertTrue(ossieYaml.contains("SUM(region.population)"),
        "import should de-alias the join path to the dataset name, got:\n" + ossieYaml);
    // ...and the export puts it back, so the measure survives MV -> Ossie -> MV.
    assertEquals("SUM(customer.region.population)",
        firstMeasureExpr(OssieConverter.parseYaml(
            OssieConverter.convertOssieToMetricView(ossieYaml, null).yaml)));
  }

  @Test
  public void cascadeDropIgnoresADroppedNameInsideAStringLiteral() {
    // A field dropped for lacking a usable dialect must not take an unrelated measure with it just
    // because its name appears in a string literal: `'us'` is not a reference to a column `us`.
    String osi =
        "version: \"0.2.0.dev0\"\n"
        + "semantic_model:\n"
        + "  - name: sales\n"
        + "    datasets:\n"
        + "      - name: orders\n"
        + "        source: cat.sch.orders\n"
        + "        fields:\n"
        + "          - name: amount\n"
        + "            expression:\n"
        + "              dialects: [{dialect: DATABRICKS, expression: amount}]\n"
        + "          - name: us\n"
        + "            expression:\n"
        + "              dialects: [{dialect: SNOWFLAKE, expression: us_col}]\n"
        + "    metrics:\n"
        + "      - name: amt_us\n"
        + "        expression:\n"
        + "          dialects:\n"
        + "            - dialect: DATABRICKS\n"
        + "              expression: \"SUM(IF(region = 'us', amount, 0))\"\n";
    OssieConverter.Result result = OssieConverter.convertOssieToMetricView(osi, null);
    assertEquals("SUM(IF(region = 'us', amount, 0))",
        firstMeasureExpr(OssieConverter.parseYaml(result.yaml)));
    for (String notice : result.notices) {
      assertFalse(notice.contains("amt_us") && notice.contains("references dropped"),
          "the measure only mentions 'us' in a literal, got notice: " + notice);
    }
  }

  @Test
  public void stashPreservesABackslashBeforeAUnicodeEscape() {
    // The stash lowercases the hex of Jackson's \\uXXXX escapes. That rewrite must re-emit any
    // escaped-backslash run in front of the escape verbatim; it used to halve it, so a value
    // holding a literal backslash came back as the six characters "u00e9".
    String value = "tag = \\" + "é";
    String mv =
        "version: '1.1'\n"
        + "source: c.s.orders\n"
        + "filter: '" + value + "'\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus}\n";
    String ossieYaml = OssieConverter.convertMetricViewToOssie(mv, null).yaml;
    @SuppressWarnings("unchecked")
    Map<String, Object> back = (Map<String, Object>) OssieConverter.parseYaml(
        OssieConverter.convertOssieToMetricView(ossieYaml, null).yaml);
    assertEquals(value, back.get("filter"),
        "the stashed filter must survive MV -> Ossie -> MV byte-for-byte");
  }

  @Test
  public void exportWarnsWhenAColumnAiContextObjectIsDropped() {
    // `ai_context` is `string | object`; only the object's `synonyms` has a Metric View slot, so
    // the other members are dropped -- and the README promises a notice when that happens. The
    // same goes for foreign-vendor extensions on a metric.
    String osi =
        "version: \"0.2.0.dev0\"\n"
        + "semantic_model:\n"
        + "  - name: sales\n"
        + "    datasets:\n"
        + "      - name: orders\n"
        + "        source: cat.sch.orders\n"
        + "        fields:\n"
        + "          - name: amount\n"
        + "            expression:\n"
        + "              dialects: [{dialect: DATABRICKS, expression: amount}]\n"
        + "            ai_context:\n"
        + "              instructions: do not use this column\n"
        + "              synonyms: [amt]\n"
        + "    metrics:\n"
        + "      - name: total\n"
        + "        expression:\n"
        + "          dialects: [{dialect: DATABRICKS, expression: SUM(amount)}]\n"
        + "        ai_context:\n"
        + "          examples: [how much did we sell]\n"
        + "        custom_extensions:\n"
        + "          - vendor_name: SNOWFLAKE\n"
        + "            data: \"{}\"\n";
    List<String> notices = OssieConverter.convertOssieToMetricView(osi, null).notices;
    assertTrue(notices.contains(
        "[field 'amount'] ai_context [instructions] dropped "
            + "(only 'synonyms' has a Metric View counterpart)"),
        "expected a notice for the field's dropped ai_context members, got: " + notices);
    assertTrue(notices.contains(
        "[metric 'total'] ai_context [examples] dropped "
            + "(only 'synonyms' has a Metric View counterpart)"),
        "expected a notice for the metric's dropped ai_context members, got: " + notices);
    assertTrue(notices.contains("[metric 'total'] foreign-vendor custom_extensions dropped"),
        "expected a notice for the metric's foreign-vendor extensions, got: " + notices);
  }

  @Test
  public void importValidatesAJoinSourceTheWayTheExportDoes() {
    // A 2-part join source used to import cleanly and then fail on the way back out, so a view
    // that imported could not be exported. Reject it at the same point either direction would.
    String mv =
        "version: '1.1'\n"
        + "source: cat.sch.orders\n"
        + "joins:\n"
        + "- name: d\n"
        + "  source: sch.d\n"
        + "  using: [k]\n"
        + "dimensions:\n"
        + "- {name: o_status, expr: o_orderstatus}\n";
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertMetricViewToOssie(mv, null));
    assertTrue(e.getMessage().contains("3-part"),
        "expected the same source-shape error the export raises, got: " + e.getMessage());
  }

  // -- hardening: strict schema/stash parsing and structured failures -------

  @Test
  public void bareNullOptionalCollectionsConvertLikeAbsent() {
    // A present-but-null optional collection (the bare `relationships:` YAML idiom) must convert
    // like an absent key, as the base reader (asList) did. Only a scalar/map there is rejected.
    String yaml = "version: '" + OssieConverter.OSSIE_VERSION + "'\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.t\n"
        + "    fields:\n"
        + "    - name: region\n"
        + "      expression: {dialects: [{dialect: DATABRICKS, expression: region}]}\n"
        + "  relationships:\n"
        + "  metrics:\n"
        + "  - name: cnt\n"
        + "    expression: {dialects: [{dialect: DATABRICKS, expression: 'count(*)'}]}\n";
    String mv = OssieConverter.convertOssieToMetricView(yaml, null).yaml;
    assertTrue(mv.contains("cnt"), "expected the model to convert, got: " + mv);
  }

  @Test
  public void invalidOssieInputHasStructuredFailureDetails() {
    OssieConverter.ConversionException e = assertThrows(OssieConverter.ConversionException.class,
        () -> OssieConverter.convertOssieToMetricView("just a scalar", null));
    assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, e.getKind());
    assertEquals("it is not a mapping at the root", e.getReason());
  }

  @Test
  public void malformedSchemaCollectionsAreRejected() {
    assertInvalidOssieInput(
        "version: '" + OssieConverter.OSSIE_VERSION + "'\nsemantic_model: nope\n",
        "Apache Ossie YAML: 'semantic_model' must be a list");
    assertInvalidOssieInput(
        "version: '" + OssieConverter.OSSIE_VERSION + "'\nsemantic_model:\n- nope\n",
        "Apache Ossie YAML: 'semantic_model[0]' must be a mapping");
    assertInvalidOssieInput(
        ossieModelWithBody("  datasets: nope\n"),
        "Model 'm': 'datasets' must be a list");
    assertInvalidOssieInput(
        ossieModelWithBody("  datasets:\n  - nope\n"),
        "Model 'm': 'datasets[0]' must be a mapping");

    String dataset =
        "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.t\n";
    assertInvalidOssieInput(
        ossieModelWithBody(dataset + "  relationships: nope\n"),
        "Model 'm': 'relationships' must be a list");
    assertInvalidOssieInput(
        ossieModelWithBody(dataset + "  relationships:\n  - nope\n"),
        "Model 'm': 'relationships[0]' must be a mapping");
    assertInvalidOssieInput(
        ossieModelWithBody(dataset + "    fields: nope\n"),
        "Dataset 'd': 'fields' must be a list");
    assertInvalidOssieInput(
        ossieModelWithBody(dataset + "    fields:\n    - nope\n"),
        "Dataset 'd': 'fields[0]' must be a mapping");
    assertInvalidOssieInput(
        ossieModelWithBody(dataset + "  metrics: nope\n"),
        "Model 'm': 'metrics' must be a list");
    assertInvalidOssieInput(
        ossieModelWithBody(dataset + "  metrics:\n  - nope\n"),
        "Model 'm': 'metrics[0]' must be a mapping");
  }

  @Test
  public void malformedExpressionCollectionsAreRejected() {
    String datasetPrefix =
        "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.t\n"
        + "    fields:\n"
        + "    - name: id\n";
    assertInvalidOssieInput(
        ossieModelWithBody(datasetPrefix + "      expression: nope\n"),
        "field 'id': 'expression' must be a mapping");
    assertInvalidOssieInput(
        ossieModelWithBody(datasetPrefix + "      expression: {dialects: nope}\n"),
        "field 'id': 'expression.dialects' must be a list");
    assertInvalidOssieInput(
        ossieModelWithBody(datasetPrefix + "      expression: {dialects: [nope]}\n"),
        "field 'id': 'expression.dialects[0]' must be a mapping");

    String validField = datasetPrefix
        + "      expression:\n"
        + "        dialects:\n"
        + "        - {dialect: DATABRICKS, expression: id}\n";
    assertInvalidOssieInput(
        ossieModelWithBody(validField
            + "  metrics:\n"
            + "  - name: count_id\n"
            + "    expression: {dialects: nope}\n"),
        "metric 'count_id': 'expression.dialects' must be a list");
  }

  @Test
  public void malformedCustomExtensionsAreRejected() {
    String validModelBody =
        "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.t\n"
        + "    fields:\n"
        + "    - name: id\n"
        + "      expression:\n"
        + "        dialects:\n"
        + "        - {dialect: DATABRICKS, expression: id}\n";
    assertInvalidOssieInput(
        ossieModelWithBody(
            "  custom_extensions: {vendor_name: DATABRICKS, data: '{}'}\n" + validModelBody),
        "'custom_extensions' must be a list");
    assertInvalidOssieInput(
        ossieModelWithBody("  custom_extensions: [nope]\n" + validModelBody),
        "'custom_extensions[0]' must be a mapping");
    assertInvalidOssieInput(
        ossieModelWithBody(
            "  custom_extensions:\n"
            + "  - {vendor_name: DATABRICKS, data: 123}\n"
            + validModelBody),
        "DATABRICKS custom_extensions data must be a string");
    assertInvalidOssieInput(
        ossieModelWithBody(
            "  custom_extensions:\n"
            + "  - {vendor_name: DATABRICKS, data: '[]'}\n"
            + validModelBody),
        "DATABRICKS custom_extensions data must be a JSON object");

    for (String data : List.of(
        "filter: x = 1",
        "{\"filter\": \"x\", \"filter\": \"y\"}",
        "{\"filter\": \"x\"} true")) {
      String yaml = ossieModelWithBody(
          "  custom_extensions:\n"
          + "  - vendor_name: DATABRICKS\n"
          + "    data: '" + data + "'\n"
          + validModelBody);
      OssieConverter.ConversionException e =
          assertThrows(OssieConverter.ConversionException.class,
              () -> OssieConverter.convertOssieToMetricView(yaml, null));
      assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, e.getKind());
      assertTrue(e.getReason().startsWith(
          "DATABRICKS custom_extensions data is not valid JSON:"), e.getReason());
    }
  }

  @Test
  @SuppressWarnings("unchecked")
  public void duplicateDatabricksStashKeepsFirstAndWarns() {
    // Two DATABRICKS custom_extensions entries on one object is malformed, but rather than fail we
    // keep the first, ignore the rest, and warn so the dropped entry is not lost silently.
    String osi = ossieModelWithBody(
        "  custom_extensions:\n"
        + "  - {vendor_name: DATABRICKS, data: '{\"filter\": \"a = 1\"}'}\n"
        + "  - {vendor_name: DATABRICKS, data: '{\"filter\": \"b = 2\"}'}\n"
        + "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.t\n"
        + "    fields:\n"
        + "    - {name: id, expression: {dialects: [{dialect: DATABRICKS, expression: id}]}}\n");
    OssieConverter.Result result = OssieConverter.convertOssieToMetricView(osi, null);
    Map<String, Object> view = (Map<String, Object>) OssieConverter.parseYaml(result.yaml);
    assertEquals("a = 1", view.get("filter"), "the first DATABRICKS entry must win");
    assertTrue(
        result.notices.stream().anyMatch(n ->
            n.contains("more than one DATABRICKS custom_extensions entry")),
        result.notices.toString());
  }

  @Test
  public void oversizedDatasetGraphIsRejectedBeforeRecursiveTraversal() {
    StringBuilder body = new StringBuilder("  datasets:\n");
    for (int i = 0; i <= OssieConverterCommon.MAX_JOIN_NODES; i++) {
      body.append("  - {name: d").append(i).append(", source: c.s.t").append(i).append("}\n");
    }
    body.append("  relationships:\n");
    for (int i = 0; i < OssieConverterCommon.MAX_JOIN_NODES; i++) {
      body.append("  - {name: r").append(i)
          .append(", from: d").append(i)
          .append(", to: d").append(i + 1)
          .append(", from_columns: [id], to_columns: [id]}\n");
    }

    OssieConverter.ConversionException e =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.convertOssieToMetricView(
                ossieModelWithBody(body.toString()), null));

    assertTrue(e.getMessage().contains("at most 200 are supported"), e.getMessage());
  }

  @Test
  public void onlyTheFirstSemanticModelIsValidatedAndConverted() {
    String yaml = ossieModelWithBody(
        "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.t\n"
        + "    fields:\n"
        + "    - name: id\n"
        + "      expression:\n"
        + "        dialects:\n"
        + "        - {dialect: DATABRICKS, expression: id}\n")
        + "- this-ignored-model-is-not-a-mapping\n";

    OssieConverter.Result result = OssieConverter.convertOssieToMetricView(yaml, null);

    assertTrue(result.yaml.contains("name: \"id\""), result.yaml);
    assertTrue(result.notices.stream().anyMatch(
        notice -> notice.contains("multiple semantic models")), result.notices.toString());
  }

  @Test
  public void duplicateYamlKeysAreRejectedAtEveryDepth() {
    String topLevel =
        "version: '" + OssieConverter.OSSIE_VERSION + "'\n"
        + "version: '" + OssieConverter.OSSIE_VERSION + "'\n"
        + "semantic_model: []\n";
    String nested = ossieModelWithBody(
        "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.first\n"
        + "    source: c.s.second\n");
    for (String yaml : List.of(topLevel, nested)) {
      OssieConverter.ConversionException e =
          assertThrows(OssieConverter.ConversionException.class,
              () -> OssieConverter.convertOssieToMetricView(yaml, null));
      assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, e.getKind());
      assertTrue(e.getReason().contains("duplicate key"), e.getReason());
    }
  }

  @Test
  @SuppressWarnings("unchecked")
  public void reverseOrderedDropChainIsPropagatedWithoutRepeatedFullScans() {
    // Every field depends on the preceding field, but reverse source order means the old repeated
    // pass implementation could discover only one new drop per pass.
    int chainLength = 400;
    StringBuilder body = new StringBuilder(
        "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "    fields:\n");
    for (int index = chainLength; index >= 1; index--) {
      body.append("    - name: d").append(index).append("\n")
          .append("      expression:\n")
          .append("        dialects:\n")
          .append("        - {dialect: DATABRICKS, expression: d")
          .append(index - 1).append("}\n");
    }
    body.append("    - name: d0\n")
        .append("      expression:\n")
        .append("        dialects:\n")
        .append("        - {dialect: SNOWFLAKE, expression: d0}\n")
        .append("  metrics:\n")
        .append("  - name: row_count\n")
        .append("    expression:\n")
        .append("      dialects:\n")
        .append("      - {dialect: DATABRICKS, expression: count(*)}\n");

    OssieConverter.Result result =
        OssieConverter.convertOssieToMetricView(ossieModelWithBody(body.toString()), null);
    Map<String, Object> view = (Map<String, Object>) OssieConverter.parseYaml(result.yaml);

    assertFalse(view.containsKey("dimensions"), result.yaml);
    assertEquals(1, ((List<Object>) view.get("measures")).size(), result.yaml);
    assertEquals(chainLength + 1, result.notices.size(), result.notices.toString());
    List<String> cascadeNotices = result.notices.stream()
        .filter(notice -> notice.contains("downstream of a dropped field/metric"))
        .toList();
    assertEquals(chainLength, cascadeNotices.size(), cascadeNotices.toString());
    for (int index = 1; index <= chainLength; index++) {
      assertEquals(
          cascadeNotice("dimension", "d" + index, "d" + (index - 1)),
          cascadeNotices.get(index - 1));
    }
  }

  @Test
  public void cascadeDropPreservesDimensionThenMeasurePhaseOrder() {
    String osi =
        "version: 0.2.0.dev0\n"
        + "semantic_model:\n"
        + "- name: m\n"
        + "  datasets:\n"
        + "  - name: d\n"
        + "    source: c.s.d\n"
        + "    fields:\n"
        + "    - {name: d1, expression: {dialects: "
        + "[{dialect: DATABRICKS, expression: bad_dim}]}}\n"
        + "    - {name: d0, expression: {dialects: "
        + "[{dialect: DATABRICKS, expression: 'measure(m1)'}]}}\n"
        + "    - {name: bad_dim, expression: {dialects: "
        + "[{dialect: SNOWFLAKE, expression: bad_dim}]}}\n"
        + "  metrics:\n"
        + "  - {name: m1, expression: {dialects: "
        + "[{dialect: DATABRICKS, expression: 'measure(bad_measure)'}]}}\n"
        + "  - {name: m0, expression: {dialects: "
        + "[{dialect: DATABRICKS, expression: d0}]}}\n"
        + "  - {name: bad_measure, expression: {dialects: "
        + "[{dialect: SNOWFLAKE, expression: bad_measure}]}}\n"
        + "  - {name: keep, expression: {dialects: "
        + "[{dialect: DATABRICKS, expression: 'count(*)'}]}}\n";

    OssieConverter.Result result = OssieConverter.convertOssieToMetricView(osi, null);
    List<String> cascadeNotices = result.notices.stream()
        .filter(notice -> notice.contains("downstream of a dropped field/metric"))
        .toList();

    assertEquals(List.of(
        cascadeNotice("dimension", "d1", "bad_dim"),
        cascadeNotice("measure", "m1", "bad_measure"),
        cascadeNotice("dimension", "d0", "m1"),
        cascadeNotice("measure", "m0", "d0")), cascadeNotices);
  }

  @Test
  public void invalidMetricViewInputHasStructuredFailureDetails() {
    OssieConverter.ConversionException scalarError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.convertMetricViewToOssie("just a scalar", null));
    assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, scalarError.getKind());
    assertEquals("it is not a mapping at the root", scalarError.getReason());

    OssieConverter.ConversionException parseError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.convertMetricViewToOssie("version: [1", null));
    assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, parseError.getKind());
    assertTrue(parseError.getReason().startsWith("failed to parse YAML:"));

    OssieConverter.ConversionException emptyError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.convertMetricViewToOssie("   ", null));
    assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, emptyError.getKind());
    assertEquals("the input is empty", emptyError.getReason());

    OssieConverter.ConversionException missingVersionError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.convertMetricViewToOssie("source: c.s.t", null));
    assertEquals(
        OssieConverter.ConversionException.Kind.INVALID_INPUT, missingVersionError.getKind());
    assertEquals("it is missing the required 'version' field", missingVersionError.getReason());
  }

  @Test
  public void reverseDirectionSerializationFailuresAreInternalErrors() {
    Map<String, Object> invalid = valueThatFailsSerialization();

    OssieConverter.ConversionException yamlError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> MetricViewToOssie.serializeOssie(invalid, new OssieConverter.Notices()));
    assertEquals(OssieConverter.ConversionException.Kind.INTERNAL_ERROR, yamlError.getKind());

    OssieConverter.ConversionException stashError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverterCommon.writeStash(new HashMap<>(), invalid));
    assertEquals(OssieConverter.ConversionException.Kind.INTERNAL_ERROR, stashError.getKind());
  }

  @Test
  public void publicYamlHelpersHaveStructuredFailureKinds() {
    OssieConverter.ConversionException parseError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.parseYaml("version: [1"));
    assertEquals(OssieConverter.ConversionException.Kind.INVALID_INPUT, parseError.getKind());
    assertEquals(parseError.getMessage(), parseError.getReason());
    assertTrue(parseError.getCause() != null);

    OssieConverter.ConversionException dumpError =
        assertThrows(OssieConverter.ConversionException.class,
            () -> OssieConverter.dumpYaml(valueThatFailsSerialization()));
    assertEquals(OssieConverter.ConversionException.Kind.INTERNAL_ERROR, dumpError.getKind());
    assertEquals(dumpError.getMessage(), dumpError.getReason());
    assertTrue(dumpError.getCause() != null);
  }
}
