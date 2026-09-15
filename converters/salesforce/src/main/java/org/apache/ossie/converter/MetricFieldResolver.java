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

import static org.apache.ossie.util.DataStructureUtils.getList;
import static org.apache.ossie.util.DataStructureUtils.getString;
import static org.apache.ossie.util.DataStructureUtils.streamMaps;

import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/** Binds metric references to declared fields that the Salesforce converter actually exported. */
final class MetricFieldResolver {

    record Identifier(String text, boolean quoted) {}

    record ResolvedField(String expression, String datatype, String dataset) {}

    private record Field(Map<String, Object> dataset, Map<String, Object> field) {}

    private final List<Map<String, Object>> datasets;
    private final List<Map<String, Object>> targetDatasets;
    private final List<Map<String, Object>> relationships;

    MetricFieldResolver(Map<String, Object> sourceModel, Map<String, Object> targetModel) {
        datasets = items(sourceModel, "datasets");
        targetDatasets = items(targetModel, "semanticDataObjects");
        relationships = items(targetModel, "semanticRelationships");
    }

    /** Checks exported connectivity only; join grain and cardinality still require native validation. */
    void validateDatasets(Set<String> referenced) {
        if (referenced.size() < 2) {
            return;
        }
        Map<String, Set<String>> graph = new HashMap<>();
        for (Map<String, Object> dataset : targetDatasets) {
            String name = getString(dataset, "apiName");
            if (name != null) {
                graph.put(name, new HashSet<>());
            }
        }
        for (Map<String, Object> relationship : relationships) {
            String left = getString(relationship, "leftSemanticDefinitionApiName");
            String right = getString(relationship, "rightSemanticDefinitionApiName");
            if (graph.containsKey(left) && graph.containsKey(right)) {
                graph.get(left).add(right);
                graph.get(right).add(left);
            }
        }
        Set<String> visited = new HashSet<>();
        ArrayDeque<String> pending = new ArrayDeque<>();
        pending.add(referenced.iterator().next());
        while (!pending.isEmpty()) {
            String dataset = pending.removeFirst();
            if (visited.add(dataset)) {
                pending.addAll(graph.getOrDefault(dataset, Set.of()));
            }
        }
        if (!visited.containsAll(referenced)) {
            throw new IllegalArgumentException("Metric references disconnected datasets "
                    + referenced.stream().sorted().collect(Collectors.joining(", "))
                    + "; declare supported relationships connecting them before exporting the metric");
        }
    }

    ResolvedField resolve(List<Identifier> parts, boolean tableau) {
        String reference = parts.stream().map(Identifier::text).collect(Collectors.joining("."));
        if (parts.isEmpty() || parts.size() > 2) {
            throw new IllegalArgumentException("Reference '" + reference
                    + "' must name a declared field or dataset.field; physical source paths are unsupported");
        }
        if (tableau && parts.size() != 2) {
            throw new IllegalArgumentException("TABLEAU field reference '" + reference
                    + "' must use [dataset].[field]");
        }

        List<Map<String, Object>> candidates = datasets;
        if (parts.size() == 2) {
            candidates = datasets.stream()
                    .filter(dataset -> matches(parts.get(0), getString(dataset, "name"), tableau))
                    .toList();
            if (candidates.isEmpty()) {
                throw new IllegalArgumentException("Unknown dataset in reference '" + reference
                        + "'; use a declared dataset name, not its physical source");
            }
            if (candidates.size() > 1) {
                throw new IllegalArgumentException("Ambiguous dataset in reference '" + reference
                        + "'; dataset declarations must have distinct names");
            }
        }

        Identifier fieldName = parts.get(parts.size() - 1);
        List<Field> fields = new ArrayList<>();
        for (Map<String, Object> dataset : candidates) {
            for (Map<String, Object> field : items(dataset, "fields")) {
                if (matches(fieldName, getString(field, "name"), tableau)) {
                    fields.add(new Field(dataset, field));
                }
            }
        }
        if (fields.isEmpty()) {
            throw new IllegalArgumentException("Unknown field reference '" + reference
                    + "'; declare the field under datasets[].fields before exporting the metric");
        }
        if (fields.size() > 1) {
            throw new IllegalArgumentException("Ambiguous field reference '" + reference
                    + "'; qualify the dataset and remove duplicate field declarations");
        }

        Field match = fields.get(0);
        String datasetName = getString(match.dataset(), "name");
        // An unqualified field must not accidentally select one of two equivalent datasets.
        if (datasets.stream().filter(dataset -> equivalentDeclaration(
                datasetName, getString(dataset, "name"), tableau)).count() > 1) {
            throw new IllegalArgumentException("Ambiguous dataset for reference '" + reference
                    + "'; dataset declarations must have distinct names");
        }
        String sourceFieldName = getString(match.field(), "name");
        Map<String, Object> targetDataset = exportedItem(targetDatasets, datasetName,
                "dataset", reference);
        List<Map<String, Object>> targetFields = new ArrayList<>(items(targetDataset, "semanticDimensions"));
        targetFields.addAll(items(targetDataset, "semanticMeasurements"));
        Map<String, Object> targetField = exportedItem(targetFields, sourceFieldName, "field", reference);

        String datatype = getString(match.field(), "datatype");
        String targetType = getString(targetField, "dataType");
        if (datatype == null || datatype.isBlank()) {
            datatype = SalesforceDataTypeMapper.toOssie(targetType);
        }
        if (SalesforceDataTypeMapper.toSalesforce(datatype) == null) {
            throw new IllegalArgumentException("Field reference '" + reference
                    + "' has no supported datatype; declare a portable field datatype");
        }
        if (targetType == null || targetType.isBlank()
                || !SalesforceDataTypeMapper.areCompatible(datatype, targetType)) {
            throw new IllegalArgumentException("Field reference '" + reference + "' has datatype '"
                    + datatype + "' but exported Salesforce dataType '" + targetType
                    + "'; use compatible field types");
        }

        String targetDatasetName = getString(targetDataset, "apiName");
        String targetFieldName = getString(targetField, "apiName");
        return new ResolvedField(bracket(targetDatasetName) + "." + bracket(targetFieldName),
                datatype, targetDatasetName);
    }

    private static Map<String, Object> exportedItem(List<Map<String, Object>> items,
            String name, String kind, String reference) {
        List<Map<String, Object>> matches = items.stream()
                .filter(item -> name.equals(getString(item, "apiName"))).toList();
        if (matches.isEmpty()) {
            throw new IllegalArgumentException("Declared " + kind + " in reference '" + reference
                    + "' was not exported as a direct Salesforce semantic " + kind
                    + "; calculated or omitted fields are unsupported in metric references");
        }
        if (matches.size() > 1) {
            throw new IllegalArgumentException("Ambiguous exported Salesforce " + kind + " for reference '"
                    + reference + "'; apiName values must be unique");
        }
        return matches.get(0);
    }

    private static boolean matches(Identifier reference, String declaration, boolean tableau) {
        if (declaration == null) {
            return false;
        }
        return tableau ? reference.text().equals(declaration)
                : normalize(reference).equals(normalizeDeclaration(declaration));
    }

    private static boolean equivalentDeclaration(String first, String second, boolean tableau) {
        return second != null && (tableau ? first.equals(second)
                : normalizeDeclaration(first).equals(normalizeDeclaration(second)));
    }

    private static String normalize(Identifier identifier) {
        return identifier.quoted() ? identifier.text() : identifier.text().toUpperCase(Locale.ROOT);
    }

    private static String normalizeDeclaration(String name) {
        if (name.startsWith("\"") && name.endsWith("\"") && name.length() >= 2) {
            String text = name.substring(1, name.length() - 1);
            String unescaped = text.replace("\"\"", "");
            if (text.isEmpty() || unescaped.contains("\"")) {
                throw new IllegalArgumentException("Invalid quoted declaration name '" + name + "'");
            }
            return text.replace("\"\"", "\"");
        }
        return name.toUpperCase(Locale.ROOT);
    }

    private static String bracket(String name) {
        if (name == null || name.isBlank() || name.indexOf('[') >= 0 || name.indexOf(']') >= 0
                || name.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("Exported apiName '" + name
                    + "' cannot be represented safely in a TABLEAU field reference; rename it");
        }
        return "[" + name + "]";
    }

    private static List<Map<String, Object>> items(Map<String, Object> map, String key) {
        List<Object> values = getList(map, key);
        return values == null ? List.of() : streamMaps(values).toList();
    }
}
