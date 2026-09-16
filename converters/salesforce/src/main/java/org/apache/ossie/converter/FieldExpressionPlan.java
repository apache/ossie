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

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.apache.ossie.converter.ExpressionCompiler.Binding;
import org.apache.ossie.converter.ExpressionCompiler.Compiled;
import org.apache.ossie.converter.ExpressionCompiler.Level;
import org.apache.ossie.converter.ExpressionCompiler.Parsed;
import org.apache.ossie.converter.ExpressionCompiler.Reference;
import org.apache.ossie.converter.ExpressionCompiler.Selected;
import org.apache.ossie.converter.MetricFieldResolver.Identifier;

/**
 * A per-conversion field dependency plan. SQL field expressions have dataset-local physical
 * column scope plus explicitly declared semantic aliases; metric scope remains semantic only.
 * Calculated aliases are expanded over direct semantic fields, never guessed global Tua names.
 */
final class FieldExpressionPlan {
    record PlannedField(String dataset, String name, Map<String, Object> source,
            Parsed parsed, Reference directReference, String physicalColumn, String calculatedApiName) {
        boolean direct() { return physicalColumn != null; }
    }

    private record Key(String dataset, String field) {
        @Override public String toString() { return dataset + "." + field; }
    }

    private final Map<String, Map<String, Object>> datasets = new LinkedHashMap<>();
    private final Map<Key, PlannedField> fields = new LinkedHashMap<>();
    private final Map<String, List<PlannedField>> byDataset = new LinkedHashMap<>();
    private final Map<Key, Binding> resolved = new HashMap<>();
    private final Map<Key, Set<Key>> lineage = new HashMap<>();
    private final Map<String, Map<String, Set<PlannedField>>> sqlReferences = new HashMap<>();
    private final Map<String, Map<String, Set<PlannedField>>> tableauReferences = new HashMap<>();
    private final Map<String, Set<List<String>>> qualifiers = new HashMap<>();
    private final Map<Key, Map<String, Object>> exported = new HashMap<>();
    private final Map<String, Object> target;
    private final ArrayDeque<Key> pending = new ArrayDeque<>();
    private boolean indexed;

    FieldExpressionPlan(Map<String, Object> source, Map<String, Object> target) {
        this.target = target;
        Set<String> reserved = new HashSet<>();
        for (String list : List.of("metrics", "semanticCalculatedDimensions", "semanticCalculatedMeasurements")) {
            for (Map<String, Object> item : items(list.equals("metrics") ? source : target, list)) {
                String name = getString(item, list.equals("metrics") ? "name" : "apiName");
                if (name != null) reserved.add(name.toUpperCase(Locale.ROOT));
            }
        }
        for (Map<String, Object> dataset : items(source, "datasets")) {
            String datasetName = requiredName(dataset, "dataset");
            if (datasets.putIfAbsent(datasetName, dataset) != null) {
                throw new IllegalArgumentException("Duplicate dataset declaration '" + datasetName + "'");
            }
            byDataset.put(datasetName, new ArrayList<>());
            qualifiers.put(datasetName, sourceQualifiers(dataset));
            for (Map<String, Object> field : items(dataset, "fields")) {
                String fieldName = requiredName(field, "field in dataset '" + datasetName + "'");
                Key key = new Key(datasetName, fieldName);
                try {
                    Selected selected = ExpressionCompiler.select(field);
                    Parsed parsed = ExpressionCompiler.parse(selected.text(), selected.dialect());
                    Reference reference = ExpressionCompiler.directReference(parsed).orElse(null);
                    // A Tua reference addresses semantic fields, not physical catalog columns.
                    String physical = reference != null && !reference.tableau()
                            ? physicalColumn(datasetName, reference) : null;
                    PlannedField planned = new PlannedField(datasetName, fieldName, field, parsed,
                            physical == null ? null : reference, physical, null);
                    if (fields.putIfAbsent(key, planned) != null) {
                        throw new IllegalArgumentException("Duplicate field declaration");
                    }
                } catch (IllegalArgumentException e) {
                    throw new IllegalArgumentException("Field '" + key + "': " + e.getMessage(), e);
                }
            }
        }
        // Reserve every ordinary name before assigning suffixes, independent of declaration order.
        Map<String, Integer> candidateCounts = new HashMap<>();
        for (PlannedField field : fields.values()) {
            if (!field.direct()) candidateCounts.merge(candidate(field).toUpperCase(Locale.ROOT), 1, Integer::sum);
        }
        for (Key key : fields.keySet().stream().sorted(Comparator.comparing(Key::dataset).thenComparing(Key::field)).toList()) {
            PlannedField field = fields.get(key);
            if (!field.direct()) {
                String base = candidate(field);
                String name = base;
                if (candidateCounts.get(base.toUpperCase(Locale.ROOT)) > 1
                        || reserved.contains(base.toUpperCase(Locale.ROOT))) {
                    String hash = digest(key.dataset() + "\u0000" + key.field());
                    int length = 12;
                    do {
                        if (length > hash.length()) {
                            throw new IllegalArgumentException("Cannot allocate unique calculated field name for '" + key + "'");
                        }
                        name = base + "__" + hash.substring(0, length);
                        length += 4;
                    } while (reserved.contains(name.toUpperCase(Locale.ROOT))
                            || candidateCounts.containsKey(name.toUpperCase(Locale.ROOT)));
                }
                reserved.add(name.toUpperCase(Locale.ROOT));
                fields.put(key, new PlannedField(field.dataset(), field.name(), field.source(), field.parsed(),
                        null, null, name));
            }
        }
        for (PlannedField field : fields.values()) byDataset.get(field.dataset()).add(field);
        byDataset.replaceAll((key, value) -> List.copyOf(value));
        for (PlannedField field : fields.values()) {
            addIndex(tableauReferences, field.dataset(), field.name(), field);
            addIndex(sqlReferences, field.dataset(), normalizeDeclaration(field.name()), field);
            if (field.direct()) {
                List<Identifier> parts = field.directReference().parts();
                addIndex(sqlReferences, field.dataset(), normalize(parts.get(parts.size() - 1)), field);
            }
        }
    }

    List<PlannedField> fields(String dataset) { return byDataset.getOrDefault(dataset, List.of()); }

    boolean isDirect(String dataset, String field) { return field(dataset, field).direct(); }

    String calculatedApiName(String dataset, String field) { return field(dataset, field).calculatedApiName(); }

    boolean hasPhysicalDependencies(String dataset, String field) {
        resolve(dataset, field);
        return !lineage.get(new Key(dataset, field)).isEmpty();
    }

    List<Map<String, Object>> dependencies(String dataset, String field) {
        resolve(dataset, field);
        return lineage.get(new Key(dataset, field)).stream()
                .sorted(Comparator.comparing(Key::dataset).thenComparing(Key::field))
                .map(key -> Map.<String, Object>of("dependentDefinitionApiName", key.dataset(),
                        "dependentFieldApiName", key.field())).toList();
    }

    /** Compile all derived fields, including unused ones, so unsupported fields cannot disappear. */
    void compileAll() {
        indexExported();
        for (PlannedField field : fields.values()) {
            if (!field.direct()) resolve(field.dataset(), field.name());
        }
    }

    /** Exact declaration lookup for a metric resolver that has already resolved identifier spelling. */
    Binding resolve(String dataset, String field) {
        indexExported();
        Key key = new Key(dataset, field);
        Binding cached = resolved.get(key);
        if (cached != null) return cached;
        PlannedField planned = field(dataset, field);
        if (planned.direct()) {
            Map<String, Object> output = exported.get(key);
            if (output == null) throw new IllegalArgumentException("Field '" + key + "' was not exported as a direct field");
            String datatype = getString(planned.source(), "datatype");
            String targetType = getString(output, "dataType");
            if (datatype == null || datatype.isBlank()) datatype = SalesforceDataTypeMapper.toOssie(targetType);
            if (SalesforceDataTypeMapper.toSalesforce(datatype) == null) {
                throw new IllegalArgumentException("Field '" + key + "' has no supported datatype; declare a portable field datatype");
            }
            if (targetType == null || !SalesforceDataTypeMapper.areCompatible(datatype, targetType)) {
                throw new IllegalArgumentException("Field '" + key + "' datatype conflicts with exported Salesforce dataType '" + targetType + "'");
            }
            Binding binding = new Binding(bracket(dataset) + "." + bracket(field), datatype, dataset, Level.ROW);
            resolved.put(key, binding);
            lineage.put(key, Set.of(key));
            return binding;
        }
        if (pending.contains(key)) {
            throw new IllegalArgumentException("Calculated field dependency cycle: "
                    + String.join(" -> ", pending.stream().map(Key::toString).toList()) + " -> " + key);
        }
        if (pending.size() >= 128) {
            throw new IllegalArgumentException("Calculated field dependency depth exceeds 128 at '" + key + "'");
        }
        pending.addLast(key);
        try {
            Set<Key> dependencies = new LinkedHashSet<>();
            Compiled compiled = ExpressionCompiler.compile(planned.parsed(), reference -> bind(planned, reference, dependencies));
            if (compiled.level() == Level.AGGREGATE) {
                throw new IllegalArgumentException("Dataset fields must be row expressions; declare aggregate expressions as metrics");
            }
            String declared = getString(planned.source(), "datatype");
            String datatype = compiled.datatype();
            if (declared != null) {
                if (!compatibleResult(declared, datatype)) {
                    throw new IllegalArgumentException("Declared datatype '" + declared
                            + "' conflicts with calculated result datatype '" + datatype + "'");
                }
                datatype = declared;
            }
            if (SalesforceDataTypeMapper.toSalesforce(datatype) == null) {
                throw new IllegalArgumentException("Calculated field needs a supported, unambiguous datatype");
            }
            // Constant fields still belong to their declared dataset when consumed by a metric.
            Binding binding = new Binding(compiled.expression(), datatype, dataset, Level.ROW);
            resolved.put(key, binding);
            lineage.put(key, Set.copyOf(dependencies));
            return binding;
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Field '" + key + "': " + e.getMessage(), e);
        } finally {
            pending.removeLast();
        }
    }

    private Binding bind(PlannedField owner, Reference reference, Set<Key> dependencies) {
        List<Identifier> parts = reference.parts();
        String text = String.join(".", parts.stream().map(Identifier::text).toList());
        if (parts.isEmpty()) throw new IllegalArgumentException("Empty field reference");
        if (reference.tableau()) {
            if (parts.size() != 2 || !owner.dataset().equals(parts.get(0).text())) {
                throw new IllegalArgumentException("Row field reference '" + text
                        + "' must use [" + owner.dataset() + "].[declared field]; cross-dataset row calculations are unsupported");
            }
        } else if (!validQualifier(owner.dataset(), parts.subList(0, parts.size() - 1))) {
            throw new IllegalArgumentException("Unknown physical or semantic dataset qualifier in row field reference '" + text + "'");
        }
        Identifier name = parts.get(parts.size() - 1);
        Set<PlannedField> matches = (reference.tableau() ? tableauReferences : sqlReferences)
                .getOrDefault(owner.dataset(), Map.of())
                .getOrDefault(reference.tableau() ? name.text() : normalize(name), Set.of());
        if (matches.isEmpty()) {
            throw new IllegalArgumentException("Unknown row field reference '" + text
                    + "'; declare its physical column or semantic field in dataset '" + owner.dataset() + "'");
        }
        if (matches.size() > 1) {
            throw new IllegalArgumentException("Ambiguous row field reference '" + text
                    + "' matches multiple declared physical columns or semantic fields");
        }
        PlannedField match = matches.iterator().next();
        Binding binding = resolve(match.dataset(), match.name());
        dependencies.addAll(lineage.get(new Key(match.dataset(), match.name())));
        return binding;
    }

    private void indexExported() {
        if (indexed) return;
        for (Map<String, Object> dataset : items(target, "semanticDataObjects")) {
            String name = getString(dataset, "apiName");
            for (String kind : List.of("semanticDimensions", "semanticMeasurements")) {
                for (Map<String, Object> item : items(dataset, kind)) {
                    Key key = new Key(name, getString(item, "apiName"));
                    if (exported.putIfAbsent(key, item) != null) {
                        throw new IllegalArgumentException("Duplicate exported field '" + key + "'");
                    }
                }
            }
        }
        indexed = true;
    }

    private PlannedField field(String dataset, String field) {
        PlannedField planned = fields.get(new Key(dataset, field));
        if (planned == null) throw new IllegalArgumentException("Unknown declared field '" + dataset + "." + field + "'");
        return planned;
    }

    private String physicalColumn(String dataset, Reference reference) {
        List<Identifier> parts = reference.parts();
        if (parts.isEmpty() || !validQualifier(dataset, parts.subList(0, parts.size() - 1))) {
            throw new IllegalArgumentException("Direct field qualifier does not match its declared dataset or physical source");
        }
        return parts.get(parts.size() - 1).text();
    }

    private boolean validQualifier(String dataset, List<Identifier> qualifier) {
        return qualifier.isEmpty() || qualifiers.get(dataset).contains(qualifier.stream()
                .map(FieldExpressionPlan::normalize).toList());
    }

    private static Set<List<String>> sourceQualifiers(Map<String, Object> dataset) {
        Set<List<String>> result = new HashSet<>();
        result.add(List.of(normalizeDeclaration(getString(dataset, "name"))));
        String source = getString(dataset, "source");
        if (source != null) {
            try {
                Reference ref = ExpressionCompiler.directReference(ExpressionCompiler.parse(source, "ANSI_SQL")).orElse(null);
                if (ref != null) {
                    List<String> parts = ref.parts().stream().map(FieldExpressionPlan::normalize).toList();
                    for (int i = 0; i < parts.size(); i++) result.add(List.copyOf(parts.subList(i, parts.size())));
                }
            } catch (IllegalArgumentException ignored) {
                // Opaque external source identifiers do not create implicit SQL aliases.
            }
        }
        return Set.copyOf(result);
    }

    private static void addIndex(Map<String, Map<String, Set<PlannedField>>> index,
            String dataset, String name, PlannedField field) {
        index.computeIfAbsent(dataset, key -> new HashMap<>())
                .computeIfAbsent(name, key -> new LinkedHashSet<>()).add(field);
    }

    private static boolean compatibleResult(String declared, String inferred) {
        if (SalesforceDataTypeMapper.toSalesforce(declared) == null) return false;
        if (inferred == null || declared.equals(inferred)) return true;
        return Set.of("Decimal", "Float").contains(declared)
                && Set.of("Integer", "Decimal", "Float").contains(inferred);
    }

    private static String candidate(PlannedField field) {
        String name = (field.dataset() + "__" + field.name()).replaceAll("[^A-Za-z0-9_]", "_");
        if (name.isEmpty() || Character.isDigit(name.charAt(0))) name = "field_" + name;
        return name;
    }

    private static String digest(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required by the Java runtime", e);
        }
    }

    private static String bracket(String name) {
        if (name == null || name.isBlank() || name.contains("[") || name.contains("]")
                || name.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("Semantic name '" + name + "' cannot be represented safely in Tua");
        }
        return "[" + name + "]";
    }

    private static String normalize(Identifier name) {
        return name.quoted() ? name.text() : name.text().toUpperCase(Locale.ROOT);
    }

    private static String normalizeDeclaration(String name) {
        if (name.startsWith("\"") && name.endsWith("\"") && name.length() >= 2) {
            String text = name.substring(1, name.length() - 1);
            if (text.isEmpty() || text.replace("\"\"", "").contains("\"")) {
                throw new IllegalArgumentException("Invalid quoted declaration name '" + name + "'");
            }
            return text.replace("\"\"", "\"");
        }
        return name.toUpperCase(Locale.ROOT);
    }

    private static String requiredName(Map<String, Object> item, String kind) {
        String name = getString(item, "name");
        if (name == null || name.isBlank()) throw new IllegalArgumentException("Missing name for " + kind);
        return name;
    }

    private static List<Map<String, Object>> items(Map<String, Object> map, String name) {
        List<Object> list = getList(map, name);
        return list == null ? List.of() : streamMaps(list).toList();
    }
}
