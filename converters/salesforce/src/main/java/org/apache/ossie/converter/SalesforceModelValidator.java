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

import static org.apache.ossie.converter.ConverterConstants.*;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import org.apache.ossie.exception.ConversionException;

/**
 * Checks references and preservation after all handlers and native extensions have run.
 * JSON schema validation remains separate: a structurally valid model can still contain
 * duplicate identities, dangling references, omitted entities, or disconnected calculations.
 * This validates declared metadata, not actual uniqueness or data in a Salesforce catalog.
 */
public final class SalesforceModelValidator {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Set<String> CARDINALITIES = Set.of(
            "OneToOne", "OneToMany", "ManyToOne", "ManyToMany", "Unspecified");
    private static final Set<String> JOIN_TYPES = Set.of("Auto", "Inner", "Left", "Right", "Full");
    private static final List<String> DIRECT_FIELDS = List.of(SEMANTIC_DIMENSIONS, SEMANTIC_MEASUREMENTS);
    private static final List<String> CALCULATED_FIELDS =
            List.of(SEMANTIC_CALCULATED_DIMENSIONS, SEMANTIC_CALCULATED_MEASUREMENTS);

    /** Validates a model whose source fields are all direct bindings. */
    public void validate(Map<String, Object> sourceModel, Map<String, Object> targetModel) {
        validate(sourceModel, targetModel, null);
    }

    /** Uses the existing field plan to verify coverage without compiling expressions again. */
    public void validate(Map<String, Object> sourceModel, Map<String, Object> targetModel,
            FieldExpressionPlan fieldPlan) {
        String modelName = text(sourceModel, NAME, "Ossie model");
        if (!modelName.equals(text(targetModel, API_NAME, "Salesforce model"))) {
            fail("Model '" + modelName + "' changed identity during conversion");
        }
        Map<String, Map<String, Object>> sources = index(items(sourceModel, DATASETS), NAME, "dataset", true);
        Map<String, Map<String, Object>> targets = index(items(targetModel, SEMANTIC_DATA_OBJECTS), API_NAME,
                "exported dataset", false);
        Map<String, Map<String, Object>> calculations = calculationIndex(targetModel);
        Set<String> compiledCalculations = new HashSet<>();
        Map<String, Map<String, Map<String, Object>>> targetFields = new LinkedHashMap<>();
        for (var entry : targets.entrySet()) {
            String scope = "dataset '" + entry.getKey() + "'";
            text(entry.getValue(), "dataObjectName", scope);
            Map<String, Map<String, Object>> fields = directFields(entry.getValue());
            for (var field : fields.entrySet()) {
                text(field.getValue(), DATA_OBJECT_FIELD_NAME, scope + " field '" + field.getKey() + "'");
            }
            targetFields.put(entry.getKey(), fields);
        }
        for (var entry : sources.entrySet()) {
            String dataset = entry.getKey();
            require(targets, dataset, "Declared dataset '" + dataset + "' was not exported");
            Map<String, Map<String, Object>> fields = sourceFields(entry.getValue());
            validateKeys(entry.getValue(), fields, dataset);
            for (String field : fields.keySet()) {
                if (targetFields.get(dataset).containsKey(field)) {
                    continue;
                }
                String calculated = fieldPlan == null ? null : fieldPlan.calculatedApiName(dataset, field);
                if (calculated == null || !calculations.containsKey(calculated)) {
                    fail("Declared field '" + dataset + "." + field + "' was not exported");
                }
                compiledCalculations.add(calculated);
            }
        }
        Map<String, Map<String, Object>> sourceMetrics = index(items(sourceModel, METRICS), NAME, "metric", true);
        Map<String, Map<String, Object>> targetMetrics = index(items(targetModel, SEMANTIC_CALCULATED_MEASUREMENTS),
                API_NAME, "exported metric", false);
        for (String metric : sourceMetrics.keySet()) {
            require(targetMetrics, metric, "Declared metric '" + metric + "' was not exported");
            compiledCalculations.add(metric);
        }
        validateNativeArrayCoverage(sourceModel, targetModel);
        validateRelationshipDeclarations(sourceModel, targetModel);
        validateRelationships(sourceModel, targetModel);
        validateCalculations(calculations, targetFields, targetModel, compiledCalculations);
    }

    private static void validateNativeArrayCoverage(Map<String, Object> source, Map<String, Object> target) {
        for (Map<String, Object> extension : items(source, CUSTOM_EXTENSIONS)) {
            if (!VENDOR_NAME_VALUE.equals(extension.get(VENDOR_NAME))) continue;
            Map<String, Object> nativeModel;
            try {
                nativeModel = JSON.readValue(text(extension, DATA, "Salesforce extension"), new TypeReference<>() {});
            } catch (java.io.IOException e) {
                throw new ConversionException("Model Salesforce extension contains invalid JSON", e);
            }
            if (nativeModel == null) fail("Model Salesforce extension must contain a JSON object");
            for (String array : List.of(SEMANTIC_DATA_OBJECTS, SEMANTIC_RELATIONSHIPS,
                    SEMANTIC_CALCULATED_DIMENSIONS, SEMANTIC_CALCULATED_MEASUREMENTS)) {
                Map<String, Map<String, Object>> expected = index(items(nativeModel, array), API_NAME,
                        "native extension " + array, false);
                Map<String, Map<String, Object>> actual = index(items(target, array), API_NAME, array, false);
                for (String name : expected.keySet()) {
                    require(actual, name, "Native extension " + array + " entity '" + name
                            + "' was not exported; merge it into the core model instead of silently replacing its array");
                }
            }
        }
    }

    /** Checks every declared edge before mapping; no field or edge may be silently dropped. */
    static void validateRelationshipDeclarations(Map<String, Object> source, Map<String, Object> target) {
        Map<String, Map<String, Object>> sources = index(items(source, DATASETS), NAME, "dataset", true);
        Map<String, Map<String, Object>> targets = index(items(target, SEMANTIC_DATA_OBJECTS), API_NAME,
                "exported dataset", false);
        Map<String, Map<String, Object>> relationships = index(items(source, RELATIONSHIPS), NAME,
                "relationship", true);
        for (var entry : relationships.entrySet()) {
            String scope = "Relationship '" + entry.getKey() + "'";
            Map<String, Object> relation = entry.getValue();
            String from = text(relation, FROM, scope);
            String to = text(relation, TO, scope);
            Map<String, Object> fromDataset = require(sources, from, scope + " has unknown from dataset '" + from + "'");
            Map<String, Object> toDataset = require(sources, to, scope + " has unknown to dataset '" + to + "'");
            Map<String, Object> fromTarget = require(targets, from, scope + " has unexported dataset '" + from + "'");
            Map<String, Object> toTarget = require(targets, to, scope + " has unexported dataset '" + to + "'");
            List<String> fromColumns = names(relation.get(FROM_COLUMNS), scope + " from_columns");
            List<String> toColumns = names(relation.get(TO_COLUMNS), scope + " to_columns");
            if (fromColumns.size() != toColumns.size()) {
                fail(scope + " must have the same number of from_columns and to_columns");
            }
            checkColumns(scope, from, fromColumns, fromDataset, fromTarget);
            checkColumns(scope, to, toColumns, toDataset, toTarget);
            validateKeys(toDataset, sourceFields(toDataset), to);
            validateKeys(fromDataset, sourceFields(fromDataset), from);
        }
    }

    /** Validates final native edges, including relationships restored from native extensions. */
    static void validateRelationships(Map<String, Object> source, Map<String, Object> target) {
        Map<String, Map<String, Object>> sourceDatasets = index(items(source, DATASETS), NAME, "dataset", true);
        Map<String, Map<String, Object>> datasets = index(items(target, SEMANTIC_DATA_OBJECTS), API_NAME,
                "exported dataset", false);
        Map<String, Map<String, Object>> relationships = index(items(target, SEMANTIC_RELATIONSHIPS), API_NAME,
                "exported relationship", false);
        for (var entry : relationships.entrySet()) {
            String scope = "Relationship '" + entry.getKey() + "'";
            Map<String, Object> relation = entry.getValue();
            String left = text(relation, LEFT_SEMANTIC_DEFINITION_API_NAME, scope);
            String right = text(relation, RIGHT_SEMANTIC_DEFINITION_API_NAME, scope);
            Map<String, Object> leftDataset = require(datasets, left, scope + " has unknown endpoint '" + left + "'");
            Map<String, Object> rightDataset = require(datasets, right, scope + " has unknown endpoint '" + right + "'");
            List<Map<String, Object>> criteria = items(relation, CRITERIA);
            if (criteria.isEmpty()) fail(scope + " requires nonempty criteria");
            for (Map<String, Object> criterion : criteria) {
                validateCriterion(scope, criterion, LEFT_FIELD_TYPE, LEFT_SEMANTIC_FIELD_API_NAME, left, leftDataset);
                validateCriterion(scope, criterion, RIGHT_FIELD_TYPE, RIGHT_SEMANTIC_FIELD_API_NAME, right, rightDataset);
            }
            validateRelationshipOptions(relation);
        }
        for (Map<String, Object> relation : items(source, RELATIONSHIPS)) {
            String name = text(relation, NAME, "Ossie relationship");
            Map<String, Object> exported = require(relationships, name,
                    "Declared relationship '" + name + "' was not exported");
            String scope = "Relationship '" + name + "'";
            if (!text(relation, FROM, scope).equals(exported.get(LEFT_SEMANTIC_DEFINITION_API_NAME))
                    || !text(relation, TO, scope).equals(exported.get(RIGHT_SEMANTIC_DEFINITION_API_NAME))) {
                fail(scope + " changed endpoints during conversion");
            }
            List<String> from = names(relation.get(FROM_COLUMNS), scope + " from_columns");
            List<String> to = names(relation.get(TO_COLUMNS), scope + " to_columns");
            List<Map<String, Object>> criteria = items(exported, CRITERIA);
            if (from.size() != to.size() || criteria.size() != from.size()) fail(scope + " changed composite key arity");
            for (int i = 0; i < from.size(); i++) {
                if (!from.get(i).equals(criteria.get(i).get(LEFT_SEMANTIC_FIELD_API_NAME))
                        || !to.get(i).equals(criteria.get(i).get(RIGHT_SEMANTIC_FIELD_API_NAME))) {
                    fail(scope + " changed join key correspondence during conversion");
                }
            }
            // Native round-trip metadata can explicitly reverse or relax core cardinality.
            // Check keys on the side(s) that the final native edge actually declares unique.
            String cardinality = (String) exported.get(CARDINALITY);
            if ("ManyToOne".equals(cardinality) || "OneToOne".equals(cardinality)) {
                validateUniqueSide(scope, "to_columns", to, sourceDatasets.get(relation.get(TO)));
            }
            if ("OneToMany".equals(cardinality) || "OneToOne".equals(cardinality)) {
                validateUniqueSide(scope, "from_columns", from, sourceDatasets.get(relation.get(FROM)));
            }
        }
    }

    static void validateRelationshipOptions(Map<String, Object> relation) {
        String scope = "Relationship '" + relation.get(API_NAME) + "'";
        if (relation.containsKey(CARDINALITY)) {
            String cardinality = text(relation, CARDINALITY, scope);
            if (!CARDINALITIES.contains(cardinality)) fail(scope + " has invalid cardinality '" + cardinality + "'");
        }
        if (relation.containsKey(JOIN_TYPE)) {
            String joinType = text(relation, JOIN_TYPE, scope);
            if (!JOIN_TYPES.contains(joinType)) fail(scope + " has invalid joinType '" + joinType + "'");
        }
        if (relation.containsKey(IS_ENABLED) && !(relation.get(IS_ENABLED) instanceof Boolean)) {
            fail(scope + " requires a Boolean isEnabled when supplied");
        }
    }

    private static void validateUniqueSide(String scope, String side, List<String> columns, Map<String, Object> dataset) {
        if (dataset == null) fail(scope + " references an undeclared dataset");
        String name = (String) dataset.get(NAME);
        List<Set<String>> keys = validateKeys(dataset, sourceFields(dataset), name);
        if (!keys.isEmpty() && keys.stream().noneMatch(columns::containsAll)) {
            fail(scope + " " + side + " do not include a declared primary_key or unique_key of '" + name + "'");
        }
    }

    private static void validateCriterion(String scope, Map<String, Object> criterion, String typeKey,
            String fieldKey, String dataset, Map<String, Object> target) {
        Object type = criterion.get(typeKey);
        if (type != null && !FIELD_TYPE_TABLE_FIELD.equals(type)) {
            fail(scope + " uses unsupported calculated join key type '" + type + "'; only direct table fields are supported");
        }
        String field = text(criterion, fieldKey, scope + " criterion");
        require(directFields(target), field, scope + " references missing direct field '" + dataset + "." + field + "'");
    }

    private static void checkColumns(String scope, String dataset, List<String> columns,
            Map<String, Object> source, Map<String, Object> target) {
        Map<String, Map<String, Object>> declared = sourceFields(source);
        Map<String, Map<String, Object>> exported = directFields(target);
        for (String column : columns) {
            require(declared, column, scope + " references undeclared field '" + dataset + "." + column + "'");
            require(exported, column, scope + " field '" + dataset + "." + column
                    + "' was not exported as a direct field; calculated join keys are unsupported");
        }
    }

    private static List<Set<String>> validateKeys(Map<String, Object> dataset,
            Map<String, Map<String, Object>> fields, String name) {
        List<Set<String>> keys = new ArrayList<>();
        if (dataset.containsKey("primary_key")) {
            keys.add(new LinkedHashSet<>(names(dataset.get("primary_key"), "Dataset '" + name + "' primary_key")));
        }
        Object unique = dataset.get("unique_keys");
        if (unique != null) {
            if (!(unique instanceof List<?>)) fail("Dataset '" + name + "' unique_keys must be an array");
            for (Object key : (List<?>) unique) {
                keys.add(new LinkedHashSet<>(names(key, "Dataset '" + name + "' unique_key")));
            }
        }
        for (Set<String> key : keys) {
            for (String field : key) require(fields, field, "Dataset '" + name + "' key references unknown field '" + field + "'");
        }
        // The native schema has no primary/unique-key constraint. primaryNameField is
        // a display identifier, and keyQualifierName is not a uniqueness declaration.
        return keys;
    }

    private static void validateCalculations(Map<String, Map<String, Object>> calculations,
            Map<String, Map<String, Map<String, Object>>> fields, Map<String, Object> target,
            Set<String> compiledCalculations) {
        Map<String, Set<String>> dependencies = new LinkedHashMap<>();
        Map<String, Set<String>> datasets = new LinkedHashMap<>();
        for (var entry : calculations.entrySet()) {
            String scope = "Calculated field '" + entry.getKey() + "'";
            if (!"Tua".equals(entry.getValue().get("syntax"))) fail(scope + " requires syntax 'Tua'");
            String expression = text(entry.getValue(), EXPRESSION, scope);
            Set<String> refs = new LinkedHashSet<>();
            Set<String> sources = new LinkedHashSet<>();
            for (List<String> reference : references(expression, scope)) {
                if (reference.size() == 2) {
                    String dataset = reference.get(0);
                    Map<String, Map<String, Object>> table = require(fields, dataset,
                            scope + " references unknown dataset '" + dataset + "'");
                    require(table, reference.get(1), scope + " references missing field '"
                            + dataset + "." + reference.get(1) + "'");
                    sources.add(dataset);
                } else {
                    String name = reference.get(0);
                    require(calculations, name, scope + " references unknown calculated field '" + name + "'");
                    refs.add(name);
                }
            }
            for (Map<String, Object> dependency : items(entry.getValue(), DEPENDENCIES)) {
                String definition = text(dependency, DEPENDENT_DEFINITION_API_NAME, scope + " dependency");
                Object field = dependency.get("dependentFieldApiName");
                if (field != null) {
                    Map<String, Map<String, Object>> table = require(fields, definition,
                            scope + " dependency references unknown dataset '" + definition + "'");
                    String name = text(dependency, "dependentFieldApiName", scope + " dependency");
                    require(table, name, scope + " dependency references missing field '" + definition + "." + name + "'");
                } else if (!fields.containsKey(definition) && !calculations.containsKey(definition)) {
                    fail(scope + " dependency references unknown definition '" + definition + "'");
                }
            }
            dependencies.put(entry.getKey(), refs);
            datasets.put(entry.getKey(), sources);
        }
        List<String> order = collectDatasets(dependencies, datasets);
        validateNativeCalculations(order, calculations, fields, datasets, target, compiledCalculations);
        Map<String, Set<String>> graph = new LinkedHashMap<>();
        fields.keySet().forEach(name -> graph.put(name, new LinkedHashSet<>()));
        for (Map<String, Object> relation : items(target, SEMANTIC_RELATIONSHIPS)) {
            // Optional native metadata may be absent. Only explicitly enabled edges
            // establish connectivity; absence is not proof of the target's default.
            if (!Boolean.TRUE.equals(relation.get(IS_ENABLED))) continue;
            String left = (String) relation.get(LEFT_SEMANTIC_DEFINITION_API_NAME);
            String right = (String) relation.get(RIGHT_SEMANTIC_DEFINITION_API_NAME);
            graph.get(left).add(right);
            graph.get(right).add(left);
        }
        for (var entry : datasets.entrySet()) {
            if (entry.getValue().size() < 2) continue;
            Set<String> reached = new HashSet<>();
            List<String> pending = new ArrayList<>(List.of(entry.getValue().iterator().next()));
            for (int i = 0; i < pending.size(); i++) {
                String dataset = pending.get(i);
                if (reached.add(dataset)) pending.addAll(graph.get(dataset));
            }
            if (!reached.containsAll(entry.getValue())) {
                fail("Calculated field '" + entry.getKey() + "' references datasets disconnected by enabled relationships");
            }
        }
    }

    private static List<String> collectDatasets(Map<String, Set<String>> dependencies, Map<String, Set<String>> datasets) {
        Map<String, Integer> remaining = new LinkedHashMap<>();
        Map<String, List<String>> consumers = new HashMap<>();
        ArrayDeque<String> ready = new ArrayDeque<>();
        for (var entry : dependencies.entrySet()) {
            remaining.put(entry.getKey(), entry.getValue().size());
            if (entry.getValue().isEmpty()) ready.add(entry.getKey());
            for (String dependency : entry.getValue()) {
                consumers.computeIfAbsent(dependency, ignored -> new ArrayList<>()).add(entry.getKey());
            }
        }
        int processed = 0;
        List<String> order = new ArrayList<>();
        while (!ready.isEmpty()) {
            String name = ready.removeFirst();
            order.add(name);
            processed++;
            for (String consumer : consumers.getOrDefault(name, List.of())) {
                datasets.get(consumer).addAll(datasets.get(name));
                if (remaining.compute(consumer, (ignored, count) -> count - 1) == 0) ready.add(consumer);
            }
        }
        if (processed != dependencies.size()) {
            String cyclic = remaining.entrySet().stream().filter(entry -> entry.getValue() > 0)
                    .map(Map.Entry::getKey).findFirst().orElseThrow();
            fail("Cyclic calculated field reference involving '" + cyclic + "'");
        }
        return order;
    }

    private static void validateNativeCalculations(List<String> order,
            Map<String, Map<String, Object>> calculations,
            Map<String, Map<String, Map<String, Object>>> fields, Map<String, Set<String>> datasets,
            Map<String, Object> target, Set<String> alreadyCompiled) {
        Set<String> measurements = index(items(target, SEMANTIC_CALCULATED_MEASUREMENTS), API_NAME,
                "calculated measurement", false).keySet();
        Map<String, ExpressionCompiler.Compiled> verified = new HashMap<>();
        for (String name : order) {
            if (alreadyCompiled.contains(name)) continue;
            Map<String, Object> calculation = calculations.get(name);
            String scope = "Native calculated field '" + name + "'";
            try {
                ExpressionCompiler.Compiled compiled = ExpressionCompiler.compile(
                        ExpressionCompiler.parse(text(calculation, EXPRESSION, scope), DIALECT_TABLEAU), reference -> {
                            List<MetricFieldResolver.Identifier> parts = reference.parts();
                            if (parts.size() == 2) {
                                String dataset = parts.get(0).text();
                                String field = parts.get(1).text();
                                Map<String, Map<String, Object>> table = require(fields, dataset,
                                        scope + " references unknown dataset '" + dataset + "'");
                                Map<String, Object> item = require(table, field, scope + " references missing field '" + field + "'");
                                return new ExpressionCompiler.Binding("[" + dataset + "].[" + field + "]",
                                        nativeDatatype(item, scope + " reference '" + dataset + "." + field + "'"),
                                        dataset, ExpressionCompiler.Level.ROW);
                            }
                            if (parts.size() != 1) throw new IllegalArgumentException("reference must be dataset.field or calculated field");
                            String dependency = parts.get(0).text();
                            Map<String, Object> item = require(calculations, dependency,
                                    scope + " references unknown calculated field '" + dependency + "'");
                            ExpressionCompiler.Compiled checked = verified.get(dependency);
                            String datatype = checked == null ? nativeDatatype(item, scope + " reference '" + dependency + "'")
                                    : checked.datatype();
                            ExpressionCompiler.Level level = checked == null
                                    ? measurements.contains(dependency) ? ExpressionCompiler.Level.AGGREGATE : ExpressionCompiler.Level.ROW
                                    : checked.level();
                            return new ExpressionCompiler.Binding("[" + dependency + "]", datatype, datasets.get(dependency), level);
                        });
                String type = calculation.get(DATA_TYPE) instanceof String value ? value : null;
                String datatype = compiled.datatype() == null ? SalesforceDataTypeMapper.toOssie(type) : compiled.datatype();
                if (SalesforceDataTypeMapper.toSalesforce(datatype) == null) {
                    throw new IllegalArgumentException("expression needs a supported native dataType to determine its result type");
                }
                boolean measurement = measurements.contains(name);
                if (measurement && !Set.of("Integer", "Decimal", "Float").contains(datatype)) {
                    throw new IllegalArgumentException("calculated measurements must return a number");
                }
                String expectedLevel = calculation.containsKey("level") ? text(calculation, "level", scope)
                        : measurement ? "AggregateFunction" : "Row";
                if (!Set.of("Row", "AggregateFunction").contains(expectedLevel)) {
                    throw new IllegalArgumentException("unsupported native level '" + expectedLevel + "'");
                }
                if (compiled.level() == ExpressionCompiler.Level.AGGREGATE && expectedLevel.equals("Row")
                        || compiled.level() == ExpressionCompiler.Level.ROW && expectedLevel.equals("AggregateFunction")) {
                    throw new IllegalArgumentException("expression aggregation conflicts with native level '" + expectedLevel + "'");
                }
                if (type != null && !SalesforceDataTypeMapper.areCompatible(datatype, type)) {
                    throw new IllegalArgumentException("expression datatype '" + datatype
                            + "' conflicts with native dataType '" + type + "'");
                }
                verified.put(name, new ExpressionCompiler.Compiled(compiled.expression(), datatype,
                        compiled.level(), compiled.datasets()));
            } catch (IllegalArgumentException e) {
                throw new ConversionException(scope + ": " + e.getMessage(), e);
            }
        }
    }

    private static String nativeDatatype(Map<String, Object> item, String scope) {
        String type = SalesforceDataTypeMapper.toOssie(item.get(DATA_TYPE) instanceof String value ? value : null);
        if (SalesforceDataTypeMapper.toSalesforce(type) == null) fail(scope + " needs a supported dataType");
        return type;
    }

    // A reference scanner, not a second formula parser. The compiler owns grammar and
    // type validation; this checks final names after native extensions have been restored.
    private static List<List<String>> references(String expression, String scope) {
        List<List<String>> references = new ArrayList<>();
        for (int i = 0; i < expression.length();) {
            char c = expression.charAt(i);
            if (c == '\'' || c == '"') {
                char quote = c;
                i++;
                boolean closed = false;
                while (i < expression.length()) {
                    char next = expression.charAt(i++);
                    if (next == '\\' && i < expression.length()) { i++; continue; }
                    if (next == quote) {
                        if (i < expression.length() && expression.charAt(i) == quote) { i++; continue; }
                        closed = true;
                        break;
                    }
                }
                if (!closed) fail(scope + " has an unterminated string literal");
            } else if (c == '[') {
                List<String> parts = new ArrayList<>();
                do {
                    int end = expression.indexOf(']', i + 1);
                    if (end < 0 || end == i + 1) fail(scope + " has an invalid bracket reference");
                    parts.add(expression.substring(i + 1, end));
                    i = end + 1;
                    while (i < expression.length() && Character.isWhitespace(expression.charAt(i))) i++;
                    if (i >= expression.length() || expression.charAt(i) != '.') break;
                    i++;
                    while (i < expression.length() && Character.isWhitespace(expression.charAt(i))) i++;
                    if (i >= expression.length() || expression.charAt(i) != '[') fail(scope + " has an invalid qualified reference");
                } while (true);
                if (parts.size() > 2) fail(scope + " references more than dataset.field");
                references.add(parts);
            } else {
                i++;
            }
        }
        return references;
    }

    private static Map<String, Map<String, Object>> calculationIndex(Map<String, Object> model) {
        List<Map<String, Object>> calculations = new ArrayList<>();
        for (String key : CALCULATED_FIELDS) calculations.addAll(items(model, key));
        return index(calculations, API_NAME, "model-level calculated field", false);
    }

    private static Map<String, Map<String, Object>> sourceFields(Map<String, Object> dataset) {
        return index(items(dataset, FIELDS), NAME, "field in dataset '" + dataset.get(NAME) + "'", true);
    }

    private static Map<String, Map<String, Object>> directFields(Map<String, Object> dataset) {
        List<Map<String, Object>> fields = new ArrayList<>();
        for (String key : DIRECT_FIELDS) fields.addAll(items(dataset, key));
        return index(fields, API_NAME, "field in exported dataset '" + dataset.get(API_NAME) + "'", false);
    }

    private static Map<String, Map<String, Object>> index(List<Map<String, Object>> values,
            String key, String kind, boolean normalize) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        Set<String> identities = new HashSet<>();
        for (Map<String, Object> value : values) {
            String name = text(value, key, kind);
            String identity = normalize ? normalize(name) : name;
            if (!identities.add(identity)) fail("Duplicate " + kind + " identity '" + name + "'");
            result.put(name, value);
        }
        return result;
    }

    private static String normalize(String name) {
        return name.length() >= 2 && name.startsWith("\"") && name.endsWith("\"")
                ? name.substring(1, name.length() - 1).replace("\"\"", "\"") : name.toUpperCase(Locale.ROOT);
    }

    private static List<String> names(Object value, String scope) {
        if (!(value instanceof List<?> values) || values.isEmpty()) fail(scope + " must be a nonempty array");
        List<String> result = new ArrayList<>();
        Set<String> unique = new HashSet<>();
        for (Object item : (List<?>) value) {
            if (!(item instanceof String name) || name.isBlank()) fail(scope + " contains a blank or non-string field name");
            String name = (String) item;
            if (!unique.add(name)) fail(scope + " contains duplicate field '" + name + "'");
            result.add(name);
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> items(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (value == null) return List.of();
        if (!(value instanceof List<?>)) fail("Property '" + key + "' must be an array");
        List<Map<String, Object>> result = new ArrayList<>();
        for (Object item : (List<?>) value) {
            if (!(item instanceof Map<?, ?>)) fail("Property '" + key + "' must contain objects");
            result.add((Map<String, Object>) item);
        }
        return result;
    }

    private static String text(Map<String, Object> object, String key, String scope) {
        Object value = object.get(key);
        if (!(value instanceof String text) || text.isBlank()) fail(scope + " requires a nonempty '" + key + "'");
        return (String) value;
    }

    private static <T> T require(Map<String, T> values, String key, String error) {
        T value = values.get(key);
        if (value == null) fail(error);
        return value;
    }

    private static void fail(String message) {
        throw new ConversionException(message);
    }
}
