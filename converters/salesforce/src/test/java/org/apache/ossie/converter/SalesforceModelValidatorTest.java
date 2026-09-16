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

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.ossie.exception.ConversionException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class SalesforceModelValidatorTest {
    private final SalesforceModelValidator validator = new SalesforceModelValidator();

    @Test
    void acceptsCompleteDirectModelWithoutMutatingEitherSide() {
        Map<String, Object> source = source();
        Map<String, Object> target = target();
        String originalSource = source.toString();
        String originalTarget = target.toString();
        assertDoesNotThrow(() -> validator.validate(source, target));
        assertEquals(originalSource, source.toString());
        assertEquals(originalTarget, target.toString());
    }

    @ParameterizedTest
    @ValueSource(strings = {"semanticDataObjects", "semanticCalculatedMeasurements"})
    void rejectsMissingExportedEntities(String property) {
        Map<String, Object> target = target();
        target.remove(property);
        assertTrue(error(source(), target).contains("was not exported"));
    }

    @Test
    void rejectsMissingFieldAndRequiredPhysicalBinding() {
        Map<String, Object> target = target();
        object(target).put("semanticMeasurements", List.of());
        assertTrue(error(source(), target).contains("orders.amount"));
        target = target();
        field(target).remove("dataObjectFieldName");
        assertTrue(error(source(), target).contains("dataObjectFieldName"));
    }

    @Test
    void rejectsEquivalentSourceIdentifiersAndDuplicateTargetFields() {
        Map<String, Object> source = source();
        sourceObject(source).put("fields", List.of(Map.of("name", "amount"), Map.of("name", "AMOUNT")));
        assertTrue(error(source, target()).contains("Duplicate"));
        Map<String, Object> target = target();
        object(target).put("semanticDimensions", List.of(new LinkedHashMap<>(field(target))));
        assertTrue(error(source(), target).contains("Duplicate"));
    }

    @Test
    void rejectsModelLevelCalculatedNameCollisionAcrossDimensionsAndMetrics() {
        Map<String, Object> target = target();
        target.put("semanticCalculatedDimensions", List.of(calculation("total", "1")));
        assertTrue(error(source(), target).contains("Duplicate model-level calculated field"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUM([missing].[amount])", "SUM([orders].[missing])", "[missing]"})
    void rejectsDanglingFinalFormulaReferences(String expression) {
        Map<String, Object> target = target();
        calculation(target).put("expression", expression);
        String error = error(source(), target);
        assertTrue(error.contains("references"), error);
        assertTrue(error.contains("missing"), error);
    }

    @ParameterizedTest
    @ValueSource(strings = {"IF '[missing].[field]' = '[missing].[field]' THEN SUM([orders].[amount]) ELSE 0 END",
            "IF \"[missing]\" = \"[missing]\" THEN SUM([orders].[amount]) ELSE 0 END",
            "IF 'it''s [missing]' = 'it''s [missing]' THEN SUM([orders].[amount]) ELSE 0 END"})
    void bracketTextInsideStringsDoesNotBecomeAReference(String expression) {
        Map<String, Object> target = target();
        calculation(target).put("expression", expression);
        assertDoesNotThrow(() -> validator.validate(source(), target));
    }

    @ParameterizedTest
    @ValueSource(strings = {"TABLEAU", "SQL", ""})
    void requiresTuaOnFinalCalculatedFields(String syntax) {
        Map<String, Object> target = target();
        calculation(target).put("syntax", syntax);
        assertTrue(error(source(), target).contains("requires syntax 'Tua'"));
    }

    @Test
    void detectsCyclesInNativeCalculatedReferences() {
        Map<String, Object> target = target();
        calculation(target).put("expression", "[other]");
        target.put("semanticCalculatedDimensions", List.of(calculation("other", "[total]")));
        assertTrue(error(source(), target).contains("Cyclic"));
    }

    @Test
    void rejectsGenericHandlerResidueInsteadOfTreatingItAsNativeRelationship() {
        Map<String, Object> target = target();
        target.put("semanticRelationships", List.of(Map.of("name", "raw", "from", "orders", "to", "orders",
                "from_columns", List.of("amount"), "to_columns", List.of("amount"))));
        assertTrue(error(source(), target).contains("apiName"));
    }

    @Test
    void finalMetricConnectivityUsesOnlyEnabledRelationships() {
        Map<String, Object> source = source();
        Map<String, Object> target = target();
        datasets(source).add(new LinkedHashMap<>(Map.of("name", "returns", "fields", List.of(Map.of("name", "amount")))));
        objects(target).add(new LinkedHashMap<>(Map.of("apiName", "returns", "dataObjectName", "returns__dll",
                "semanticMeasurements", List.of(Map.of("apiName", "amount", "dataObjectFieldName", "amount__c")))));
        calculation(target).put("expression", "SUM([orders].[amount]) - SUM([returns].[amount])");
        Map<String, Object> relationship = new LinkedHashMap<>(Map.of("apiName", "join", "leftSemanticDefinitionApiName", "orders",
                "rightSemanticDefinitionApiName", "returns", "criteria", List.of(Map.of("leftSemanticFieldApiName", "amount",
                "rightSemanticFieldApiName", "amount")), "cardinality", "ManyToMany", "joinType", "Auto", "isEnabled", false));
        target.put("semanticRelationships", List.of(relationship));
        assertTrue(error(source, target).contains("disconnected by enabled relationships"));
        relationship.remove("isEnabled");
        assertTrue(error(source, target).contains("disconnected by enabled relationships"));
        relationship.put("isEnabled", true);
        assertDoesNotThrow(() -> validator.validate(source, target));
    }

    @Test
    void acceptsOptionalNativeRelationshipMetadataWithoutAssumingAnEnabledEdge() {
        Map<String, Object> source = source();
        source.remove("metrics");
        Map<String, Object> target = target();
        target.remove("semanticCalculatedMeasurements");
        target.put("semanticRelationships", List.of(Map.of("apiName", "native", "leftSemanticDefinitionApiName", "orders",
                "rightSemanticDefinitionApiName", "orders", "criteria", List.of(Map.of("leftSemanticFieldApiName", "amount",
                "rightSemanticFieldApiName", "amount")))));
        assertDoesNotThrow(() -> validator.validate(source, target));
    }

    @Test
    void declaredPrimaryAndUniqueKeysAreCheckedWithoutInventingNativeSchemaFields() {
        Map<String, Object> source = source();
        sourceObject(source).put("primary_key", List.of("amount"));
        sourceObject(source).put("unique_keys", List.of(List.of("amount")));
        Map<String, Object> target = target();
        assertDoesNotThrow(() -> validator.validate(source, target));
        assertFalse(object(target).containsKey("primaryNameField"));
        sourceObject(source).put("primary_key", List.of("missing"));
        assertTrue(error(source, target).contains("key references unknown field"));
    }

    @Test
    void detectsChangedCompositeCorrespondenceAfterLaterExtensionRestoration() {
        Map<String, Object> source = source();
        source.put("relationships", List.of(Map.of("name", "join", "from", "orders", "to", "orders",
                "from_columns", List.of("amount"), "to_columns", List.of("amount"))));
        Map<String, Object> target = target();
        object(target).put("semanticDimensions", List.of(Map.of("apiName", "other", "dataObjectFieldName", "other__c")));
        target.put("semanticRelationships", List.of(Map.of("apiName", "join", "leftSemanticDefinitionApiName", "orders",
                "rightSemanticDefinitionApiName", "orders", "criteria", List.of(Map.of("leftSemanticFieldApiName", "other",
                "rightSemanticFieldApiName", "amount")), "cardinality", "ManyToOne", "joinType", "Auto", "isEnabled", true)));
        assertTrue(error(source, target).contains("changed join key correspondence"));
    }

    @Test
    void checksDerivedFieldCoverageUsingTheExistingPlan() {
        Map<String, Object> source = source();
        sourceObject(source).put("source", "orders__dll");
        sourceObject(source).put("fields", List.of(
                Map.of("name", "amount", "datatype", "Decimal", "expression", Map.of("dialects",
                        List.of(Map.of("dialect", "SNOWFLAKE", "expression", "amount__c")))),
                Map.of("name", "derived", "datatype", "Decimal", "expression", Map.of("dialects",
                        List.of(Map.of("dialect", "SNOWFLAKE", "expression", "amount + 1"))))));
        Map<String, Object> target = target();
        FieldExpressionPlan plan = new FieldExpressionPlan(source, target);
        assertTrue(assertThrows(ConversionException.class, () -> validator.validate(source, target, plan))
                .getMessage().contains("orders.derived"));
        target.put("semanticCalculatedDimensions", List.of(calculation(plan.calculatedApiName("orders", "derived"),
                "[orders].[amount] + 1")));
        assertDoesNotThrow(() -> validator.validate(source, target, plan));
    }

    @Test
    void validatesNativeDependencyMetadataAfterExtensionRestoration() {
        Map<String, Object> target = target();
        calculation(target).put("dependencies", List.of(Map.of("dependentDefinitionApiName", "orders",
                "dependentFieldApiName", "missing")));
        assertTrue(error(source(), target).contains("dependency references missing field"));
        calculation(target).put("dependencies", List.of(Map.of("dependentDefinitionApiName", "orders",
                "dependentFieldApiName", "amount")));
        assertDoesNotThrow(() -> validator.validate(source(), target));
    }

    @Test
    void compilesNativeOnlyCalculationsInsteadOfTrustingMetadata() {
        Map<String, Object> target = target();
        target.put("semanticCalculatedDimensions", List.of(calculation("external", "BOGUS(1)")));
        String failure = error(source(), target);
        assertTrue(failure.contains("Native calculated field 'external'"), failure);
        assertTrue(failure.contains("BOGUS"), failure);
        Map<String, Object> external = calculation("external", "1");
        external.put("dataType", "Text");
        target.put("semanticCalculatedDimensions", List.of(external));
        assertTrue(error(source(), target).contains("conflicts with native dataType"));
    }

    @Test
    void nativeOnlyMixedAggregationCannotBypassTheSharedAnalyzer() {
        Map<String, Object> target = target();
        field(target).put("dataType", "Number");
        target.put("semanticCalculatedMeasurements", List.of(calculation(target),
                calculation("external", "SUM([orders].[amount]) + [orders].[amount]")));
        String failure = error(source(), target);
        assertTrue(failure.contains("Native calculated field 'external'"), failure);
        assertTrue(failure.contains("aggregate") || failure.contains("aggregat"), failure);
    }

    @Test
    void explicitlyMarkedNativeRowMeasurementsRemainSupported() {
        Map<String, Object> target = target();
        field(target).put("dataType", "Number");
        Map<String, Object> external = calculation("external", "[orders].[amount] * 2");
        target.put("semanticCalculatedMeasurements", List.of(calculation(target), external));
        assertTrue(error(source(), target).contains("expression aggregation conflicts"));
        external.put("level", "Row");
        assertDoesNotThrow(() -> validator.validate(source(), target));
    }

    @Test
    void explicitlyMarkedNativeAggregateDimensionKeepsItsSupportedLevel() {
        Map<String, Object> target = target();
        field(target).put("dataType", "Number");
        Map<String, Object> external = calculation("external", "SUM([orders].[amount]) > 0");
        external.put("dataType", "Boolean");
        target.put("semanticCalculatedDimensions", List.of(external));
        assertTrue(error(source(), target).contains("expression aggregation conflicts"));
        external.put("level", "AggregateFunction");
        assertDoesNotThrow(() -> validator.validate(source(), target));
    }

    @Test
    void nativeOnlyCalculationDependenciesUseFinalTypesInTopologicalOrder() {
        Map<String, Object> target = target();
        field(target).put("dataType", "Number");
        target.put("semanticCalculatedDimensions", List.of(calculation("row", "[orders].[amount] + 1")));
        target.put("semanticCalculatedMeasurements", List.of(calculation(target), calculation("external", "SUM([row])")));
        assertDoesNotThrow(() -> validator.validate(source(), target));
    }

    @Test
    void nullOnlyNativeFormulaRequiresADeclaredTypeWithoutCrashing() {
        Map<String, Object> target = target();
        Map<String, Object> external = calculation("external", "NULL");
        target.put("semanticCalculatedDimensions", List.of(external));
        assertTrue(error(source(), target).contains("dataType"));
        external.put("dataType", "Text");
        assertDoesNotThrow(() -> validator.validate(source(), target));
    }

    @Test
    void nativeArraysCannotDisappearBehindAlreadyPresentGeneratedArrays() {
        Map<String, Object> source = source();
        source.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"semanticCalculatedMeasurements\":[{\"apiName\":\"external\",\"expression\":\"1\",\"syntax\":\"Tua\"}]}")));
        assertTrue(error(source, target()).contains("Native extension semanticCalculatedMeasurements entity 'external' was not exported"));
        Map<String, Object> target = target();
        target.put("semanticCalculatedMeasurements", List.of(calculation(target), calculation("external", "1")));
        assertDoesNotThrow(() -> validator.validate(source, target));
        // Matching core identities remain authoritative even when their native snapshot is stale.
        source.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"semanticCalculatedMeasurements\":[{\"apiName\":\"total\",\"expression\":\"STALE()\"}]}")));
        assertDoesNotThrow(() -> validator.validate(source, target()));
    }

    @Test
    void publicConverterRejectsUncompiledFunctionRestoredAtModelLevel() throws Exception {
        String input = conversionInput("semanticCalculatedDimensions",
                Map.of("apiName", "native_extra", "expression", "BOGUS(1)", "syntax", "Tua"));
        ConversionException error = assertThrows(ConversionException.class,
                () -> ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE).convert(input));
        assertTrue(error.getMessage().contains("native_extra"), error.getMessage());
        assertTrue(error.getMessage().contains("BOGUS"), error.getMessage());
    }

    @Test
    void publicConverterRejectsNativeArrayLossAlongsideCoreMetrics() throws Exception {
        String input = conversionInput("semanticCalculatedMeasurements",
                Map.of("apiName", "native_extra", "expression", "1", "syntax", "Tua"));
        ConversionException error = assertThrows(ConversionException.class,
                () -> ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE).convert(input));
        assertTrue(error.getMessage().contains("native_extra"), error.getMessage());
        assertTrue(error.getMessage().contains("was not exported"), error.getMessage());
    }

    private static String conversionInput(String nativeArray, Map<String, Object> nativeCalculation) throws Exception {
        com.fasterxml.jackson.databind.ObjectMapper json = new com.fasterxml.jackson.databind.ObjectMapper();
        Map<String, Object> source = source();
        sourceObject(source).put("source", "orders__dll");
        sourceObject(source).put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"dataObjectType\":\"Dlo\"}")));
        sourceObject(source).put("fields", List.of(Map.of("name", "amount", "datatype", "Decimal", "expression",
                Map.of("dialects", List.of(Map.of("dialect", "SNOWFLAKE", "expression", "amount__c"))))));
        source.put("metrics", List.of(Map.of("name", "total", "datatype", "Decimal", "expression",
                Map.of("dialects", List.of(Map.of("dialect", "SNOWFLAKE", "expression", "SUM(orders.amount)"))))));
        source.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                json.writeValueAsString(Map.of("dataspace", "default", nativeArray, List.of(nativeCalculation))))));
        return json.writeValueAsString(Map.of("version", "0.2.0.dev0", "semantic_model", List.of(source)));
    }

    private String error(Map<String, Object> source, Map<String, Object> target) {
        return assertThrows(ConversionException.class, () -> validator.validate(source, target)).getMessage();
    }

    private static Map<String, Object> source() {
        Map<String, Object> source = new LinkedHashMap<>();
        source.put("name", "sales");
        source.put("datasets", new ArrayList<>(List.of(new LinkedHashMap<>(Map.of("name", "orders", "fields", List.of(Map.of("name", "amount")))))));
        source.put("metrics", List.of(Map.of("name", "total")));
        return source;
    }

    private static Map<String, Object> target() {
        Map<String, Object> target = new LinkedHashMap<>();
        target.put("apiName", "sales");
        target.put("semanticDataObjects", new ArrayList<>(List.of(new LinkedHashMap<>(Map.of("apiName", "orders", "dataObjectName", "orders__dll",
                "semanticMeasurements", List.of(new LinkedHashMap<>(Map.of("apiName", "amount", "dataObjectFieldName", "amount__c"))))))));
        target.put("semanticCalculatedMeasurements", List.of(calculation("total", "SUM([orders].[amount])")));
        return target;
    }

    private static Map<String, Object> calculation(String name, String expression) {
        return new LinkedHashMap<>(Map.of("apiName", name, "expression", expression, "syntax", "Tua"));
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> datasets(Map<String, Object> source) { return (List<Map<String, Object>>) source.get("datasets"); }
    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> objects(Map<String, Object> target) { return (List<Map<String, Object>>) target.get("semanticDataObjects"); }
    private static Map<String, Object> sourceObject(Map<String, Object> source) { return datasets(source).get(0); }
    private static Map<String, Object> object(Map<String, Object> target) { return objects(target).get(0); }
    @SuppressWarnings("unchecked")
    private static Map<String, Object> field(Map<String, Object> target) { return ((List<Map<String, Object>>) object(target).get("semanticMeasurements")).get(0); }
    @SuppressWarnings("unchecked")
    private static Map<String, Object> calculation(Map<String, Object> target) { return ((List<Map<String, Object>>) target.get("semanticCalculatedMeasurements")).get(0); }
}
