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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.apache.ossie.converter.MetricFieldResolver.Identifier;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class MetricFieldResolverTest {

    @Test
    void acceptsDatasetsConnectedByAnExportedRelationshipInEitherDirection() {
        MetricFieldResolver resolver = graphResolver(List.of(
                relationship("Orders", "Customers")), "Orders", "Customers");
        resolver.validateDatasets(Set.of("Orders", "Customers"));
        resolver.validateDatasets(Set.of("Orders"));
        resolver.validateDatasets(Set.of());
    }

    @Test
    void acceptsFactDatasetsConnectedThroughAnIntermediateSharedDimension() {
        MetricFieldResolver resolver = graphResolver(List.of(
                relationship("Orders", "Customers"), relationship("Returns", "Customers")),
                "Orders", "Returns", "Customers");
        resolver.validateDatasets(Set.of("Orders", "Returns"));
    }

    @Test
    void rejectsDisconnectedDatasetsAndRelationshipsThroughMissingTargets() {
        MetricFieldResolver resolver = graphResolver(List.of(
                relationship("Orders", "MissingCustomers"), relationship("Returns", "MissingCustomers")),
                "Orders", "Returns");
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> resolver.validateDatasets(Set.of("Orders", "Returns")));
        assertTrue(error.getMessage().contains("disconnected datasets Orders, Returns"), error.getMessage());
        assertTrue(error.getMessage().contains("declare supported relationships"), error.getMessage());
    }

    @Test
    void bindsQualifiedAndUniqueUnqualifiedSqlNamesToExportedApiNames() {
        MetricFieldResolver resolver = resolver("Orders", "revenue", "Decimal", "Currency");
        assertEquals("[Orders].[revenue]", resolver.resolve(sql("oRders", "REVENUE"), false).expression());
        assertEquals("[Orders].[revenue]", resolver.resolve(sql("revenue"), false).expression());
        assertEquals("Orders", resolver.resolve(sql("revenue"), false).dataset());
        assertEquals("Decimal", resolver.resolve(sql("revenue"), false).datatype());
    }

    @Test
    void normalizesQuotedSqlIdentifiersAsSpecified() {
        MetricFieldResolver resolver = resolver("orders", "revenue", "Decimal", "Number");
        assertEquals("[orders].[revenue]", resolver.resolve(
                List.of(new Identifier("ORDERS", true), new Identifier("REVENUE", true)), false).expression());
        assertError(resolver, List.of(new Identifier("orders", true), new Identifier("revenue", true)),
                false, "Unknown dataset");
    }

    @Test
    void resolvesQuotedDeclarationsWithoutRenamingExportedObjects() {
        MetricFieldResolver resolver = resolver("\"Order Items\"", "\"Unit \"\"Price\"\"\"", "Decimal", "Number");
        assertEquals("[\"Order Items\"].[\"Unit \"\"Price\"\"\"]", resolver.resolve(
                List.of(new Identifier("Order Items", true), new Identifier("Unit \"Price\"", true)), false)
                .expression());
        assertError(resolver, sql("ORDER ITEMS", "UNIT PRICE"), false, "Unknown dataset");
    }

    @Test
    void tableauReferencesUseExactApiNamesAndRequireDataset() {
        MetricFieldResolver resolver = resolver("Orders", "revenue", "Integer", "Number");
        assertEquals("[Orders].[revenue]", resolver.resolve(sql("Orders", "revenue"), true).expression());
        assertEquals("[Orders].[revenue]", resolver.resolve(sql("orders", "revenue"), false).expression());
        assertError(resolver, sql("orders", "revenue"), true, "Unknown dataset");
        assertError(resolver, sql("Orders", "Revenue"), true, "Unknown field");
        assertError(resolver, sql("revenue"), true, "must use [dataset].[field]");
    }

    @Test
    void rejectsPhysicalSourceNamesAndUndeclaredFields() {
        MetricFieldResolver resolver = resolver("Orders", "revenue", "Decimal", "Number");
        assertError(resolver, sql("Orders__dll", "revenue"), false, "Unknown dataset");
        assertError(resolver, sql("Orders", "revenue__c"), false, "Unknown field");
        assertError(resolver, sql("warehouse", "Orders", "revenue"), false, "physical source paths");
        assertError(resolver, List.of(), false, "must name a declared field");
    }

    @Test
    void rejectsAmbiguousUnqualifiedFieldsAcrossDatasets() {
        Map<String, Object> orders = dataset("Orders", field("amount", "Decimal"));
        Map<String, Object> returns = dataset("Returns", field("amount", "Decimal"));
        MetricFieldResolver resolver = new MetricFieldResolver(
                Map.of("datasets", List.of(orders, returns)),
                Map.of("semanticDataObjects", List.of(targetDataset("Orders", targetField("amount", "Number")),
                        targetDataset("Returns", targetField("amount", "Number")))));
        assertError(resolver, sql("amount"), false, "Ambiguous field");
        assertEquals("[Orders].[amount]", resolver.resolve(sql("Orders", "amount"), false).expression());
    }

    @Test
    void rejectsDuplicateDatasetDeclarationsEvenWhenOnlyOneHasTheField() {
        MetricFieldResolver resolver = new MetricFieldResolver(Map.of("datasets", List.of(
                dataset("Orders", field("amount", "Decimal")), dataset("ORDERS", field("other", "Decimal")))),
                Map.of());
        assertError(resolver, sql("Orders", "amount"), false, "Ambiguous dataset");
        assertError(resolver, sql("amount"), false, "Ambiguous dataset");
    }

    @Test
    void rejectsDuplicateFieldDeclarations() {
        MetricFieldResolver resolver = new MetricFieldResolver(Map.of("datasets", List.of(
                dataset("Orders", field("amount", "Decimal"), field("AMOUNT", "Decimal")))), Map.of());
        assertError(resolver, sql("Orders", "amount"), false, "Ambiguous field");
    }

    @Test
    void checksThatDeclaredFieldsWereActuallyExportedAsDirectFields() {
        Map<String, Object> source = Map.of("datasets", List.of(dataset("Orders", field("amount", "Decimal"))));
        MetricFieldResolver missingDataset = new MetricFieldResolver(source, Map.of());
        assertError(missingDataset, sql("Orders", "amount"), false, "was not exported");

        MetricFieldResolver calculatedField = new MetricFieldResolver(source, Map.of(
                "semanticDataObjects", List.of(targetDataset("Orders")),
                "semanticCalculatedDimensions", List.of(Map.of("apiName", "amount", "expression", "1 + 2"))));
        assertError(calculatedField, sql("Orders", "amount"), false, "calculated or omitted fields");
    }

    @ParameterizedTest
    @ValueSource(strings = {"profit+tax", "profit-tax", "profit=tax", "1", "TRUE", "(profit)",
            "Orders.profit", "\"profit\"", "[profit]", "SUM(profit)", "profit;tax", ""})
    void rejectsExpressionsMisclassifiedAsPhysicalFields(String binding) {
        Map<String, Object> target = new LinkedHashMap<>(targetField("revenue", "Number"));
        if (binding.isEmpty()) target.remove("dataObjectFieldName");
        else target.put("dataObjectFieldName", binding);
        MetricFieldResolver resolver = new MetricFieldResolver(
                Map.of("datasets", List.of(dataset("Orders", field("revenue", "Decimal")))),
                Map.of("semanticDataObjects", List.of(targetDataset("Orders", target))));
        assertError(resolver, sql("Orders", "revenue"), false, "revenue");
    }

    @Test
    void rejectsDuplicateExportedObjectsAndFieldsAcrossKinds() {
        Map<String, Object> source = Map.of("datasets", List.of(dataset("Orders", field("amount", "Decimal"))));
        Map<String, Object> target = targetDataset("Orders", targetField("amount", "Number"));
        MetricFieldResolver duplicateDatasets = new MetricFieldResolver(source,
                Map.of("semanticDataObjects", List.of(target, target)));
        assertError(duplicateDatasets, sql("amount"), false, "Ambiguous exported Salesforce dataset");

        Map<String, Object> duplicateFields = Map.of("apiName", "Orders",
                "semanticDimensions", List.of(targetField("amount", "Number")),
                "semanticMeasurements", List.of(targetField("amount", "Number")));
        MetricFieldResolver resolver = new MetricFieldResolver(source,
                Map.of("semanticDataObjects", List.of(duplicateFields)));
        assertError(resolver, sql("amount"), false, "Ambiguous exported Salesforce field");
    }

    @Test
    void infersMissingPortableTypeOnlyFromKnownExportedType() {
        MetricFieldResolver resolver = new MetricFieldResolver(
                Map.of("datasets", List.of(dataset("Orders", Map.of("name", "amount")))),
                Map.of("semanticDataObjects", List.of(targetDataset("Orders", targetField("amount", "Currency")))));
        assertEquals("Decimal", resolver.resolve(sql("amount"), false).datatype());
    }

    @Test
    void rejectsUnknownOrConflictingTypes() {
        assertError(resolver("Orders", "amount", "String", "Number"), sql("amount"), false,
                "use compatible field types");
        assertError(resolver("Orders", "amount", "Opaque", "Geo"), sql("amount"), false,
                "no supported datatype");
        MetricFieldResolver resolver = new MetricFieldResolver(
                Map.of("datasets", List.of(dataset("Orders", Map.of("name", "amount")))),
                Map.of("semanticDataObjects", List.of(targetDataset("Orders", targetField("amount", "Geo")))));
        assertError(resolver, sql("amount"), false, "no supported datatype");
    }

    @ParameterizedTest
    @ValueSource(strings = {"bad[field", "bad]field", "bad\nfield", "bad\tfield", "bad\u0000field"})
    void rejectsUnrepresentableExportedNames(String name) {
        MetricFieldResolver resolver = resolver("Orders", name, "Decimal", "Number");
        assertError(resolver, sql("Orders", name), true, "cannot be represented safely");
    }

    private static MetricFieldResolver resolver(String dataset, String field, String sourceType, String targetType) {
        return new MetricFieldResolver(Map.of("datasets", List.of(dataset(dataset, field(field, sourceType)))),
                Map.of("semanticDataObjects", List.of(targetDataset(dataset, targetField(field, targetType)))));
    }

    @Test
    void disabledRelationshipsDoNotConnectDatasets() {
        Map<String, Object> disabled = new LinkedHashMap<>(relationship("Orders", "Returns"));
        disabled.put("isEnabled", false);
        MetricFieldResolver resolver = graphResolver(List.of(disabled), "Orders", "Returns");
        assertThrows(IllegalArgumentException.class,
                () -> resolver.validateDatasets(java.util.Set.of("Orders", "Returns")));
    }

    @ParameterizedTest
    @ValueSource(strings = {"changed endpoint", "swapped keys", "empty criteria", "missing criterion",
            "missing join field", "formula join", "missing enabled", "missing declaration", "duplicate declaration"})
    void unverifiedRelationshipsCannotEstablishConnectivity(String defect) {
        Map<String, Object> valid = relationship("Orders", "Customers");
        Map<String, Object> changed = new LinkedHashMap<>(valid);
        List<Map<String, Object>> declarations = List.of(sourceRelationship(valid));
        switch (defect) {
            case "changed endpoint" -> changed.put("rightSemanticDefinitionApiName", "Returns");
            case "swapped keys" -> changed.put("criteria", List.of(criterion("id", "tenant"), criterion("tenant", "id")));
            case "empty criteria" -> changed.put("criteria", List.of());
            case "missing criterion" -> changed.put("criteria", List.of(criterion("id", "id")));
            case "missing join field" -> {
                changed.put("criteria", List.of(criterion("missing", "id"), criterion("tenant", "tenant")));
                Map<String, Object> source = new LinkedHashMap<>(sourceRelationship(valid));
                source.put("from_columns", List.of("missing", "tenant"));
                declarations = List.of(source);
            }
            case "formula join" -> changed.put("criteria", List.of(
                    Map.of("leftSemanticFieldApiName", "id", "rightSemanticFieldApiName", "id", "leftFieldType", "Formula"),
                    criterion("tenant", "tenant")));
            case "missing enabled" -> changed.remove("isEnabled");
            case "missing declaration" -> declarations = List.of();
            case "duplicate declaration" -> declarations = List.of(sourceRelationship(valid), sourceRelationship(valid));
            default -> throw new AssertionError(defect);
        }
        MetricFieldResolver resolver = graphResolver(declarations, List.of(changed), "Orders", "Customers", "Returns");
        Set<String> referenced = Set.of("Orders", defect.equals("changed endpoint") ? "Returns" : "Customers");
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> resolver.validateDatasets(referenced), defect);
        assertTrue(error.getMessage().contains("disconnected"), error.getMessage());
    }

    @Test
    void computedPhysicalJoinKeyCannotEstablishConnectivity() {
        Map<String, Object> edge = relationship("Orders", "Customers");
        Map<String, Object> computed = new LinkedHashMap<>(targetField("id", "Number"));
        computed.put("dataObjectFieldName", "profit+tax");
        MetricFieldResolver resolver = new MetricFieldResolver(Map.of(
                "datasets", List.of(dataset("Orders", field("id", "Integer"), field("tenant", "Integer")),
                        dataset("Customers", field("id", "Integer"), field("tenant", "Integer"))),
                "relationships", List.of(sourceRelationship(edge))), Map.of(
                "semanticDataObjects", List.of(targetDataset("Orders", computed, targetField("tenant", "Number")),
                        targetDataset("Customers", targetField("id", "Number"), targetField("tenant", "Number"))),
                "semanticRelationships", List.of(edge)));
        assertThrows(IllegalArgumentException.class,
                () -> resolver.validateDatasets(Set.of("Orders", "Customers")));
    }

    private static MetricFieldResolver graphResolver(List<Map<String, Object>> relationships, String... datasets) {
        return graphResolver(relationships.stream().map(MetricFieldResolverTest::sourceRelationship).toList(),
                relationships, datasets);
    }

    private static MetricFieldResolver graphResolver(List<Map<String, Object>> declarations,
            List<Map<String, Object>> relationships, String... datasets) {
        return new MetricFieldResolver(Map.of(
                "datasets", java.util.Arrays.stream(datasets)
                        .map(name -> dataset(name, field("id", "Integer"), field("tenant", "Integer"))).toList(),
                "relationships", declarations), Map.of(
                "semanticDataObjects", java.util.Arrays.stream(datasets)
                        .map(name -> targetDataset(name, targetField("id", "Number"), targetField("tenant", "Number"))).toList(),
                "semanticRelationships", relationships));
    }

    private static Map<String, Object> relationship(String left, String right) {
        return Map.of("apiName", left + "_to_" + right, "leftSemanticDefinitionApiName", left,
                "rightSemanticDefinitionApiName", right, "isEnabled", true,
                "criteria", List.of(criterion("id", "id"), criterion("tenant", "tenant")));
    }

    private static Map<String, Object> criterion(String left, String right) {
        return Map.of("leftSemanticFieldApiName", left, "rightSemanticFieldApiName", right);
    }

    private static Map<String, Object> sourceRelationship(Map<String, Object> target) {
        return Map.of("name", target.get("apiName"), "from", target.get("leftSemanticDefinitionApiName"),
                "to", target.get("rightSemanticDefinitionApiName"),
                "from_columns", List.of("id", "tenant"), "to_columns", List.of("id", "tenant"));
    }

    private static List<Identifier> sql(String... parts) {
        return java.util.Arrays.stream(parts).map(part -> new Identifier(part, false)).toList();
    }

    @SafeVarargs
    private static Map<String, Object> dataset(String name, Map<String, Object>... fields) {
        return Map.of("name", name, "source", name + "__dll", "fields", List.of(fields));
    }

    private static Map<String, Object> field(String name, String datatype) {
        return Map.of("name", name, "datatype", datatype, "expression", Map.of("dialects", List.of(
                Map.of("dialect", "ANSI_SQL", "expression", "physical_column__c"))));
    }

    @SafeVarargs
    private static Map<String, Object> targetDataset(String name, Map<String, Object>... fields) {
        return Map.of("apiName", name, "semanticMeasurements", List.of(fields));
    }

    private static Map<String, Object> targetField(String name, String datatype) {
        return Map.of("apiName", name, "dataType", datatype, "dataObjectFieldName", "physical_column__c");
    }

    private static void assertError(MetricFieldResolver resolver, List<Identifier> parts,
            boolean tableau, String expected) {
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> resolver.resolve(parts, tableau));
        assertTrue(error.getMessage().contains(expected), error.getMessage());
    }
}
