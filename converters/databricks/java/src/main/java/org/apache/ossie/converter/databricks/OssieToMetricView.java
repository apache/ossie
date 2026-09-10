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
import static org.apache.ossie.converter.databricks.OssieConverterCommon.MAPPER;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.MAX_JOIN_NODES;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.MV_VERSION;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.OSSIE_VERSION;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.STASH_SOURCE_KEY;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.SYNONYM_LIMIT;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.asList;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.asMap;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.findOutsideLiterals;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.foreignVendorExtensions;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.get;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.isSimpleIdentifier;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.loadYaml;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.mergeDescription;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.pickExpression;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.qualifierChainPattern;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.readStash;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.replaceOutsideLiterals;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.rewriteQualifiers;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.require;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.requireStr;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.str;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.strList;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.synonymsOf;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.truthy;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.validateSource;
import static org.apache.ossie.converter.databricks.OssieConverterCommon.writeStash;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import org.apache.ossie.converter.databricks.OssieConverter.ConversionException;
import org.apache.ossie.converter.databricks.OssieConverter.Notices;
import org.apache.ossie.converter.databricks.OssieConverter.Result;

/**
 * EXPORT direction: Apache Ossie semantic model -&gt; Databricks Metric View v1.1 YAML. This is the
 * harder direction: it reassembles a relationship graph into a join tree. Shared helpers come from
 * {@link OssieConverterCommon}; the public entry point is re-exported through
 * {@link OssieConverter}.
 */
// Map-based YAML manipulation: casts of the parsed Object graph to Map/List are inherently
// unchecked; the asMap/asList helpers guard them, so unchecked warnings here are expected.
@SuppressWarnings("unchecked")
final class OssieToMetricView {

  private OssieToMetricView() {}

  // -- tree node ------------------------------------------------------------
  private static final class Node {
    final String dataset;
    final Map<String, Object> rel; // empty for the fact/root
    final boolean parentIsFrom;
    String alias;
    boolean isOtm;
    final List<Node> children = new ArrayList<>();

    Node(String dataset, Map<String, Object> rel, boolean parentIsFrom) {
      this.dataset = dataset;
      this.rel = rel;
      this.parentIsFrom = parentIsFrom;
    }
  }

  // -- public entry ---------------------------------------------------------
  static Result convertOssieToMetricView(String osiYamlStr, String source) {
    Notices notices = new Notices();
    if (osiYamlStr == null || osiYamlStr.trim().isEmpty()) {
      throw ConversionException.invalidInput(
          "Invalid Apache Ossie YAML: expected a mapping at the root", "the input is empty");
    }
    Object parsed;
    try {
      parsed = loadYaml(osiYamlStr);
    } catch (Exception e) {
      throw ConversionException.invalidInput(
          "Invalid Apache Ossie YAML: " + e.getMessage(),
          "failed to parse YAML: " + e.getMessage(),
          e);
    }
    if (!(parsed instanceof Map)) {
      throw ConversionException.invalidInput(
          "Invalid Apache Ossie YAML: expected a mapping at the root",
          "it is not a mapping at the root");
    }
    Map<String, Object> root = asMap(parsed);
    Object versionValue = get(root, "version");
    String version = str(versionValue);
    if (versionValue == null || version.trim().isEmpty()) {
      throw ConversionException.invalidInput(
          "Invalid Apache Ossie YAML: missing required 'version' field",
          "it is missing the required 'version' field");
    }
    if (!OSSIE_VERSION.equals(version)) {
      throw ConversionException.unsupportedVersion(
          "Unsupported Apache Ossie version '" + version + "'. Supported: " + OSSIE_VERSION,
          version);
    }
    List<Object> models = schemaList(root, "semantic_model", "Apache Ossie YAML");
    if (models.isEmpty()) {
      throw ConversionException.invalidInput(
          "'semantic_model' must be a non-empty list",
          "its 'semantic_model' must be a non-empty list");
    }
    if (!(models.get(0) instanceof Map)) {
      String reason = "Apache Ossie YAML: 'semantic_model[0]' must be a mapping";
      throw ConversionException.invalidInput(reason, reason);
    }
    if (models.size() > 1) {
      notices.warn("model", "multiple semantic models found; converting only the first");
    }
    Map<String, Object> view = convertModel((Map<String, Object>) models.get(0), source, notices);
    try {
      return new Result(MAPPER.writeValueAsString(view), notices.toList());
    } catch (Exception e) {
      throw ConversionException.internalError(
          "failed to serialize Metric View YAML: " + e.getMessage(), e);
    }
  }

  private static Map<String, Object> convertModel(
      Map<String, Object> model, String explicitSource, Notices notices) {
    String name = model.containsKey("name") ? str(get(model, "name")) : "<unnamed>";
    String modelScope = "Model '" + name + "'";
    List<Map<String, Object>> datasetList = schemaMapList(model, "datasets", modelScope);
    if (datasetList.isEmpty()) {
      throw new ConversionException("Model '" + name + "' has no datasets");
    }
    Set<String> seen = new HashSet<>();
    Map<String, Map<String, Object>> datasets = new LinkedHashMap<>();
    Map<String, List<Map<String, Object>>> datasetFields = new LinkedHashMap<>();
    for (Map<String, Object> d : datasetList) {
      String dsName = requireStr(d, "name", "Model '" + name + "': dataset");
      if (!seen.add(dsName.trim().toLowerCase(Locale.ROOT))) {
        throw new ConversionException("Model '" + name + "': duplicate dataset name '" + dsName + "'");
      }
      datasets.put(dsName, d);
      datasetFields.put(dsName, schemaMapList(d, "fields", "Dataset '" + dsName + "'"));
    }
    List<Map<String, Object>> relationships = schemaMapList(model, "relationships", modelScope);
    List<Map<String, Object>> metrics = schemaMapList(model, "metrics", modelScope);

    Map<String, Object> modelStash = readStash(model, notices, modelScope);
    // A non-equi/filtered join has no schema-valid Ossie relationship (from/to columns are
    // required), so MetricViewToOssie stashes it under the model's DATABRICKS custom_extensions
    // (complex_joins) rather than emitting a stub relationship. Rebuild a columns-less
    // relationship for each -- carrying its raw `on` (and rely/cardinality) in the stash that
    // buildJoin restores from -- and merge them in so the join tree includes them.
    for (Map<String, Object> pj : schemaMapList(modelStash, "complex_joins", modelScope)) {
      Map<String, Object> rel = new LinkedHashMap<>();
      rel.put("name", get(pj, "name"));
      rel.put("from", get(pj, "from"));
      rel.put("to", get(pj, "to"));
      Map<String, Object> stash = new LinkedHashMap<>(pj);
      stash.remove("name");
      stash.remove("from");
      stash.remove("to");
      writeStash(rel, stash);
      relationships.add(rel);
    }
    String factHint = explicitSource != null ? explicitSource : str(get(modelStash, STASH_SOURCE_KEY));
    Object[] built = buildJoinTree(name, datasets, relationships, factHint, notices);
    Node root = (Node) built[0];
    String fact = (String) built[1];
    Map<String, Integer> counts = assignAliases(root, fact);
    markOtm(name, root);

    Map<String, Object> factDs = datasets.get(fact);
    Map<String, Object> view = new LinkedHashMap<>();
    view.put("version", MV_VERSION);
    view.put("source", validateSource(get(factDs, "source"), fact));

    String comment = str(get(model, "description"));
    if (truthy(comment)) {
      view.put("comment", comment);
    }
    if (modelStash.containsKey("filter")) {
      view.put("filter", modelStash.get("filter"));
    }

    List<Object> joins = new ArrayList<>();
    for (Node child : root.children) {
      joins.add(buildJoin(child, "source", datasets, notices));
    }
    if (!joins.isEmpty()) {
      view.put("joins", joins);
    }

    Set<String> droppedDims = new HashSet<>();
    Set<String> droppedMeasures = new HashSet<>();
    List<Map<String, Object>> dimensions = new ArrayList<>();
    Set<String> seenDims = new HashSet<>();
    // Dataset name -> the alias path that addresses its columns from the primary source, collected
    // from the same walk the dimensions use so measures qualify identically (see qualifyMeasure).
    Map<String, String> datasetAliasPath = new LinkedHashMap<>();
    for (Object[] entry : nodeOrder(root)) {
      Node node = (Node) entry[0];
      @SuppressWarnings("unchecked")
      List<String> joinPath = (List<String>) entry[1];
      boolean isFact = node == root;
      String prefix = counts.get(node.dataset) > 1 ? node.alias : null;
      // Fixed per node, like `prefix`: hoisted so it is not rebuilt for every field.
      String qualifier = String.join(".", joinPath);
      if (!isFact) {
        // A dataset reachable by more than one path (diamond) appears once per path; the first
        // wins, matching the order dimensions are emitted in.
        datasetAliasPath.putIfAbsent(node.dataset, qualifier);
      }
      for (Map<String, Object> field : datasetFields.get(node.dataset)) {
        String fname = requireStr(field, "name", "dataset '" + node.dataset + "': field");
        if (node.isOtm) {
          notices.warn("field '" + fname + "'",
              "column on a one-to-many-joined table cannot be a dimension "
                  + "(must resolve to one value per source row); dropped");
          droppedDims.add(fname);
          continue;
        }
        Map<String, Object> dim =
            convertField(field, fname, qualifier, isFact, prefix, notices);
        if (dim == null) {
          droppedDims.add(fname);
          continue;
        }
        String dn = (String) dim.get("name");
        if (!seenDims.add(dn.toLowerCase(Locale.ROOT))) {
          throw new ConversionException("dataset '" + node.dataset + "': dimension name '" + dn
              + "' collides with another dimension/measure; Metric Views require unique "
              + "dimension/measure names -- rename before use");
        }
        dimensions.add(dim);
      }
    }

    List<Map<String, Object>> measures = new ArrayList<>();
    // Depends only on the fact name, so compile it once rather than per metric.
    Pattern factQualifier = Pattern.compile("\\b" + Pattern.quote(fact) + "\\.");
    // Likewise fixed once the join tree is walked: one alternation over the joined dataset names.
    Pattern datasetHead = qualifierChainPattern(datasetAliasPath.keySet());
    // Datasets reached by more than one join path (a diamond). A measure names a dataset, so a
    // bare reference to one of these cannot be unambiguously qualified (see convertMetric).
    Set<String> diamondDatasets = new HashSet<>();
    for (String joined : datasetAliasPath.keySet()) {
      if (counts.getOrDefault(joined, 1) > 1) {
        diamondDatasets.add(joined);
      }
    }
    for (Map<String, Object> metric : metrics) {
      Map<String, Object> measure = convertMetric(
          metric, factQualifier, datasetHead, datasetAliasPath, diamondDatasets, seenDims, notices);
      if (measure == null) {
        droppedMeasures.add(str(get(metric, "name")));
        continue;
      }
      measures.add(measure);
    }

    cascadeDrop(dimensions, measures, droppedDims, droppedMeasures, notices);

    // A Metric View must define at least one dimension or measure
    // (SingleSourceMetricView.validate rejects an empty `select`), so a view with neither is one
    // the engine refuses at CREATE. Fail here instead, naming the dropped columns: after
    // cascadeDrop the emptiness is usually a consequence of earlier drops rather than an empty
    // input, and those names are the actionable part.
    if (dimensions.isEmpty() && measures.isEmpty()) {
      StringBuilder msg = new StringBuilder("Model '" + name
          + "' produced no dimensions or measures; a Metric View requires at least one.");
      if (!droppedDims.isEmpty() || !droppedMeasures.isEmpty()) {
        msg.append(" Dropped during conversion:");
        if (!droppedDims.isEmpty()) {
          msg.append(" dimensions ").append(sortedNames(droppedDims));
        }
        if (!droppedMeasures.isEmpty()) {
          msg.append(" measures ").append(sortedNames(droppedMeasures));
        }
        msg.append(" -- see the warnings for why each was dropped.");
      }
      throw new ConversionException(msg.toString());
    }

    if (!dimensions.isEmpty()) {
      view.put("dimensions", dimensions);
    }
    if (!measures.isEmpty()) {
      view.put("measures", measures);
    }
    if (modelStash.containsKey("parameters")) {
      view.put("parameters", modelStash.get("parameters"));
    }
    if (modelStash.containsKey("materialization")) {
      view.put("materialization", modelStash.get("materialization"));
    }

    warnDroppedModel(model, notices);
    return view;
  }

  private static List<Map<String, Object>> schemaMapList(
      Map<String, Object> parent, String key, String scope) {
    List<Object> values = schemaList(parent, key, scope);
    List<Map<String, Object>> result = new ArrayList<>();
    int index = 0;
    for (Object element : values) {
      if (!(element instanceof Map)) {
        String reason = scope + ": '" + key + "[" + index + "]' must be a mapping";
        throw ConversionException.invalidInput(reason, reason);
      }
      result.add((Map<String, Object>) element);
      index++;
    }
    return result;
  }

  private static List<Object> schemaList(
      Map<String, Object> parent, String key, String scope) {
    if (!parent.containsKey(key)) {
      return new ArrayList<>();
    }
    Object value = get(parent, key);
    // A present-but-null value (the bare `key:` YAML idiom) is treated like an absent key, matching
    // the tolerant base reader (asList) and the sibling customExtensions reader. A scalar or map
    // value is still rejected below. Required collections stay guarded by their own emptiness
    // checks, so tolerating null here does not let an empty semantic_model/datasets through.
    if (value == null) {
      return new ArrayList<>();
    }
    if (!(value instanceof List)) {
      String reason = scope + ": '" + key + "' must be a list";
      throw ConversionException.invalidInput(reason, reason);
    }
    return (List<Object>) value;
  }

  private static Object[] buildJoinTree(
      String modelName, Map<String, Map<String, Object>> datasets,
      List<Map<String, Object>> relationships0, String factHint, Notices notices) {
    for (Map<String, Object> rel : relationships0) {
      String scope = "Model '" + modelName + "': relationship '"
          + (rel.containsKey("name") ? str(get(rel, "name")) : "<unnamed>") + "'";
      Object f = require(rel, "from", scope);
      Object t = require(rel, "to", scope);
      if (!datasets.containsKey(str(f)) || !datasets.containsKey(str(t))) {
        throw new ConversionException("Model '" + modelName + "': relationship '"
            + str(get(rel, "name")) + "' references an unknown dataset");
      }
    }
    List<Map<String, Object>> relationships = new ArrayList<>();
    for (Map<String, Object> rel : relationships0) {
      relationships.add(orientByKey(rel, datasets, notices));
    }
    if (datasets.size() > MAX_JOIN_NODES) {
      throw new ConversionException("Model '" + modelName + "' has " + datasets.size()
          + " datasets; at most " + MAX_JOIN_NODES + " are supported.");
    }
    String fact = pickFact(modelName, datasets, relationships, factHint);
    rejectDirectedCycle(modelName, datasets, relationships);

    Map<String, List<String>> adj = new HashMap<>();
    for (String n : datasets.keySet()) {
      adj.put(n, new ArrayList<>());
    }
    for (Map<String, Object> rel : relationships) {
      adj.get(str(get(rel, "from"))).add(str(get(rel, "to")));
      adj.get(str(get(rel, "to"))).add(str(get(rel, "from")));
    }
    Map<String, Integer> dist = new HashMap<>();
    dist.put(fact, 0);
    Deque<String> queue = new ArrayDeque<>();
    queue.add(fact);
    while (!queue.isEmpty()) {
      String cur = queue.poll();
      for (String nb : adj.get(cur)) {
        if (!dist.containsKey(nb)) {
          dist.put(nb, dist.get(cur) + 1);
          queue.add(nb);
        }
      }
    }
    List<String> unreachable = new ArrayList<>();
    for (String n : datasets.keySet()) {
      if (!dist.containsKey(n)) {
        unreachable.add(n);
      }
    }
    if (!unreachable.isEmpty()) {
      java.util.Collections.sort(unreachable);
      throw new ConversionException("Model '" + modelName + "': datasets " + unreachable
          + " are not reachable from fact '" + fact + "' via relationships.");
    }
    Map<String, List<Object[]>> childrenOf = new HashMap<>();
    for (String n : datasets.keySet()) {
      childrenOf.put(n, new ArrayList<>());
    }
    for (Map<String, Object> rel : relationships) {
      String a = str(get(rel, "from"));
      String b = str(get(rel, "to"));
      if (dist.get(a).equals(dist.get(b))) {
        throw new ConversionException("Model '" + modelName + "': relationship '"
            + str(get(rel, "name")) + "' joins two datasets equidistant from the fact; "
            + "the graph is not tree-shaped (it contains a cycle).");
      }
      String parent = dist.get(a) < dist.get(b) ? a : b;
      String child = dist.get(a) < dist.get(b) ? b : a;
      childrenOf.get(parent).add(new Object[] {child, rel, parent.equals(str(get(rel, "from")))});
    }
    int[] counter = {0};
    Node root = build(modelName, fact, new LinkedHashMap<>(), false, childrenOf, counter);
    return new Object[] {root, fact};
  }

  private static Node build(String modelName, String dataset, Map<String, Object> rel,
      boolean parentIsFrom, Map<String, List<Object[]>> childrenOf, int[] counter) {
    counter[0]++;
    if (counter[0] > MAX_JOIN_NODES) {
      throw new ConversionException("Model '" + modelName + "': join graph fans out to more than "
          + MAX_JOIN_NODES + " joins; check for an unintended diamond explosion.");
    }
    Node node = new Node(dataset, rel, parentIsFrom);
    for (Object[] c : childrenOf.get(dataset)) {
      @SuppressWarnings("unchecked")
      Map<String, Object> crel = (Map<String, Object>) c[1];
      node.children.add(build(modelName, (String) c[0], crel, (Boolean) c[2], childrenOf, counter));
    }
    return node;
  }

  private static Map<String, Integer> assignAliases(Node root, String fact) {
    Map<String, Integer> counts = new HashMap<>();
    countNode(root, counts);
    Set<String> used = new HashSet<>();
    used.add("source");
    assign(root, null, fact, counts, used);
    return counts;
  }

  private static void countNode(Node node, Map<String, Integer> counts) {
    counts.merge(node.dataset, 1, Integer::sum);
    for (Node c : node.children) {
      countNode(c, counts);
    }
  }

  private static void assign(Node node, String parentAlias, String fact,
      Map<String, Integer> counts, Set<String> used) {
    String alias;
    if (node.dataset.equals(fact)) {
      alias = "source";
    } else {
      String base;
      if (counts.get(node.dataset) == 1) {
        base = node.dataset;
      } else if (parentAlias != null && !parentAlias.equals("source")) {
        base = parentAlias + "_" + node.dataset;
      } else {
        base = node.dataset;
      }
      alias = base;
      int n = 2;
      while (used.contains(alias)) {
        alias = base + "_" + n;
        n++;
      }
    }
    node.alias = alias;
    used.add(alias);
    for (Node c : node.children) {
      assign(c, alias, fact, counts, used);
    }
  }

  private static String pickFact(String modelName, Map<String, Map<String, Object>> datasets,
      List<Map<String, Object>> relationships, String factHint) {
    if (factHint != null) {
      if (!datasets.containsKey(factHint)) {
        throw new ConversionException(
            "Model '" + modelName + "': requested source '" + factHint + "' is not a dataset");
      }
      return factHint;
    }
    if (datasets.size() > 1 && relationships.isEmpty()) {
      throw new ConversionException("Model '" + modelName + "': " + datasets.size()
          + " datasets but no relationships; cannot determine the fact table.");
    }
    Map<String, Integer> incoming = new LinkedHashMap<>();
    for (String n : datasets.keySet()) {
      incoming.put(n, 0);
    }
    for (Map<String, Object> rel : relationships) {
      incoming.merge(str(get(rel, "to")), 1, Integer::sum);
    }
    List<String> roots = new ArrayList<>();
    for (Map.Entry<String, Integer> e : incoming.entrySet()) {
      if (e.getValue() == 0) {
        roots.add(e.getKey());
      }
    }
    if (roots.isEmpty()) {
      throw new ConversionException("Model '" + modelName + "': join graph contains a cycle "
          + "(no root dataset). A Metric View requires an acyclic, tree-shaped graph.");
    }
    if (roots.size() > 1) {
      java.util.Collections.sort(roots);
      throw new ConversionException("Model '" + modelName + "': multiple candidate fact datasets "
          + roots + ". Name the grain with --source.");
    }
    return roots.get(0);
  }

  /**
   * Marks each joined node with its branch's cardinality and rejects a branch that mixes the two.
   *
   * <p>A Metric View requires every join within one top-level branch to share a single cardinality:
   * `Join.validateSubJoinCardinalities` seeds the expected value from the top-level join and fails
   * any descendant that differs (an absent `cardinality` reads as `many_to_one`). So a mixed branch
   * is rejected in *either* direction -- a many-to-one nested under one-to-many, and equally a
   * one-to-many nested under many-to-one. Emitting one would produce a view the engine refuses at
   * CREATE, so reject it here with a converter-level error instead.
   */
  private static void markOtm(String modelName, Node root) {
    // Each top-level join starts a branch and sets that branch's expected cardinality.
    for (Node top : root.children) {
      boolean branchIsOtm = !top.parentIsFrom;
      top.isOtm = branchIsOtm;
      markOtmVisit(modelName, top, branchIsOtm);
    }
  }

  private static void markOtmVisit(String modelName, Node node, boolean branchIsOtm) {
    for (Node child : node.children) {
      boolean isOtm = !child.parentIsFrom;
      if (isOtm != branchIsOtm) {
        throw new ConversionException("Model '" + modelName + "': join '" + child.alias + "' is "
            + cardinalityName(isOtm) + " but descends from a " + cardinalityName(branchIsOtm)
            + " join; a Metric View requires every join within one top-level branch to share the "
            + "same cardinality.");
      }
      child.isOtm = branchIsOtm;
      markOtmVisit(modelName, child, branchIsOtm);
    }
  }

  private static String cardinalityName(boolean isOtm) {
    return isOtm ? CARD_ONE_TO_MANY : CARD_MANY_TO_ONE;
  }

  /**
   * Rejects a directed cycle in the relationship graph.
   *
   * <p>Checked on the DIRECTED graph, and specifically for an edge back to a dataset on the current
   * DFS stack: a dataset reachable by two distinct paths (a diamond, e.g. `a -> b -> d` plus
   * `a -> c -> d`) is directed-acyclic and legitimately supported via the fan-out aliases, so a
   * test for "reached twice" would wrongly reject it. Only a genuine directed cycle is an error.
   *
   * <p>This replaces relying on the equidistance heuristic below, which only detects a cycle whose
   * closing edge happens to join two datasets at the same BFS distance from the fact: a cycle such
   * as `a -> b -> c -> d -> e -> b` has no equidistant edge, so it used to expand into duplicate
   * join paths (`d` emitted under both `c` and `e`) and fabricate a tree from a cyclic model.
   * `pickFact` does not catch it either -- it only fails when no dataset has zero incoming edges,
   * and here `a` has none. With this check, MAX_JOIN_NODES is purely a fan-out bound rather than
   * the last defense against a cycle.
   */
  private static void rejectDirectedCycle(String modelName,
      Map<String, Map<String, Object>> datasets, List<Map<String, Object>> relationships) {
    Map<String, List<String>> out = new HashMap<>();
    for (String n : datasets.keySet()) {
      out.put(n, new ArrayList<>());
    }
    for (Map<String, Object> rel : relationships) {
      out.get(str(get(rel, "from"))).add(str(get(rel, "to")));
    }
    Set<String> done = new HashSet<>();
    Set<String> onStack = new LinkedHashSet<>();
    for (String n : datasets.keySet()) {
      List<String> cycle = findCycle(n, out, done, onStack);
      if (cycle != null) {
        throw new ConversionException("Model '" + modelName + "': relationships form a directed"
            + " cycle " + String.join(" -> ", cycle)
            + "; a Metric View join graph must be acyclic.");
      }
    }
  }

  /** The cycle path (closing dataset repeated at the end), or null if this subtree is clean. */
  private static List<String> findCycle(
      String node, Map<String, List<String>> out, Set<String> done, Set<String> onStack) {
    if (done.contains(node)) {
      return null;
    }
    if (!onStack.add(node)) {
      // Back-edge: report from the first occurrence of `node` so the message shows just the cycle.
      List<String> cycle = new ArrayList<>();
      boolean seen = false;
      for (String s : onStack) {
        if (s.equals(node)) {
          seen = true;
        }
        if (seen) {
          cycle.add(s);
        }
      }
      cycle.add(node);
      return cycle;
    }
    for (String next : out.get(node)) {
      List<String> cycle = findCycle(next, out, done, onStack);
      if (cycle != null) {
        return cycle;
      }
    }
    onStack.remove(node);
    done.add(node);
    return null;
  }

  /**
   * Rewrites the `<dataset>.` qualifier of a measure expression to the alias path that addresses
   * that dataset's columns from the primary source (`parentJoin.nestedJoin.`).
   *
   * <p>A dataset joined directly to the primary already maps to its own alias, so those rewrites
   * are no-ops; only a nested dataset actually changes. The whole qualifier run is matched at once
   * and resolved from its leaf (see {@link OssieConverterCommon#qualifierChainPattern}), so an
   * expression that already carries a full path -- {@code SUM(customer.region.population)}, which
   * is what an import leaves behind -- is recognized as addressing `region` and re-emitted
   * unchanged rather than qualified a second time. String literals and comments are skipped, as
   * the fact strip does.
   */
  private static String qualifyMeasure(
      String expr, Pattern datasetHead, Map<String, String> datasetAliasPath) {
    return rewriteQualifiers(expr, datasetHead, datasetAliasPath::get);
  }

  /**
   * The first dataset a diamond reference names in {@code expr}, or null if none. A measure
   * addresses a dataset by name, so a bare reference to a dataset reached by more than one join
   * path cannot be unambiguously qualified. Reuses the literal-blind qualifier matcher so a string
   * literal that merely looks like a reference is not mistaken for one; the run is left unchanged
   * (the resolver returns null) since this pass only detects.
   */
  private static String firstDiamondReference(
      String expr, Pattern datasetHead, Set<String> diamondDatasets) {
    if (datasetHead == null || diamondDatasets.isEmpty()) {
      return null;
    }
    String[] found = {null};
    rewriteQualifiers(expr, datasetHead, leaf -> {
      if (found[0] == null && diamondDatasets.contains(leaf)) {
        found[0] = leaf;
      }
      return null;
    });
    return found[0];
  }

  /** Sorted so the error message is deterministic (the dropped-name sets are unordered). */
  private static String sortedNames(Set<String> names) {
    List<String> sorted = new ArrayList<>();
    for (String n : names) {
      if (n != null) {
        sorted.add(n);
      }
    }
    java.util.Collections.sort(sorted);
    return sorted.toString();
  }

  private static List<Object[]> nodeOrder(Node root) {
    List<Object[]> order = new ArrayList<>();
    nodeOrderVisit(root, new ArrayList<>(), order);
    return order;
  }

  private static void nodeOrderVisit(Node node, List<String> path, List<Object[]> order) {
    order.add(new Object[] {node, new ArrayList<>(path)});
    for (Node child : node.children) {
      List<String> childPath = new ArrayList<>(path);
      childPath.add(child.alias);
      nodeOrderVisit(child, childPath, order);
    }
  }

  private static Map<String, Object> buildJoin(Node node, String parentAlias,
      Map<String, Map<String, Object>> datasets, Notices notices) {
    Map<String, Object> rel = node.rel;
    String alias = node.alias;
    Map<String, Object> join = new LinkedHashMap<>();
    join.put("name", alias);
    join.put("source", validateSource(get(datasets.get(node.dataset), "source"), node.dataset));

    Map<String, Object> stash = readStash(rel, notices, "join '" + alias + "'");
    List<String> fromCols = strList(get(rel, "from_columns"));
    List<String> toCols = strList(get(rel, "to_columns"));
    if (stash.containsKey("on")) {
      // A non-equi/filtered `on` with no Ossie relationship representation was preserved verbatim
      // in the DATABRICKS stash by MetricViewToOssie.convertJoin (the relationship carries no
      // from/to columns). Restore it directly rather than rebuilding an `on` from columns.
      join.put("on", stash.get("on"));
    } else {
      validateJoinColumns(rel, fromCols, toCols);
      List<String> parentCols = node.parentIsFrom ? fromCols : toCols;
      List<String> childCols = node.parentIsFrom ? toCols : fromCols;
      if (parentCols.equals(childCols)) {
        join.put("using", new ArrayList<>(parentCols));
      } else {
        List<String> clauses = new ArrayList<>();
        for (int i = 0; i < parentCols.size(); i++) {
          clauses.add(
              parentAlias + "." + parentCols.get(i) + " = " + alias + "." + childCols.get(i));
        }
        join.put("on", String.join(" AND ", clauses));
      }
    }
    if (stash.containsKey("rely")) {
      join.put("rely", stash.get("rely"));
    } else if (node.parentIsFrom && coversUniqueKey(datasets.get(node.dataset), toCols)) {
      Map<String, Object> rely = new LinkedHashMap<>();
      rely.put("at_most_one_match", true);
      join.put("rely", rely);
    }
    if (stash.containsKey("cardinality")) {
      join.put("cardinality", stash.get("cardinality"));
    } else if (!node.parentIsFrom) {
      join.put("cardinality", CARD_ONE_TO_MANY);
    }
    List<Object> nested = new ArrayList<>();
    for (Node c : node.children) {
      nested.add(buildJoin(c, alias, datasets, notices));
    }
    if (!nested.isEmpty()) {
      join.put("joins", nested);
    }
    return join;
  }

  private static boolean coversUniqueKey(Map<String, Object> dataset, List<String> joinCols) {
    Set<String> cols = new HashSet<>(joinCols);
    List<List<String>> keys = new ArrayList<>();
    List<String> pk = strList(get(dataset, "primary_key"));
    if (!pk.isEmpty()) {
      keys.add(pk);
    }
    for (Object k : asList(get(dataset, "unique_keys"))) {
      keys.add(strList(k));
    }
    for (List<String> key : keys) {
      if (!key.isEmpty() && cols.containsAll(key)) {
        return true;
      }
    }
    return false;
  }

  private static Map<String, Object> orientByKey(
      Map<String, Object> rel, Map<String, Map<String, Object>> datasets, Notices notices) {
    List<String> fromCols = strList(get(rel, "from_columns"));
    List<String> toCols = strList(get(rel, "to_columns"));
    if (fromCols.isEmpty() || toCols.isEmpty()) {
      return rel;
    }
    Map<String, Object> toDs = datasets.get(str(get(rel, "to")));
    boolean toHasKeys = !strList(get(toDs, "primary_key")).isEmpty()
        || !asList(get(toDs, "unique_keys")).isEmpty();
    boolean fromCovers = coversUniqueKey(datasets.get(str(get(rel, "from"))), fromCols);
    if (fromCovers && toHasKeys && !coversUniqueKey(toDs, toCols)) {
      notices.warn("relationship '" + str(get(rel, "name")) + "'",
          "from/to looks mislabeled (the `from` columns are a declared key, the `to` columns "
              + "are not); re-orienting so the key side is the `to`/one side");
      Map<String, Object> swapped = new LinkedHashMap<>(rel);
      swapped.put("from", get(rel, "to"));
      swapped.put("to", get(rel, "from"));
      swapped.put("from_columns", toCols);
      swapped.put("to_columns", fromCols);
      return swapped;
    }
    if (fromCovers && !toHasKeys) {
      notices.warn("relationship '" + str(get(rel, "name")) + "'",
          "the `from` columns are a declared key but the `to` side declares none, so from/to "
              + "orientation can't be verified; using it as-is -- check the join direction if "
              + "the resulting cardinality looks inverted");
    }
    return rel;
  }

  private static void validateJoinColumns(
      Map<String, Object> rel, List<String> fromCols, List<String> toCols) {
    String name = str(get(rel, "name"));
    if (fromCols.isEmpty() || toCols.isEmpty()) {
      throw new ConversionException(
          "Relationship '" + name + "': from_columns and to_columns are required");
    }
    if (fromCols.size() != toCols.size()) {
      throw new ConversionException("Relationship '" + name + "': from_columns (" + fromCols.size()
          + ") and to_columns (" + toCols.size() + ") must have the same length");
    }
  }

  private static Map<String, Object> convertField(Map<String, Object> field, String name0,
      String qualifier, boolean isFact, String prefix, Notices notices) {
    String scope = "field '" + name0 + "'";
    String expr = pickExpression(get(field, "expression"), scope);
    if (expr == null) {
      notices.warn(scope, "no DATABRICKS/ANSI_SQL dialect; dropping field");
      return null;
    }
    if (!isFact) {
      if (isSimpleIdentifier(expr)) {
        expr = qualifier + "." + expr;
      } else if (prefix != null) {
        notices.warn(scope, "complex expression on a fanned-out (diamond) join cannot be "
            + "unambiguously qualified; dropped");
        return null;
      } else {
        notices.warn(scope, "complex expression on a joined table; emitted as-is, verify qualification");
      }
    }
    String name = prefix != null ? prefix + "_" + name0 : name0;
    Map<String, Object> dim = new LinkedHashMap<>();
    dim.put("name", name);
    dim.put("expr", expr);
    String comment = mergeDescription(get(field, "description"), get(field, "ai_context"));
    if (truthy(comment)) {
      dim.put("comment", comment);
    }
    String label = str(get(field, "label"));
    if (truthy(label)) {
      dim.put("display_name", label);
    }
    List<String> syns = synonymsOf(get(field, "ai_context"));
    if (!syns.isEmpty()) {
      dim.put("synonyms", truncateSynonyms(syns, scope, notices));
    }
    Map<String, Object> stash = readStash(field, notices, scope);
    if (stash.containsKey("format")) {
      dim.put("format", stash.get("format"));
    }
    warnDroppedField(field, scope, notices);
    return dim;
  }

  private static Map<String, Object> convertMetric(
      Map<String, Object> metric,
      Pattern factQualifier,
      Pattern datasetHead,
      Map<String, String> datasetAliasPath,
      Set<String> diamondDatasets,
      Set<String> seenNames,
      Notices notices) {
    String name = requireStr(metric, "name", "metric");
    String scope = "metric '" + name + "'";
    if (!seenNames.add(name.toLowerCase(Locale.ROOT))) {
      throw new ConversionException("metric '" + name + "' collides with another dimension/measure; "
          + "Metric Views require unique dimension/measure names -- rename before use");
    }
    String expr = pickExpression(get(metric, "expression"), scope);
    if (expr == null) {
      notices.warn(scope, "no DATABRICKS/ANSI_SQL dialect; dropping metric");
      return null;
    }
    String diamond = firstDiamondReference(expr, datasetHead, diamondDatasets);
    if (diamond != null) {
      notices.warn(scope, "expression references dataset '" + diamond + "', reached by more "
          + "than one join path (diamond); cannot be unambiguously qualified; dropped");
      return null;
    }
    // Re-qualify a joined dataset's columns with the alias path that addresses them from the
    // primary source. A Metric View addresses a nested join column by its full path
    // (`parentJoin.nestedJoin.col`), so a bare nested alias at the head is read as struct access on
    // a parameter rather than as a join column -- it fails silently. Dimensions already qualify
    // this way via the joinPath handed to convertField, so this keeps the two directions
    // consistent on the same input.
    expr = qualifyMeasure(expr, datasetHead, datasetAliasPath);
    // Strip a `<fact>.` qualifier so fact columns are bare in measures (the Metric View idiom).
    // Only outside string literals / comments: a literal such as 'customer.us' must not be
    // rewritten, or the measure's predicate changes.
    expr = replaceOutsideLiterals(expr, factQualifier, "");
    Map<String, Object> measure = new LinkedHashMap<>();
    measure.put("name", name);
    measure.put("expr", expr);
    String comment = mergeDescription(get(metric, "description"), get(metric, "ai_context"));
    if (truthy(comment)) {
      measure.put("comment", comment);
    }
    List<String> syns = synonymsOf(get(metric, "ai_context"));
    if (!syns.isEmpty()) {
      measure.put("synonyms", truncateSynonyms(syns, scope, notices));
    }
    Map<String, Object> stash = readStash(metric, notices, scope);
    if (stash.containsKey("format")) {
      measure.put("format", stash.get("format"));
    }
    if (stash.containsKey("window")) {
      measure.put("window", stash.get("window"));
    }
    if (stash.containsKey("partition")) {
      measure.put("partition", stash.get("partition"));
    }
    // The Ossie Metric schema has no `label`, so a measure's display_name round-trips through the
    // DATABRICKS stash (MetricViewToOssie.MEASURE_STASH_KEYS) rather than a native field.
    if (stash.containsKey("display_name")) {
      measure.put("display_name", stash.get("display_name"));
    }
    warnDroppedColumn(metric, scope, notices);
    return measure;
  }

  private static void cascadeDrop(List<Map<String, Object>> dimensions,
      List<Map<String, Object>> measures, Set<String> droppedDims,
      Set<String> droppedMeasures, Notices notices) {
    new CascadeDropper(dimensions, measures, droppedDims, droppedMeasures, notices).run();
  }

  /**
   * Propagates dropped field/metric references without repeatedly compiling the same patterns or
   * rescanning every prior drop on every pass. The worklists preserve the old phase order:
   * dimensions then measures, both in source order, with a dependency discovered after its source
   * position deferred to the next round. This keeps both output and notice ordering stable.
   */
  private static final class CascadeDropper {
    private static final int SEED_PHASE = 0;
    private static final int DIMENSION_PHASE = 1;
    private static final int MEASURE_PHASE = 2;

    private final List<Map<String, Object>> dimensions;
    private final List<Map<String, Object>> measures;
    private final Set<String> droppedDims;
    private final Set<String> droppedMeasures;
    private final Notices notices;
    private final BitSet removedDims = new BitSet();
    private final BitSet removedMeasures = new BitSet();
    private final Map<String, Pattern> dimensionPatterns = new HashMap<>();
    private final Map<String, Pattern> measurePatterns = new HashMap<>();
    private BitSet dimsNow = new BitSet();
    private BitSet dimsNext = new BitSet();
    private BitSet measuresNow = new BitSet();
    private BitSet measuresNext = new BitSet();

    CascadeDropper(List<Map<String, Object>> dimensions,
        List<Map<String, Object>> measures, Set<String> droppedDims,
        Set<String> droppedMeasures, Notices notices) {
      this.dimensions = dimensions;
      this.measures = measures;
      this.droppedDims = droppedDims;
      this.droppedMeasures = droppedMeasures;
      this.notices = notices;
    }

    void run() {
      for (String name : droppedMeasures) {
        propagate(name, true, SEED_PHASE, -1);
      }
      for (String name : droppedDims) {
        propagate(name, false, SEED_PHASE, -1);
      }

      while (!dimsNow.isEmpty() || !measuresNow.isEmpty()) {
        processDimensions();
        processMeasures();
        dimsNow = dimsNext;
        dimsNext = new BitSet();
        measuresNow = measuresNext;
        measuresNext = new BitSet();
      }

      retainSurvivors(dimensions, removedDims);
      retainSurvivors(measures, removedMeasures);
    }

    private void processDimensions() {
      for (int index = dimsNow.nextSetBit(0); index >= 0;
          index = dimsNow.nextSetBit(index + 1)) {
        dimsNow.clear(index);
        drop(index, false, DIMENSION_PHASE);
      }
    }

    private void processMeasures() {
      for (int index = measuresNow.nextSetBit(0); index >= 0;
          index = measuresNow.nextSetBit(index + 1)) {
        measuresNow.clear(index);
        drop(index, true, MEASURE_PHASE);
      }
    }

    private void drop(int index, boolean measure, int phase) {
      BitSet removed = measure ? removedMeasures : removedDims;
      if (removed.get(index)) {
        return;
      }
      Map<String, Object> column = (measure ? measures : dimensions).get(index);
      String name = (String) column.get("name");
      String reference = referencesDropped((String) column.get("expr"), name);
      if (reference == null) {
        return;
      }

      removed.set(index);
      String kind = measure ? "measure" : "dimension";
      notices.warn(kind + " '" + name + "'",
          "references dropped '" + reference
              + "'; dropping (downstream of a dropped field/metric)");
      Set<String> dropped = measure ? droppedMeasures : droppedDims;
      if (dropped.add(name)) {
        propagate(name, measure, phase, index);
      }
    }

    private void propagate(String name, boolean measureReference, int phase, int sourceIndex) {
      if (name == null) {
        return;
      }
      Pattern pattern = referencePattern(name, measureReference);
      for (int index = 0; index < dimensions.size(); index++) {
        if (!isPending(index, false)
            && matches(dimensions.get(index), name, measureReference, pattern)) {
          schedule(index, false, phase, sourceIndex);
        }
      }
      for (int index = 0; index < measures.size(); index++) {
        if (!isPending(index, true)
            && matches(measures.get(index), name, measureReference, pattern)) {
          schedule(index, true, phase, sourceIndex);
        }
      }
    }

    private boolean isPending(int index, boolean measure) {
      if (measure) {
        return removedMeasures.get(index)
            || measuresNow.get(index)
            || measuresNext.get(index);
      }
      return removedDims.get(index) || dimsNow.get(index) || dimsNext.get(index);
    }

    private static boolean matches(Map<String, Object> column, String reference,
        boolean measureReference, Pattern pattern) {
      String name = (String) column.get("name");
      if (!measureReference && reference.equals(name)) {
        return false;
      }
      String expr = (String) column.get("expr");
      return expr.contains(reference) && findOutsideLiterals(expr, pattern);
    }

    private void schedule(int index, boolean measure, int phase, int sourceIndex) {
      if (measure) {
        if (phase != MEASURE_PHASE || index > sourceIndex) {
          measuresNow.set(index);
        } else {
          measuresNext.set(index);
        }
      } else if (phase == SEED_PHASE
          || (phase == DIMENSION_PHASE && index > sourceIndex)) {
        dimsNow.set(index);
      } else {
        dimsNext.set(index);
      }
    }

    private String referencesDropped(String expr, String selfName) {
      for (String name : droppedMeasures) {
        if (name != null && expr.contains(name)
            && findOutsideLiterals(expr, referencePattern(name, true))) {
          return name;
        }
      }
      for (String name : droppedDims) {
        if (name != null && !name.equals(selfName) && expr.contains(name)
            && findOutsideLiterals(expr, referencePattern(name, false))) {
          return name;
        }
      }
      return null;
    }

    private Pattern referencePattern(String name, boolean measure) {
      Map<String, Pattern> patterns = measure ? measurePatterns : dimensionPatterns;
      return patterns.computeIfAbsent(name, ignored -> measure
          ? Pattern.compile("measure\\(\\s*" + Pattern.quote(name) + "\\s*\\)")
          : Pattern.compile("(?<![\\w.])" + Pattern.quote(name) + "(?![\\w.])"));
    }

    private static void retainSurvivors(
        List<Map<String, Object>> columns, BitSet removed) {
      List<Map<String, Object>> survivors = new ArrayList<>(columns.size() - removed.cardinality());
      for (int index = 0; index < columns.size(); index++) {
        if (!removed.get(index)) {
          survivors.add(columns.get(index));
        }
      }
      columns.clear();
      columns.addAll(survivors);
    }
  }

  private static List<String> truncateSynonyms(List<String> syns, String scope, Notices notices) {
    if (syns.size() > SYNONYM_LIMIT) {
      notices.warn(scope, syns.size() + " synonyms exceeds Metric View limit; keeping first " + SYNONYM_LIMIT);
      return new ArrayList<>(syns.subList(0, SYNONYM_LIMIT));
    }
    return syns;
  }

  private static void warnDroppedModel(Map<String, Object> model, Notices notices) {
    if (!foreignVendorExtensions(model).isEmpty()) {
      notices.warn("model", "foreign-vendor custom_extensions dropped");
    }
    if (truthy(get(model, "ai_context"))) {
      notices.warn("model", "model-level ai_context dropped (only the description maps to the view comment)");
    }
    for (Object dsObj : asList(get(model, "datasets"))) {
      Map<String, Object> ds = asMap(dsObj);
      String scope = "dataset '" + str(get(ds, "name")) + "'";
      if (!strList(get(ds, "primary_key")).isEmpty() || !asList(get(ds, "unique_keys")).isEmpty()) {
        notices.warn(scope, "primary_key/unique_keys not stored as columns; used to set "
            + "rely.at_most_one_match on a matching many_to_one join where applicable");
      }
      if (get(ds, "ai_context") instanceof Map && !asMap(get(ds, "ai_context")).isEmpty()) {
        notices.warn(scope, "dataset-level ai_context (object) dropped");
      }
      if (truthy(get(ds, "description"))) {
        notices.warn(scope, "dataset-level description dropped (no per-source comment field)");
      }
      if (!foreignVendorExtensions(ds).isEmpty()) {
        notices.warn(scope, "foreign-vendor custom_extensions dropped");
      }
    }
    for (Object relObj : asList(get(model, "relationships"))) {
      Map<String, Object> rel = asMap(relObj);
      if (truthy(get(rel, "ai_context"))) {
        String rn = rel.containsKey("name") ? str(get(rel, "name")) : "<unnamed>";
        notices.warn("relationship '" + rn + "'", "relationship ai_context dropped");
      }
    }
  }

  private static void warnDroppedField(Map<String, Object> field, String scope, Notices notices) {
    Object dim = get(field, "dimension");
    if (dim instanceof Map && asMap(dim).containsKey("is_time")) {
      notices.warn(scope, "dimension.is_time has no Metric View counterpart; dropped");
    }
    warnDroppedColumn(field, scope, notices);
  }

  /**
   * Notices shared by a field and a metric: the members of an `ai_context` OBJECT that have no
   * Metric View slot, and foreign-vendor extensions.
   *
   * <p>`ai_context` is `string | object` in the Apache Ossie schema. The string form maps to the
   * column comment (mergeDescription) and the object's `synonyms` maps to the column's synonyms,
   * but every other object member -- `instructions`, `examples` -- has nowhere to go. Naming them
   * keeps the "dropped with a notice" contract that the dataset- and model-level checks in
   * warnDroppedModel already honour.
   */
  private static void warnDroppedColumn(Map<String, Object> column, String scope, Notices notices) {
    Object aiContext = get(column, "ai_context");
    if (aiContext instanceof Map) {
      List<String> dropped = new ArrayList<>();
      for (String key : asMap(aiContext).keySet()) {
        if (!"synonyms".equals(key)) {
          dropped.add(key);
        }
      }
      if (!dropped.isEmpty()) {
        java.util.Collections.sort(dropped);
        notices.warn(scope, "ai_context " + dropped
            + " dropped (only 'synonyms' has a Metric View counterpart)");
      }
    }
    if (!foreignVendorExtensions(column).isEmpty()) {
      notices.warn(scope, "foreign-vendor custom_extensions dropped");
    }
  }
}
