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

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.exception.InvalidInputException;
import org.apache.ossie.exception.ValidationException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class SalesforceBindingsTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final ObjectMapper YAML = new ObjectMapper(new YAMLFactory());
    private static final String MODEL = "Customer_Orders_Model";
    @TempDir Path directory;

    @Test
    void acceptsEmptyCatalogAndLoadsJsonOrYamlFromAFile() throws Exception {
        assertTrue(SalesforceBindings.none().isEmpty());
        assertTrue(SalesforceBindings.fromString("models: {}").isEmpty());
        Path file = directory.resolve("bindings.json");
        Files.writeString(file, "{\"models\":{\"sales\":{\"dataspace\":\"production\"}}}");
        SalesforceBindings bindings = SalesforceBindings.fromPath(file);
        Map<String, Object> target = new LinkedHashMap<>();
        bindings.apply(Map.of("name", "sales"), target);
        assertEquals("production", target.get("dataspace"));
        assertThrows(InvalidInputException.class, () -> SalesforceBindings.fromPath(directory.resolve("missing.yaml")));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "models: {}\nmodels: {}",
            "models: {sales: {}, sales: {}}",
            "models: {sales: {dataspace: a, dataspace: b}}",
            "models: {sales: {datasets: {orders: {}, orders: {}}}}",
            "models: {sales: {datasets: {orders: {fields: {amount: a, amount: b}}}}}"
    })
    void rejectsDuplicateKeysAtEveryBindingsLevel(String input) {
        InvalidInputException error = assertThrows(InvalidInputException.class, () -> SalesforceBindings.fromString(input));
        assertTrue(error.getMessage().contains("Duplicate field"), error.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "models: {}\nunknown: true",
            "models: {sales: {metrics: {}}}",
            "models: {sales: {expression: 'SUM(amount)'}}",
            "models: {sales: {datasets: {orders: {apiName: changed}}}}",
            "models: {sales: {datasets: {orders: {expression: 'amount + 1'}}}}",
            "models: {sales: {datasets: {orders: {syntax: Tua}}}}"
    })
    void rejectsUnknownPropertiesIncludingFormulaAndSemanticNameOverrides(String input) {
        InvalidInputException error = assertThrows(InvalidInputException.class, () -> SalesforceBindings.fromString(input));
        assertTrue(error.getMessage().contains("unknown property"), error.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "models: {}\n---\nmodels: {sales: {dataspace: hidden}}",
            "models: {}\n---\nnull",
            "{\"models\":{}}\n{\"models\":{}}"
    })
    void rejectsTrailingYamlDocumentsAndJsonValues(String input) {
        assertThrows(InvalidInputException.class, () -> SalesforceBindings.fromString(input));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "", "null", "[]", "{}", "models: null", "models: []", "models: {sales: null}",
            "models: {'': {}}", "models: {sales: {datasets: null}}",
            "models: {sales: {dataspace: 5}}", "models: {sales: {dataspace: ''}}",
            "models: {sales: {datasets: {orders: {fields: {amount: {expression: 'amount + 1'}}}}}}",
            "models: {sales: {datasets: {orders: {fields: {amount: null}}}}}",
            "models: {sales: {datasets: {orders: {fields: {amount: 17}}}}}",
            "models: {sales: {datasets: {orders: {dataObjectName: '   '}}}}",
            "{\"models\":{\"sales\":{\"dataspace\":\"bad\\nname\"}}}"
    })
    void rejectsInvalidShapesAndNonStringOrBlankBindingValues(String input) {
        assertThrows(InvalidInputException.class, () -> SalesforceBindings.fromString(input));
    }

    @Test
    void absentModelBindingLeavesTargetUntouchedAndNamesMatchExactly() {
        SalesforceBindings bindings = SalesforceBindings.fromString("models: {sales: {dataspace: production}}");
        Map<String, Object> target = new LinkedHashMap<>(Map.of("dataspace", "original"));
        bindings.apply(Map.of("name", "Sales"), target);
        assertEquals(Map.of("dataspace", "original"), target);
        assertThrows(ConversionException.class, () -> bindings.validateModels(java.util.Set.of("Sales")));
    }

    @Test
    void appliesPhysicalBindingsWithoutMutatingCanonicalModelOrFormulaMetadata() throws Exception {
        String fixture = fixture();
        Map<String, Object> original = YAML.readValue(fixture, new TypeReference<>() {});
        Map<String, Object> baseline = output(SalesforceBindings.none(), fixture);
        SalesforceBindings bindings = SalesforceBindings.fromString("""
                models:
                  Customer_Orders_Model:
                    dataspace: production
                    datasets:
                      Orders:
                        dataObjectName: OrdersProduction__dll
                        dataObjectType: Dlo
                        fields:
                          amount: NetRevenue__c
                          order_id: OrderIdentifier__c
                """);
        Map<String, Object> bound = output(bindings, fixture);
        assertEquals("production", bound.get("dataspace"));
        Map<String, Object> orders = find(items(bound, "semanticDataObjects"), "Orders");
        assertEquals("OrdersProduction__dll", orders.get("dataObjectName"));
        assertEquals("NetRevenue__c", find(items(orders, "semanticMeasurements"), "amount").get("dataObjectFieldName"));
        assertEquals("OrderIdentifier__c", find(items(orders, "semanticDimensions"), "order_id").get("dataObjectFieldName"));
        assertEquals(baseline.get("semanticCalculatedMeasurements"), bound.get("semanticCalculatedMeasurements"));
        assertEquals(baseline.get("semanticCalculatedDimensions"), bound.get("semanticCalculatedDimensions"));
        assertEquals(baseline.get("semanticRelationships"), bound.get("semanticRelationships"));
        assertEquals(original, YAML.readValue(fixture, new TypeReference<Map<String, Object>>() {}));
        // Exercise apply directly against the original map, in addition to the string API.
        @SuppressWarnings("unchecked") Map<String, Object> model = (Map<String, Object>) ((List<?>) original.get("semantic_model")).get(0);
        String before = JSON.writeValueAsString(original);
        bindings.apply(model, baseline);
        assertEquals(before, JSON.writeValueAsString(original));
        assertEquals(bound, baseline);
        assertEquals(bound, output(bindings, fixture), "bindings must be reusable without conversion state");
    }

    @Test
    void rejectsUnknownModelDatasetAndFieldIdentitiesThroughPublicConverter() throws Exception {
        String fixture = fixture();
        assertConversionError("unknown model 'missing'", "models: {missing: {dataspace: prod}}", fixture);
        assertConversionError("dataset 'Missing'", "models: {" + MODEL + ": {datasets: {Missing: {dataObjectName: x}}}}", fixture);
        assertConversionError("field 'missing'", "models: {" + MODEL + ": {datasets: {Orders: {fields: {missing: x}}}}}", fixture);
        assertConversionError("dataset 'orders'", "models: {" + MODEL + ": {datasets: {orders: {dataObjectName: x}}}}", fixture);
    }

    @Test
    void rejectsCalculatedFieldPhysicalMappingsThroughPublicConverter() throws Exception {
        assertConversionError("only direct physical fields can be rebound",
                "models: {" + MODEL + ": {datasets: {Orders: {fields: {order_year: CalendarYear__c}}}}}", fixture());
    }

    @Test
    void nativeSchemaRejectsUnsupportedDataObjectTypeAfterApplyingBindings() throws Exception {
        SalesforceBindings bindings = SalesforceBindings.fromString(
                "models: {" + MODEL + ": {datasets: {Orders: {dataObjectType: NotANativeObjectType}}}}");
        String fixture = fixture();
        ValidationException error = assertThrows(ValidationException.class, () -> output(bindings, fixture));
        assertTrue(error.getMessage().contains("dataObjectType"), error.getMessage());
    }

    @Test
    void oneBindingCatalogCanTargetMultipleModelsWithoutCrossModelLeakage() throws Exception {
        Map<String, Object> document = YAML.readValue(fixture(), new TypeReference<>() {});
        @SuppressWarnings("unchecked") Map<String, Object> first = (Map<String, Object>) ((List<?>) document.get("semantic_model")).get(0);
        Map<String, Object> second = JSON.convertValue(first, new TypeReference<>() {});
        second.put("name", "Other_Model");
        document.put("semantic_model", List.of(first, second));
        SalesforceBindings bindings = SalesforceBindings.fromString("""
                models:
                  Customer_Orders_Model: {dataspace: primary}
                  Other_Model: {dataspace: secondary}
                """);
        List<String> result = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE, bindings)
                .convert(YAML.writeValueAsString(document));
        assertEquals("primary", JSON.readTree(result.get(0)).get("dataspace").asText());
        assertEquals("secondary", JSON.readTree(result.get(1)).get("dataspace").asText());
    }

    private static void assertConversionError(String message, String bindings, String fixture) {
        ConversionException error = assertThrows(ConversionException.class,
                () -> output(SalesforceBindings.fromString(bindings), fixture));
        assertTrue(error.getMessage().contains(message), error.getMessage());
    }

    private static Map<String, Object> output(SalesforceBindings bindings, String fixture) throws Exception {
        String json = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE, bindings).convert(fixture).get(0);
        return JSON.readValue(json, new TypeReference<>() {});
    }

    private static String fixture() throws Exception {
        return Files.readString(Path.of("src/test/resources/examples/ossieToSalesforce.yaml"));
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> items(Map<String, Object> parent, String key) {
        return (List<Map<String, Object>>) parent.getOrDefault(key, List.of());
    }

    private static Map<String, Object> find(List<Map<String, Object>> items, String name) {
        return items.stream().filter(item -> name.equals(item.get("apiName"))).findFirst().orElseThrow();
    }
}
