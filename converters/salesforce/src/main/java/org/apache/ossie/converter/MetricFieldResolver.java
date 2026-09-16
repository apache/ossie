/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

package org.apache.ossie.converter;

import static org.apache.ossie.util.DataStructureUtils.*;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

/** Model-scoped indexes bind metric references to validated exported semantic identities. */
final class MetricFieldResolver {
    record Identifier(String text, boolean quoted) {}
    record ResolvedField(String expression, String datatype, String dataset) {}
    private record Field(String dataset, String name, Map<String, Object> properties) {}

    private final NameIndex<Map<String, Object>> datasets = new NameIndex<>();
    private final NameIndex<Field> unqualifiedFields = new NameIndex<>();
    private final Map<String, NameIndex<Field>> fieldsByDataset = new HashMap<>();
    private final Map<String, List<Map<String, Object>>> targetDatasets;
    private final Map<String, Map<String, List<Map<String, Object>>>> targetFields = new HashMap<>();
    private final Map<String, List<Map<String, Object>>> targetCalculatedFields;
    private final Map<String, Integer> components;
    private final FieldExpressionPlan fieldPlan;

    MetricFieldResolver(Map<String, Object> sourceModel, Map<String, Object> targetModel) {
        this(sourceModel, targetModel, null);
    }

    MetricFieldResolver(Map<String, Object> sourceModel, Map<String, Object> targetModel,
                        FieldExpressionPlan fieldPlan) {
        this.fieldPlan = fieldPlan;
        for (Map<String, Object> dataset : items(sourceModel, "datasets")) {
            String datasetName = getString(dataset, "name");
            datasets.add(datasetName, dataset);
            NameIndex<Field> fields = fieldsByDataset.computeIfAbsent(datasetName, ignored -> new NameIndex<>());
            for (Map<String, Object> properties : items(dataset, "fields")) {
                String name = getString(properties, "name");
                Field field = new Field(datasetName, name, properties);
                fields.add(name, field);
                unqualifiedFields.add(name, field);
            }
        }
        List<Map<String, Object>> targets = items(targetModel, "semanticDataObjects");
        targetDatasets = index(targets, item -> getString(item, "apiName"));
        for (Map<String, Object> dataset : targets) {
            List<Map<String, Object>> fields = new ArrayList<>(items(dataset, "semanticDimensions"));
            fields.addAll(items(dataset, "semanticMeasurements"));
            targetFields.put(getString(dataset, "apiName"), index(fields, item -> getString(item, "apiName")));
        }
        targetCalculatedFields = index(items(targetModel, "semanticCalculatedDimensions"),
                item -> getString(item, "apiName"));
        components = connectedComponents(targetDatasets.keySet(), items(targetModel, "semanticRelationships"));
    }

    /** Connectivity is a prerequisite; it is not a proof of native join-grain equivalence. */
    void validateDatasets(Set<String> referenced) {
        if (referenced.size() < 2) return;
        Integer component = components.get(referenced.iterator().next());
        if (component == null || referenced.stream().anyMatch(name -> !component.equals(components.get(name)))) {
            throw new IllegalArgumentException("Metric references disconnected datasets "
                    + referenced.stream().sorted().collect(Collectors.joining(", "))
                    + "; declare supported relationships connecting them before exporting the metric");
        }
    }

    boolean hasUnqualifiedField(Identifier identifier, boolean tableau) {
        return !unqualifiedFields.find(identifier, tableau).isEmpty();
    }

    ExpressionCompiler.Binding resolveBinding(ExpressionCompiler.Reference reference) {
        return binding(reference.parts(), reference.tableau());
    }

    ResolvedField resolve(List<Identifier> parts, boolean tableau) {
        ExpressionCompiler.Binding value = binding(parts, tableau);
        return new ResolvedField(value.expression(), value.datatype(), value.dataset());
    }

    private ExpressionCompiler.Binding binding(List<Identifier> parts, boolean tableau) {
        String reference = parts.stream().map(Identifier::text).collect(Collectors.joining("."));
        if (parts.isEmpty() || parts.size() > 2) {
            throw new IllegalArgumentException("Reference '" + reference
                    + "' must name a declared field or dataset.field; physical source paths are unsupported");
        }
        if (tableau && parts.size() != 2) {
            throw new IllegalArgumentException("TABLEAU field reference '" + reference + "' must use [dataset].[field]");
        }
        List<Field> candidates;
        if (parts.size() == 2) {
            List<Map<String, Object>> matches = datasets.find(parts.get(0), tableau);
            if (matches.isEmpty()) throw new IllegalArgumentException("Unknown dataset in reference '" + reference
                    + "'; use a declared dataset name, not its physical source");
            if (matches.size() > 1) throw new IllegalArgumentException("Ambiguous dataset in reference '" + reference
                    + "'; dataset declarations must have distinct names");
            candidates = fieldsByDataset.get(getString(matches.get(0), "name")).find(parts.get(1), tableau);
        } else {
            candidates = unqualifiedFields.find(parts.get(0), tableau);
        }
        if (candidates.isEmpty()) throw new IllegalArgumentException("Unknown field reference '" + reference
                + "'; declare the field under datasets[].fields before exporting the metric");
        if (candidates.size() > 1) throw new IllegalArgumentException("Ambiguous field reference '" + reference
                + "'; qualify the dataset and remove duplicate field declarations");
        Field match = candidates.get(0);
        if (datasets.declarations(match.dataset, tableau).size() > 1) {
            throw new IllegalArgumentException("Ambiguous dataset for reference '" + reference
                    + "'; dataset declarations must have distinct names");
        }
        Map<String, Object> targetDataset = exported(targetDatasets, match.dataset, "dataset", reference);
        if (fieldPlan != null && !fieldPlan.isDirect(match.dataset, match.name)) {
            Map<String, Object> targetField = exported(targetCalculatedFields,
                    fieldPlan.calculatedApiName(match.dataset, match.name), "field", reference);
            ExpressionCompiler.Binding resolved = fieldPlan.resolve(match.dataset, match.name);
            if (!fieldPlan.hasPhysicalDependencies(match.dataset, match.name)) {
                throw new IllegalArgumentException("Field reference '" + reference
                        + "' is a constant row field without a physical dataset anchor; "
                        + "combine it with a direct field in a derived row expression before using it in a metric");
            }
            checkType(resolved.datatype(), getString(targetField, "dataType"), reference);
            return resolved;
        }
        Map<String, Object> targetField = exported(targetFields.get(match.dataset), match.name, "field", reference);
        String datatype = getString(match.properties, "datatype");
        String targetType = getString(targetField, "dataType");
        if (datatype == null || datatype.isBlank()) datatype = SalesforceDataTypeMapper.toOssie(targetType);
        checkType(datatype, targetType, reference);
        String datasetName = getString(targetDataset, "apiName");
        return new ExpressionCompiler.Binding(bracket(datasetName) + "." + bracket(getString(targetField, "apiName")),
                datatype, datasetName);
    }

    private static void checkType(String datatype, String targetType, String reference) {
        if (SalesforceDataTypeMapper.toSalesforce(datatype) == null) {
            throw new IllegalArgumentException("Field reference '" + reference
                    + "' has no supported datatype; declare a portable field datatype");
        }
        if (targetType == null || targetType.isBlank() || !SalesforceDataTypeMapper.areCompatible(datatype, targetType)) {
            throw new IllegalArgumentException("Field reference '" + reference + "' has datatype '" + datatype
                    + "' but exported Salesforce dataType '" + targetType + "'; use compatible field types");
        }
    }

    private static Map<String, Object> exported(Map<String, List<Map<String, Object>>> index,
                                                String name, String kind, String reference) {
        List<Map<String, Object>> matches = index == null ? List.of() : index.getOrDefault(name, List.of());
        if (matches.isEmpty()) throw new IllegalArgumentException("Declared " + kind + " in reference '" + reference
                + "' was not exported as a direct Salesforce semantic " + kind
                + "; calculated or omitted fields are unsupported in metric references without a compiled field plan");
        if (matches.size() > 1) throw new IllegalArgumentException("Ambiguous exported Salesforce " + kind
                + " for reference '" + reference + "'; apiName values must be unique");
        return matches.get(0);
    }

    private static Map<String, Integer> connectedComponents(Set<String> names, List<Map<String, Object>> relationships) {
        Map<String, Set<String>> graph = new LinkedHashMap<>();
        names.forEach(name -> graph.put(name, new HashSet<>()));
        for (Map<String, Object> relationship : relationships) {
            if (Boolean.FALSE.equals(relationship.get("isEnabled"))) continue;
            String left = getString(relationship, "leftSemanticDefinitionApiName");
            String right = getString(relationship, "rightSemanticDefinitionApiName");
            if (graph.containsKey(left) && graph.containsKey(right)) {
                graph.get(left).add(right); graph.get(right).add(left);
            }
        }
        Map<String, Integer> result = new HashMap<>();
        for (String name : names) {
            if (result.containsKey(name)) continue;
            int component = result.size();
            ArrayDeque<String> pending = new ArrayDeque<>(); pending.add(name);
            while (!pending.isEmpty()) {
                String current = pending.removeFirst();
                if (result.putIfAbsent(current, component) == null) pending.addAll(graph.get(current));
            }
        }
        return Map.copyOf(result);
    }

    static String normalize(Identifier identifier) {
        return identifier.quoted() ? identifier.text() : identifier.text().toUpperCase(Locale.ROOT);
    }

    static String normalizeDeclaration(String name) {
        if (name.startsWith("\"") && name.endsWith("\"") && name.length() >= 2) {
            String text = name.substring(1, name.length() - 1);
            if (text.isEmpty() || text.replace("\"\"", "").contains("\"")) {
                throw new IllegalArgumentException("Invalid quoted declaration name '" + name + "'");
            }
            return text.replace("\"\"", "\"");
        }
        return name.toUpperCase(Locale.ROOT);
    }

    static String bracket(String name) {
        if (name == null || name.isBlank() || name.indexOf('[') >= 0 || name.indexOf(']') >= 0
                || name.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("Exported apiName '" + name
                    + "' cannot be represented safely in a TABLEAU field reference; rename it");
        }
        return "[" + name + "]";
    }

    private static <T> Map<String, List<T>> index(List<T> values, Function<T, String> name) {
        Map<String, List<T>> result = new HashMap<>();
        values.forEach(value -> result.computeIfAbsent(name.apply(value), ignored -> new ArrayList<>()).add(value));
        return result;
    }

    private static final class NameIndex<T> {
        private final Map<String, List<T>> exact = new HashMap<>();
        private final Map<String, List<T>> sql = new HashMap<>();
        void add(String name, T value) {
            if (name == null) return;
            exact.computeIfAbsent(name, ignored -> new ArrayList<>()).add(value);
            sql.computeIfAbsent(normalizeDeclaration(name), ignored -> new ArrayList<>()).add(value);
        }
        List<T> find(Identifier identifier, boolean tableau) {
            return (tableau ? exact : sql).getOrDefault(tableau ? identifier.text() : normalize(identifier), List.of());
        }
        List<T> declarations(String name, boolean tableau) {
            return (tableau ? exact : sql).getOrDefault(tableau ? name : normalizeDeclaration(name), List.of());
        }
    }

    private static List<Map<String, Object>> items(Map<String, Object> map, String key) {
        List<Object> values = getList(map, key);
        return values == null ? List.of() : streamMaps(values).toList();
    }
}
