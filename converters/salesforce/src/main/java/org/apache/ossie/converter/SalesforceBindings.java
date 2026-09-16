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

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.exception.InvalidInputException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Immutable environment bindings. Business expressions and semantic names cannot be overridden. */
public final class SalesforceBindings {
    private record DatasetBinding(String objectName, String objectType, Map<String, String> fields) {}
    private record ModelBinding(String dataspace, Map<String, DatasetBinding> datasets) {}
    private final Map<String, ModelBinding> models;

    private SalesforceBindings(Map<String, ModelBinding> models) { this.models = Map.copyOf(models); }

    public static SalesforceBindings none() { return new SalesforceBindings(Map.of()); }

    /** Loads JSON or YAML. Unknown properties and duplicate mapping keys are errors. */
    public static SalesforceBindings fromPath(Path path) {
        try { return fromString(Files.readString(path)); }
        catch (IOException e) { throw new InvalidInputException("Cannot read Salesforce bindings: " + path, e); }
    }

    public static SalesforceBindings fromString(String content) {
        try {
            YAMLFactory factory = new YAMLFactory();
            factory.enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
            Map<String, Object> root = new ObjectMapper(factory).enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                    .readValue(content, new TypeReference<>() {});
            if (root == null) throw new IllegalArgumentException("bindings document is empty");
            keys(root, Set.of("models"), "bindings");
            Map<String, Object> entries = object(root.get("models"), "bindings.models");
            Map<String, ModelBinding> result = new LinkedHashMap<>();
            for (var entry : entries.entrySet()) {
                String context = "Bindings for model '" + entry.getKey() + "'";
                Map<String, Object> model = object(entry.getValue(), context);
                keys(model, Set.of("dataspace", "datasets"), context);
                Map<String, DatasetBinding> datasets = new LinkedHashMap<>();
                for (var dataset : optionalObject(model, "datasets", context).entrySet()) {
                    String location = context + ", dataset '" + dataset.getKey() + "'";
                    Map<String, Object> definition = object(dataset.getValue(), location);
                    keys(definition, Set.of("dataObjectName", "dataObjectType", "fields"), location);
                    Map<String, String> fields = new LinkedHashMap<>();
                    for (var field : optionalObject(definition, "fields", location).entrySet()) {
                        fields.put(field.getKey(), string(field.getValue(), location + ", field '" + field.getKey() + "'"));
                    }
                    datasets.put(dataset.getKey(), new DatasetBinding(optionalString(definition, "dataObjectName", location),
                            optionalString(definition, "dataObjectType", location), Map.copyOf(fields)));
                }
                result.put(entry.getKey(), new ModelBinding(optionalString(model, "dataspace", context), Map.copyOf(datasets)));
            }
            return new SalesforceBindings(result);
        } catch (IOException | IllegalArgumentException e) {
            throw new InvalidInputException("Invalid Salesforce bindings: " + e.getMessage(), e);
        }
    }

    boolean isEmpty() { return models.isEmpty(); }

    void validateModels(Set<String> names) {
        for (String name : models.keySet()) {
            if (!names.contains(name)) throw new ConversionException("Bindings reference unknown model '" + name + "'");
        }
    }

    /** Apply only after expressions are bound in their original source scope. */
    void apply(Map<String, Object> source, Map<String, Object> target) {
        String modelName = getString(source, "name");
        ModelBinding model = models.get(modelName);
        if (model == null) return;
        if (model.dataspace != null) target.put("dataspace", model.dataspace);
        Map<String, List<Map<String, Object>>> sourceDatasets = index(items(source, "datasets"), "name");
        Map<String, List<Map<String, Object>>> targetDatasets = index(items(target, "semanticDataObjects"), "apiName");
        for (var entry : model.datasets.entrySet()) {
            String datasetName = entry.getKey();
            String context = "Bindings for model '" + modelName + "', dataset '" + datasetName + "'";
            Map<String, Object> sourceDataset = unique(sourceDatasets, datasetName, context);
            Map<String, Object> targetDataset = unique(targetDatasets, datasetName, context);
            DatasetBinding binding = entry.getValue();
            if (binding.objectName != null) targetDataset.put("dataObjectName", binding.objectName);
            if (binding.objectType != null) targetDataset.put("dataObjectType", binding.objectType);
            Map<String, List<Map<String, Object>>> sourceFields = index(items(sourceDataset, "fields"), "name");
            List<Map<String, Object>> directFields = new java.util.ArrayList<>(items(targetDataset, "semanticDimensions"));
            directFields.addAll(items(targetDataset, "semanticMeasurements"));
            Map<String, List<Map<String, Object>>> targetFields = index(directFields, "apiName");
            for (var field : binding.fields.entrySet()) {
                unique(sourceFields, field.getKey(), context + ", field '" + field.getKey() + "'");
                Map<String, Object> targetField = unique(targetFields, field.getKey(),
                        context + ", field '" + field.getKey() + "' (only direct physical fields can be rebound)");
                targetField.put("dataObjectFieldName", field.getValue());
            }
        }
    }

    private static Map<String, List<Map<String, Object>>> index(List<Map<String, Object>> objects, String key) {
        Map<String, List<Map<String, Object>>> result = new LinkedHashMap<>();
        for (Map<String, Object> object : objects) {
            result.computeIfAbsent(getString(object, key), ignored -> new java.util.ArrayList<>()).add(object);
        }
        return result;
    }

    private static Map<String, Object> unique(Map<String, List<Map<String, Object>>> objects, String name, String context) {
        List<Map<String, Object>> matches = objects.getOrDefault(name, List.of());
        if (matches.size() != 1) throw new ConversionException(context + ": expected one declared/exported identity, found " + matches.size());
        return matches.get(0);
    }

    private static void keys(Map<String, Object> value, Set<String> allowed, String context) {
        for (String key : value.keySet()) {
            if (!allowed.contains(key)) throw new IllegalArgumentException(context + ": unknown property '" + key + "'");
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String context) {
        if (!(value instanceof Map<?, ?> map)) throw new IllegalArgumentException(context + " must be an object");
        if (map.keySet().stream().anyMatch(key -> !(key instanceof String) || ((String) key).isBlank())) {
            throw new IllegalArgumentException(context + " requires nonempty string keys");
        }
        return (Map<String, Object>) map;
    }

    private static Map<String, Object> optionalObject(Map<String, Object> value, String key, String context) {
        return value.containsKey(key) ? object(value.get(key), context + "." + key) : Map.of();
    }

    private static String string(Object value, String context) {
        if (!(value instanceof String text) || text.isBlank() || text.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException(context + " must be a nonempty string without control characters");
        }
        return text;
    }

    private static String optionalString(Map<String, Object> value, String key, String context) {
        return value.containsKey(key) ? string(value.get(key), context + "." + key) : null;
    }

    private static List<Map<String, Object>> items(Map<String, Object> value, String key) {
        List<Object> list = getList(value, key);
        return list == null ? List.of() : streamMaps(list).toList();
    }
}
