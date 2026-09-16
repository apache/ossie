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

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class FieldExpressionPlanTest {
    @ParameterizedTest
    @ValueSource(strings = {"amount__c+profit__c", "amount__c + profit__c"})
    void parsesDerivedFieldsIndependentOfWhitespaceAndBindsPhysicalColumns(String expression) {
        ConversionContext context = export(dataset("Orders", "warehouse.sales.orders",
                field("amount", "Decimal", "amount__c"), field("profit", "Decimal", "profit__c"),
                field("derived", "Decimal", expression)));
        assertEquals(2, direct(context, "Orders").size());
        Map<String, Object> calc = calculated(context).get(0);
        assertEquals("Orders__derived", calc.get("apiName"));
        assertEquals("Tua", calc.get("syntax"));
        assertEquals("Number", calc.get("dataType"));
        assertTrue(calc.get("expression").toString().contains("[Orders].[amount]"));
        assertTrue(calc.get("expression").toString().contains("[Orders].[profit]"));
        assertFalse(calc.get("expression").toString().contains("__c"));
        assertEquals(2, ((List<?>) calc.get("dependencies")).size());
        assertFalse(context.fieldPlan().isDirect("Orders", "derived"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"cost+tax", "unit(price)", "gross/net", "Order Date", "has\"quote", "SUM", "schema.column"})
    void preservesQuotedPhysicalNamesInsteadOfClassifyingTheirCharacters(String physical) {
        ConversionContext context = export(dataset("Orders", "orders",
                field("value", "Decimal", "\"" + physical.replace("\"", "\"\"") + "\"")));
        assertEquals(physical, direct(context, "Orders").get(0).get("dataObjectFieldName"));
        assertTrue(calculated(context).isEmpty());
    }

    @Test
    void extractsOnlyPhysicalColumnFromVerifiedSourceQualifier() {
        ConversionContext context = export(dataset("Orders", "warehouse.sales.orders",
                field("amount", "Decimal", "sales.orders.amount__c")));
        assertEquals("amount__c", direct(context, "Orders").get(0).get("dataObjectFieldName"));
        assertFailure("qualifier", dataset("Orders", "warehouse.sales.orders",
                field("amount", "Decimal", "another_table.amount__c")));
    }

    @Test
    void expandsForwardReferencesAndCachesResolvedRowBindings() {
        ConversionContext context = export(dataset("Orders", "orders",
                field("gross", "Decimal", "net + tax"),
                field("net", "Decimal", "amount - discount"),
                field("amount", "Decimal", "amount__c"),
                field("discount", "Decimal", "discount__c"),
                field("tax", "Decimal", "tax__c")));
        ExpressionCompiler.Binding binding = context.fieldPlan().resolve("Orders", "gross");
        assertSame(binding, context.fieldPlan().resolve("Orders", "gross"));
        assertTrue(binding.expression().contains("[Orders].[amount]"));
        assertTrue(binding.expression().contains("[Orders].[discount]"));
        assertTrue(binding.expression().contains("[Orders].[tax]"));
        assertFalse(binding.expression().contains("[net]"));
        assertEquals(ExpressionCompiler.Level.ROW, binding.level());
        assertEquals(3, context.fieldPlan().dependencies("Orders", "gross").size());
    }

    @Test
    void rejectsCyclesWithWholeDependencyPath() {
        assertFailure("Orders.a -> Orders.b -> Orders.a", dataset("Orders", "orders",
                field("a", "Decimal", "b + 1"), field("b", "Decimal", "a + 1")));
        assertFailure("dependency cycle", dataset("Orders", "orders", field("a", "Decimal", "a + 1")));
    }

    @Test
    void rejectsUnknownOrAmbiguousPhysicalAndSemanticReferences() {
        assertFailure("Unknown row field reference", dataset("Orders", "orders",
                field("derived", "Decimal", "undeclared + 1")));
        assertFailure("Ambiguous row field reference", dataset("Orders", "orders",
                field("one", "Decimal", "amount__c"), field("two", "Decimal", "amount__c"),
                field("derived", "Decimal", "amount__c + 1")));
        assertFailure("Ambiguous row field reference", dataset("Orders", "orders",
                field("one", "Decimal", "amount"), field("amount", "Decimal", "second__c"),
                field("derived", "Decimal", "amount + 1")));
    }

    @Test
    void respectsQuotedPhysicalIdentifierCase() {
        ConversionContext context = export(dataset("Orders", "orders",
                field("amount", "Decimal", "\"mixedCase\""),
                field("derived", "Decimal", "\"mixedCase\" + 1")));
        assertTrue(context.fieldPlan().resolve("Orders", "derived").expression().contains("[Orders].[amount]"));
        assertFailure("Unknown row field reference", dataset("Orders", "orders",
                field("amount", "Decimal", "\"mixedCase\""),
                field("derived", "Decimal", "mixedCase + 1")));
    }

    @Test
    void rejectsUnsupportedUnusedExpressionsRatherThanOmittingThem() {
        assertFailure("Field 'Orders.derived'", dataset("Orders", "orders",
                field("amount", "Decimal", "amount__c"),
                field("derived", "Decimal", "UNKNOWN_FUNCTION(amount)")));
    }

    @Test
    void rejectsAggregateFieldsAndIncompatibleDeclaredResultTypes() {
        assertFailure("row expressions", dataset("Orders", "orders",
                field("amount", "Decimal", "amount__c"), field("derived", "Decimal", "SUM(amount)")));
        assertFailure("conflicts", dataset("Orders", "orders",
                field("amount", "Decimal", "amount__c"), field("derived", "String", "amount + 1")));
    }

    @Test
    void infersStringBooleanAndNumericRowResultTypes() {
        ConversionContext context = export(dataset("Orders", "orders",
                field("amount", "Decimal", "amount__c"), field("name", "String", "name__c"),
                field("positive", null, "amount > 0"),
                field("category", null, "CASE WHEN amount > 0 THEN 'positive' ELSE name END"), field("constant", null, "42")));
        assertEquals("Boolean", context.fieldPlan().resolve("Orders", "positive").datatype());
        assertEquals("String", context.fieldPlan().resolve("Orders", "category").datatype());
        assertEquals("Integer", context.fieldPlan().resolve("Orders", "constant").datatype());
    }

    @Test
    void treatsTableauDirectReferenceAsSemanticAliasNotPhysicalColumn() {
        Map<String, Object> alias = field("alias", "Decimal", "[Orders].[amount]");
        alias.put("expression", expression("TABLEAU", "[Orders].[amount]"));
        ConversionContext context = export(dataset("Orders", "orders",
                field("amount", "Decimal", "amount__c"), alias));
        assertEquals(1, direct(context, "Orders").size());
        assertEquals("[Orders].[amount]", context.fieldPlan().resolve("Orders", "alias").expression());
        assertEquals("Orders__alias", calculated(context).get(0).get("apiName"));
    }

    @Test
    void rejectsCrossDatasetRowFieldsEvenWhenTheOtherDatasetIsDeclared() {
        Map<String, Object> cross = field("cross", "Decimal", "[Returns].[amount] + 1");
        cross.put("expression", expression("TABLEAU", "[Returns].[amount] + 1"));
        assertFailure("cross-dataset", dataset("Orders", "orders", cross),
                dataset("Returns", "returns", field("amount", "Decimal", "amount__c")));
        assertFailure("qualifier", dataset("Orders", "orders", field("cross", "Decimal", "Returns.amount + 1")),
                dataset("Returns", "returns", field("amount", "Decimal", "amount__c")));
    }

    @Test
    void usesStableDistinctGlobalNamesAcrossDatasetsAndSanitizationCollisions() {
        Map<String, Object> a = dataset("a-b", "a", field("value", "Integer", "1 + 1"));
        Map<String, Object> b = dataset("a_b", "b", field("value", "Integer", "2 + 2"));
        ConversionContext first = export(a, b);
        ConversionContext reversed = export(b, a);
        String firstName = first.fieldPlan().calculatedApiName("a-b", "value");
        String secondName = first.fieldPlan().calculatedApiName("a_b", "value");
        assertNotEquals(firstName, secondName);
        assertTrue(firstName.matches("[A-Za-z_][A-Za-z0-9_]*"));
        assertEquals(firstName, reversed.fieldPlan().calculatedApiName("a-b", "value"));
        assertEquals(secondName, reversed.fieldPlan().calculatedApiName("a_b", "value"));
    }

    @Test
    void reservesMetricNamesBeforeAllocatingCalculatedDimensionNames() {
        Map<String, Object> dataset = dataset("Orders", "orders", field("constant", "Integer", "1 + 1"));
        Map<String, Object> source = new LinkedHashMap<>(Map.of("datasets", List.of(dataset),
                "metrics", List.of(Map.of("name", "Orders__constant"))));
        ConversionContext context = exportModel(source);
        assertNotEquals("Orders__constant", context.fieldPlan().calculatedApiName("Orders", "constant"));
    }

    @Test
    void rejectsDuplicateDeclarationsAndMissingTargetDataset() {
        assertFailure("Duplicate field", dataset("Orders", "orders",
                field("amount", "Decimal", "one__c"), field("amount", "Decimal", "two__c")));
        Map<String, Object> source = Map.of("datasets", List.of(dataset("Orders", "orders", field("a", "Integer", "a"))));
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> handler().execute(source, new LinkedHashMap<>(), Map.of()));
        assertTrue(error.getMessage().contains("was not exported"), error.getMessage());
    }

    @Test
    void reverseConversionQuotesPhysicalColumnsAndRoundTripsTheirExactName() {
        String physical = "gross/+ \"net\"";
        Map<String, Object> sfDataset = new LinkedHashMap<>(Map.of("apiName", "Orders",
                "semanticMeasurements", List.of(Map.of("apiName", "amount", "dataType", "Number", "dataObjectFieldName", physical))));
        Map<String, Object> source = new LinkedHashMap<>(Map.of("semanticDataObjects", List.of(sfDataset)));
        Map<String, Object> dataset = new LinkedHashMap<>(Map.of("name", "Orders", "source", "orders"));
        Map<String, Object> output = new LinkedHashMap<>(Map.of("datasets", List.of(dataset)));
        new FieldMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE,
                new CustomExtensionHandler(new ObjectMapper())).execute(source, output, Map.of());
        ConversionContext context = export(dataset);
        assertEquals(physical, direct(context, "Orders").get(0).get("dataObjectFieldName"));
    }

    @Test
    void preservesQuotedQualifiedPhysicalColumnsAndRejectsWrongQualifierCase() {
        String source = "\"Warehouse\".\"SaLes\".\"Order Items\"";
        ConversionContext context = export(dataset("Orders", source,
                field("amount", "Decimal", source + ".\"Net Value\""),
                field("double_amount", "Decimal", "\"SaLes\".\"Order Items\".\"Net Value\" * 2")));
        assertEquals("Net Value", direct(context, "Orders").get(0).get("dataObjectFieldName"));
        assertTrue(context.fieldPlan().resolve("Orders", "double_amount").expression().contains("[Orders].[amount]"));
        assertFailure("qualifier", dataset("Orders", source,
                field("amount", "Decimal", "\"WAREHOUSE\".\"SaLes\".\"Order Items\".\"Net Value\"")));
    }

    @Test
    void rejectsExcessiveExpansionAndDependencyDepthWithUsefulErrors() {
        List<Map<String, Object>> exponential = new ArrayList<>();
        exponential.add(field("f0", "Integer", "1 + 1"));
        for (int i = 1; i < 20; i++) {
            exponential.add(field("f" + i, "Integer", "f" + (i - 1) + " + f" + (i - 1)));
        }
        assertFailure("translated expression exceeds 131072 characters", new LinkedHashMap<>(Map.of(
                "name", "Orders", "source", "orders", "fields", exponential)));

        List<Map<String, Object>> deep = new ArrayList<>();
        for (int i = 0; i < 130; i++) deep.add(field("f" + i, "Integer", "f" + (i + 1) + " + 1"));
        deep.add(field("f130", "Integer", "1 + 1"));
        assertFailure("dependency depth exceeds 128", new LinkedHashMap<>(Map.of(
                "name", "Orders", "source", "orders", "fields", deep)));
    }

    @Test
    void physicalBindingOverlayLeavesSourceAndCompiledDependenciesUnchanged() throws Exception {
        Map<String, Object> source = new LinkedHashMap<>(Map.of("name", "Retail", "datasets", List.of(
                dataset("Orders", "warehouse.orders", field("amount", "Decimal", "amount__c"),
                        field("derived", "Decimal", "amount__c + 1")))));
        ObjectMapper mapper = new ObjectMapper();
        String original = mapper.writeValueAsString(source);
        ConversionContext context = exportModel(source);
        ExpressionCompiler.Binding binding = context.fieldPlan().resolve("Orders", "derived");
        List<Map<String, Object>> dependencies = context.fieldPlan().dependencies("Orders", "derived");
        SalesforceBindings.fromString("""
                models:
                  Retail:
                    dataspace: prod
                    datasets:
                      Orders:
                        dataObjectName: Orders__dlm
                        dataObjectType: DataModelObject
                        fields:
                          amount: NetRevenue__c
                """).apply(source, context.outputData());
        assertEquals(original, mapper.writeValueAsString(source));
        assertEquals("NetRevenue__c", direct(context, "Orders").get(0).get("dataObjectFieldName"));
        assertSame(binding, context.fieldPlan().resolve("Orders", "derived"));
        assertEquals(dependencies, context.fieldPlan().dependencies("Orders", "derived"));
        assertTrue(binding.expression().contains("[Orders].[amount]"));
        assertFalse(binding.expression().contains("NetRevenue__c"));
        assertEquals(List.of(Map.of("dependentDefinitionApiName", "Orders", "dependentFieldApiName", "amount")), dependencies);
    }

    @Test
    void calculatedRowMetadataOverridesStaleNativeFormulaProperties() throws Exception {
        Map<String, Object> derived = field("derived", "Decimal", "amount + 1");
        derived.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                new ObjectMapper().writeValueAsString(Map.of("level", "AggregateFunction", "syntax", "Salesforce",
                        "expression", "SUM(stale)", "apiName", "renamed", "label", "Adjusted amount", "dataType", "Currency",
                        "dependencies", List.of(Map.of("dependentDefinitionApiName", "Wrong", "dependentFieldApiName", "missing")))))));
        ConversionContext context = export(dataset("Orders", "orders", field("amount", "Decimal", "amount__c"), derived));
        Map<String, Object> calc = calculated(context).get(0);
        assertEquals("Row", calc.get("level"));
        assertEquals("Tua", calc.get("syntax"));
        assertEquals("Orders__derived", calc.get("apiName"));
        assertEquals("Adjusted amount", calc.get("label"));
        assertEquals("Currency", calc.get("dataType"));
        assertTrue(calc.get("expression").toString().contains("[Orders].[amount]"));
        assertFalse(calc.get("expression").toString().contains("SUM"));
        assertEquals(List.of(Map.of("dependentDefinitionApiName", "Orders", "dependentFieldApiName", "amount")), calc.get("dependencies"));
    }

    @SafeVarargs
    private static void assertFailure(String message, Map<String, Object>... datasets) {
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class, () -> export(datasets));
        assertTrue(error.getMessage().contains(message), error.getMessage());
    }

    @SafeVarargs
    private static ConversionContext export(Map<String, Object>... datasets) {
        return exportModel(new LinkedHashMap<>(Map.of("datasets", List.of(datasets))));
    }

    private static ConversionContext exportModel(Map<String, Object> source) {
        List<Object> targets = new ArrayList<>();
        for (Object item : (List<?>) source.get("datasets")) {
            Map<?, ?> dataset = (Map<?, ?>) item;
            targets.add(new LinkedHashMap<>(Map.of("apiName", dataset.get("name"))));
        }
        Map<String, Object> target = new LinkedHashMap<>(Map.of("semanticDataObjects", targets));
        ConversionContext context = new ConversionContext(source, target);
        handler().execute(context, Map.of());
        return context;
    }

    private static FieldMappingHandler handler() {
        return new FieldMappingHandler(ConversionDirection.OSSIE_TO_SALESFORCE,
                new CustomExtensionHandler(new ObjectMapper()));
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> calculated(ConversionContext context) {
        return (List<Map<String, Object>>) context.outputData().getOrDefault("semanticCalculatedDimensions", List.of());
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> direct(ConversionContext context, String dataset) {
        return ((List<Map<String, Object>>) context.outputData().get("semanticDataObjects")).stream()
                .filter(item -> dataset.equals(item.get("apiName"))).flatMap(item -> List.of("semanticDimensions", "semanticMeasurements")
                        .stream().flatMap(key -> ((List<Map<String, Object>>) item.getOrDefault(key, List.of())).stream())).toList();
    }

    @SafeVarargs
    private static Map<String, Object> dataset(String name, String source, Map<String, Object>... fields) {
        return new LinkedHashMap<>(Map.of("name", name, "source", source, "fields", List.of(fields)));
    }

    private static Map<String, Object> field(String name, String datatype, String text) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("name", name);
        if (datatype != null) field.put("datatype", datatype);
        field.put("expression", expression("ANSI_SQL", text));
        return field;
    }

    private static Map<String, Object> expression(String dialect, String text) {
        return Map.of("dialects", List.of(Map.of("dialect", dialect, "expression", text)));
    }
}
