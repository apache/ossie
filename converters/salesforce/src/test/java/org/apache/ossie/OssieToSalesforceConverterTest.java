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

package org.apache.ossie;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;
import org.apache.ossie.converter.CustomExtensionHandler;
import org.apache.ossie.converter.FieldMappingHandler;
import org.apache.ossie.converter.RelationshipMappingHandler;
import org.apache.ossie.exception.ValidationException;
import org.apache.ossie.validator.SchemaValidator;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.apache.ossie.converter.pipeline.DirectionConfig;
import org.apache.ossie.converter.pipeline.HandlerFactory;
import org.apache.ossie.converter.pipeline.PipelineConfig;
import org.apache.ossie.converter.pipeline.PipelineConfigLoader;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Comprehensive integration test for Ossie → Salesforce conversion.
 * Uses real example file: ossieToSalesforce.yaml
 */
class OssieToSalesforceConverterTest {

    private static boolean salesforceSchemaExists;
    private static boolean ossieSchemaExists;
    private static boolean warningPrinted = false;

    private Converter converter;
    private ObjectMapper jsonMapper;
    private String ossieYaml;
    private String ossieYamlAnsiSql;

    @BeforeAll
    static void checkSchemaAvailability() {
        salesforceSchemaExists = OssieToSalesforceConverterTest.class
                .getResourceAsStream(SchemaValidator.SALESFORCE_SCHEMA_PATH) != null;
        ossieSchemaExists = OssieToSalesforceConverterTest.class
                .getResourceAsStream(SchemaValidator.OSSIE_SCHEMA_PATH) != null;

        if (!warningPrinted) {
            if (!salesforceSchemaExists) {
                System.err.println("\n  WARNING: Salesforce schema not found at " + SchemaValidator.SALESFORCE_SCHEMA_PATH);
                System.err.println("  Some tests in OssieToSalesforceConverterTest will be skipped.");
                System.err.println("  To run all tests, download the schema from:");
                System.err.println("  https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html");
                System.err.println("  and save it to: src/main/resources/schemas/salesforce-semantic-model-schema.json\n");
            }
            if (!ossieSchemaExists) {
                System.err.println("\n  WARNING: Ossie schema not found at " + SchemaValidator.OSSIE_SCHEMA_PATH);
                System.err.println("  Skipping OssieToSalesforceConverterTest tests.");
                System.err.println("  To run these tests, download the schema from:");
                System.err.println("  https://github.com/apache/ossie/blob/main/core-spec/ossie-schema.json");
                System.err.println("  and save it to: src/main/resources/schemas/ossie-schema.json\n");
            }
            warningPrinted = true;
        }
    }

    @BeforeEach
    void setUp() throws IOException {
        assumeTrue(ossieSchemaExists, "Ossie schema file is required but not found. See README for setup instructions.");

        converter = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE);
        jsonMapper = new ObjectMapper();
        ossieYamlAnsiSql = Files.readString(Paths.get("src/test/resources/examples/ossieToSalesforce.yaml"));
        ossieYaml = ossieYamlAnsiSql;
    }

    @Test
    void testCompleteConversion() throws Exception {
        List<String> results = converter.convert(ossieYaml);

        assertNotNull(results);
        assertEquals(1, results.size());

        String salesforceJson = results.get(0);
        assertNotNull(salesforceJson);
        assertTrue(salesforceJson.contains("\"apiName\" : \"Customer_Orders_Model\""));
        assertTrue(salesforceJson.contains("\"semanticDataObjects\""));

        Map<String, Object> sfModel = jsonMapper.readValue(salesforceJson, new TypeReference<Map<String, Object>>() {});
        assertNotNull(sfModel);
        assertEquals("Customer_Orders_Model", sfModel.get("apiName"));
        assertNotNull(sfModel.get("description"));
    }

    @Test
    void testDatasetMapping() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        assertNotNull(dataObjects);
        assertEquals(3, dataObjects.size());

        assertEquals("Customers", dataObjects.get(0).get("apiName"));
        assertEquals("Orders", dataObjects.get(1).get("apiName"));
        assertEquals("Products", dataObjects.get(2).get("apiName"));

        assertEquals("Customers__dll", dataObjects.get(0).get("dataObjectName"));
        assertEquals("Orders__dll", dataObjects.get(1).get("dataObjectName"));
        assertEquals("Products__dll", dataObjects.get(2).get("dataObjectName"));

        assertEquals("Customer master data", dataObjects.get(0).get("description"));
        assertEquals("Order transaction data", dataObjects.get(1).get("description"));
        assertEquals("Product catalog data", dataObjects.get(2).get("description"));
    }

    @Test
    void testFieldSplittingIntoDimensionsAndMeasurements() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> customersDataset = dataObjects.get(0);

        List<Map<String, Object>> customerDimensions = (List<Map<String, Object>>) customersDataset.get("semanticDimensions");
        List<Map<String, Object>> customerMeasurements = (List<Map<String, Object>>) customersDataset.get("semanticMeasurements");

        assertNotNull(customerDimensions);
        assertNotNull(customerMeasurements);

        boolean hasCustomerId = customerDimensions.stream()
                .anyMatch(d -> "customer_id".equals(d.get("apiName")));
        assertTrue(hasCustomerId, "customer_id should be a dimension");

        boolean hasEmail = customerDimensions.stream()
                .anyMatch(d -> "email".equals(d.get("apiName")));
        assertTrue(hasEmail, "email should be a dimension");

        boolean hasTotalPurchases = customerMeasurements.stream()
                .anyMatch(m -> "total_purchases".equals(m.get("apiName")));
        assertTrue(hasTotalPurchases, "total_purchases should be a measurement");

        Map<String, Object> ordersDataset = dataObjects.get(1);
        List<Map<String, Object>> orderMeasurements = (List<Map<String, Object>>) ordersDataset.get("semanticMeasurements");

        boolean hasAmount = orderMeasurements.stream()
                .anyMatch(m -> "amount".equals(m.get("apiName")));
        assertTrue(hasAmount, "amount should be a measurement");
    }

    @Test
    void testExpressionUnwrappingFromDialects() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> customersDataset = dataObjects.get(0);
        List<Map<String, Object>> customerDimensions = (List<Map<String, Object>>) customersDataset.get("semanticDimensions");

        Map<String, Object> customerIdDim = customerDimensions.stream()
                .filter(d -> "customer_id".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(customerIdDim);
        assertEquals("customer_id__c", customerIdDim.get("dataObjectFieldName"));

        Map<String, Object> emailDim = customerDimensions.stream()
                .filter(d -> "email".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(emailDim);
        assertEquals("email__c", emailDim.get("dataObjectFieldName"));
    }

    @Test
    void testRelationshipConversion() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> relationships = (List<Map<String, Object>>) sfModel.get("semanticRelationships");
        assertNotNull(relationships);
        assertTrue(relationships.size() >= 2, "Should have at least 2 valid relationships");

        Map<String, Object> customersOrdersRel = relationships.stream()
                .filter(r -> "Customers_Orders".equals(r.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(customersOrdersRel);
        assertEquals("Customers", customersOrdersRel.get("leftSemanticDefinitionApiName"));
        assertEquals("Orders", customersOrdersRel.get("rightSemanticDefinitionApiName"));
        assertEquals("Customers to Orders", customersOrdersRel.get("label"));
        assertEquals("OneToMany", customersOrdersRel.get("cardinality"));

        List<Map<String, Object>> criteria = (List<Map<String, Object>>) customersOrdersRel.get("criteria");
        assertNotNull(criteria);
        assertEquals(1, criteria.size());
        assertEquals("customer_id", criteria.get(0).get("leftSemanticFieldApiName"));
        assertEquals("customer_id", criteria.get(0).get("rightSemanticFieldApiName"));
    }

    @Test
    void testCalculatedFieldDetection() throws Exception {
        List<String> ansiResults = converter.convert(ossieYamlAnsiSql);
        Map<String, Object> ansiModel = jsonMapper.readValue(ansiResults.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> ansiCalcDimensions = (List<Map<String, Object>>) ansiModel.get("semanticCalculatedDimensions");
        assertNull(ansiCalcDimensions, "ANSI_SQL dialect: no semanticCalculatedDimensions");
    }

    @Test
    void testCalculatedFieldDroppedForItsDialectIsReported() throws Exception {
        // Export has no translation for a calculation written in any dialect but Tableau,
        // so it omits the field. Omitting it without a word leaves the exported model
        // quietly short of fields the Ossie document declares.
        List<String> reported = new ArrayList<>();
        List<String> results = convertCollectingFieldWarnings(ossieYamlAnsiSql, reported);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<String> exported = exportedFieldNames(sfModel);
        assertFalse(exported.contains("customer_email_domain"),
                "a calculation the export cannot translate must not reach the payload: " + exported);
        assertFalse(exported.contains("order_year"),
                "a calculation the export cannot translate must not reach the payload: " + exported);

        assertTrue(reported.stream().anyMatch(m -> m.contains("customer_email_domain")
                        && m.contains("Customers") && m.contains("ANSI_SQL")),
                "the warning should name the field, its dataset and its dialect: " + reported);
        assertTrue(reported.stream().anyMatch(m -> m.contains("order_year") && m.contains("Orders")),
                "every omitted calculated field should be reported, not just the first: " + reported);
    }

    @Test
    void testCalculatedTableauFieldIsExportedWithoutAWarning() throws Exception {
        // The warning marks a field that was dropped, so a calculation the export does
        // translate has to pass through silently. Normalize line endings first: the fixture
        // may check out with CRLF, but the substitutions below are written with LF.
        String yamlWithTableauCalculations = ossieYamlAnsiSql.replace("\r\n", "\n")
                .replace("      - dialect: ANSI_SQL\n"
                        + "        expression: SUBSTRING([Customers].[email], POSITION('@' IN [Customers].[email])\n"
                        + "          + 1, LENGTH([Customers].[email]))\n",
                        "      - dialect: TABLEAU\n"
                        + "        expression: 'SPLIT([Customers].[email], \"@\", 2)'\n")
                .replace("      - dialect: ANSI_SQL\n"
                        + "        expression: YEAR([Orders].[order_date])\n",
                        "      - dialect: TABLEAU\n"
                        + "        expression: YEAR([Orders].[order_date])\n");
        assertFalse(yamlWithTableauCalculations.contains("dialect: ANSI_SQL\n        expression: SUBSTRING"),
                "fixture text substitution did not match");
        assertFalse(yamlWithTableauCalculations.contains("dialect: ANSI_SQL\n        expression: YEAR"),
                "fixture text substitution did not match");

        List<String> reported = new ArrayList<>();
        List<String> results = convertCollectingFieldWarnings(yamlWithTableauCalculations, reported);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<String> exported = exportedFieldNames(sfModel);
        assertTrue(exported.contains("customer_email_domain"), "a Tableau calculation should be exported");
        assertTrue(exported.contains("order_year"), "a Tableau calculation should be exported");
        assertTrue(reported.isEmpty(), "nothing was dropped, so nothing should be reported: " + reported);
    }

    @Test
    void testCalculatedFieldWithNoDialectIsReportedAsMissingADialect() {
        // The Ossie schema makes a dialect mandatory, so a field reaches the handler without
        // one only when a caller builds the model in memory instead of parsing a document.
        // The dialect is then null, and naming it would report the dialect as 'null'.
        Map<String, Object> ossieField = Map.of(
                "name", "order_year",
                "dimension", Map.of(),
                "expression", Map.of("dialects", List.of(Map.of("expression", "YEAR(order_date)"))));
        Map<String, Object> sourceData = Map.of(
                "datasets", List.of(Map.of("name", "Orders", "fields", List.of(ossieField))));
        Map<String, Object> sfDataObject = new LinkedHashMap<>(Map.of("apiName", "Orders"));
        Map<String, Object> outputData = new LinkedHashMap<>(
                Map.of("semanticDataObjects", new ArrayList<>(List.of(sfDataObject))));

        Logger fieldLogger = (Logger) LoggerFactory.getLogger(FieldMappingHandler.class);
        ListAppender<ILoggingEvent> warnings = new ListAppender<>();
        warnings.start();
        fieldLogger.addAppender(warnings);
        try {
            new FieldMappingHandler(ConversionDirection.OSSIE_TO_SALESFORCE,
                    new CustomExtensionHandler(jsonMapper))
                    .execute(sourceData, outputData, Map.of());
        } finally {
            fieldLogger.detachAppender(warnings);
        }

        List<String> reported = warnings.list.stream()
                .filter(event -> event.getLevel() == Level.WARN)
                .map(ILoggingEvent::getFormattedMessage)
                .toList();
        assertEquals(1, reported.size(), "the omitted field should be reported once: " + reported);
        assertTrue(reported.get(0).contains("order_year") && reported.get(0).contains("Orders"),
                "the warning should name the field and its dataset: " + reported);
        assertFalse(reported.get(0).contains("null"),
                "a dialect that was never declared must not be reported as the dialect 'null': " + reported);
        assertNull(sfDataObject.get("semanticDimensions"), "the field should still be omitted");
    }

    /**
     * Converts {@code ossieModel}, adding every warning {@link FieldMappingHandler} reported
     * while doing so to {@code warningsOut}.
     */
    private List<String> convertCollectingFieldWarnings(String ossieModel, List<String> warningsOut)
            throws Exception {
        Logger fieldLogger = (Logger) LoggerFactory.getLogger(FieldMappingHandler.class);
        ListAppender<ILoggingEvent> warnings = new ListAppender<>();
        warnings.start();
        fieldLogger.addAppender(warnings);
        try {
            return converter.convert(ossieModel);
        } finally {
            fieldLogger.detachAppender(warnings);
            warnings.list.stream()
                    .filter(event -> event.getLevel() == Level.WARN)
                    .map(ILoggingEvent::getFormattedMessage)
                    .forEach(warningsOut::add);
        }
    }

    /** Every field name the exported model carries, whichever array the field was routed to. */
    private List<String> exportedFieldNames(Map<String, Object> sfModel) {
        List<String> names = new ArrayList<>();
        collectApiNames((List<Map<String, Object>>) sfModel.get("semanticCalculatedDimensions"), names);
        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        if (dataObjects != null) {
            for (Map<String, Object> dataObject : dataObjects) {
                collectApiNames((List<Map<String, Object>>) dataObject.get("semanticDimensions"), names);
                collectApiNames((List<Map<String, Object>>) dataObject.get("semanticMeasurements"), names);
            }
        }
        return names;
    }

    private void collectApiNames(List<Map<String, Object>> sfFields, List<String> namesOut) {
        if (sfFields == null) {
            return;
        }
        for (Map<String, Object> sfField : sfFields) {
            namesOut.add(String.valueOf(sfField.get("apiName")));
        }
    }

    @Test
    void testInvalidRelationshipsFiltered() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> relationships = (List<Map<String, Object>>) sfModel.get("semanticRelationships");
        assertNotNull(relationships);
        assertEquals(2, relationships.size(), "Only 2 relationships should be present");

        boolean hasValidCustomersOrders = relationships.stream()
                .anyMatch(r -> "Customers_Orders".equals(r.get("apiName")));
        assertTrue(hasValidCustomersOrders, "Customers_Orders should be included");

        boolean hasValidOrdersProducts = relationships.stream()
                .anyMatch(r -> "Orders_Products".equals(r.get("apiName")));
        assertTrue(hasValidOrdersProducts, "Orders_Products should be included");
    }

    /**
     * Builds a model whose first relationship is dropped by the validator, because its
     * join column is not a field of the entity it names, while the second one is valid.
     * Export has to carry the second relationship's own endpoints and columns across.
     */
    private static String ossieModelWithAFilteredRelationshipFirst() {
        return "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + dataset("Customers", "customer_id")
                + dataset("Orders", "product_id")
                + dataset("Products", "product_id")
                + "relationships:\n"
                + relationship("Customers_Orders", "Customers", "Orders", "missing_column")
                + relationship("Orders_Products", "Orders", "Products", "product_id");
    }

    private static String dataset(String name, String... columns) {
        StringBuilder yaml = new StringBuilder("- name: " + name + "\n"
                + "  source: " + name + "__dll\n"
                + "  fields:\n");
        for (String column : columns) {
            yaml.append("  - name: ").append(column).append("\n")
                    .append("    datatype: String\n")
                    .append("    dimension: {}\n")
                    .append("    expression:\n")
                    .append("      dialects:\n")
                    .append("      - dialect: ANSI_SQL\n")
                    .append("        expression: ").append(column).append("\n");
        }
        return yaml.toString();
    }

    private static String relationship(String name, String from, String to, String column) {
        return "- name: " + name + "\n"
                + "  from: " + from + "\n"
                + "  to: " + to + "\n"
                + "  from_columns: [" + column + "]\n"
                + "  to_columns: [" + column + "]\n";
    }

    @Test
    void testRelationshipAfterAFilteredOneKeepsItsOwnEndpointsAndCriteria() throws Exception {
        List<String> results = converter.convert(ossieModelWithAFilteredRelationshipFirst());
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> relationships = (List<Map<String, Object>>) sfModel.get("semanticRelationships");
        assertNotNull(relationships);
        assertEquals(1, relationships.size(), "Only the valid relationship should be exported");

        Map<String, Object> exported = relationships.get(0);
        assertEquals("Orders_Products", exported.get("apiName"));
        assertEquals("Orders", exported.get("leftSemanticDefinitionApiName"));
        assertEquals("Products", exported.get("rightSemanticDefinitionApiName"));

        List<Map<String, Object>> criteria = (List<Map<String, Object>>) exported.get("criteria");
        assertNotNull(criteria);
        assertEquals(1, criteria.size());
        assertEquals("product_id", criteria.get(0).get("leftSemanticFieldApiName"));
        assertEquals("product_id", criteria.get(0).get("rightSemanticFieldApiName"));
    }

    @Test
    void testRelationshipWithUnpairableJoinColumnsIsRejected() {
        // The two sides name a different number of join columns, so the relationship has no
        // criteria to export. Salesforce accepts a relationship with no criteria and stores it
        // as an enabled, queryable relationship that joins nothing, so this is a spec violation
        // (core-spec/spec.md requires the same count on both sides), not a Salesforce
        // limitation: the whole conversion must fail instead of silently dropping it.
        String model = "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + dataset("Customers", "customer_id", "tenant_id")
                + dataset("Orders", "customer_id", "product_id")
                + dataset("Products", "product_id")
                + "relationships:\n"
                + "- name: Customers_Orders\n"
                + "  from: Customers\n"
                + "  to: Orders\n"
                + "  from_columns: [customer_id, tenant_id]\n"
                + "  to_columns: [customer_id]\n"
                + relationship("Orders_Products", "Orders", "Products", "product_id");

        ValidationException exception = assertThrows(ValidationException.class, () -> converter.convert(model));
        assertTrue(exception.getMessage().contains("Customers_Orders"),
                "the rejection must name the relationship that cannot be converted");
        assertTrue(exception.getMessage().contains("got 2 and 1"),
                "the rejection must come from the column-count rule");
    }

    @Test
    void testRelationshipWithUndeclaredEndpointAndUnpairableJoinColumnsIsRejected() {
        // The column-count check must not depend on the relationship's endpoints existing.
        // A relationship naming an undeclared dataset used to be dropped by the entity lookup
        // before the column-count check ran, so this case was silently filtered instead of
        // rejected.
        String model = "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + dataset("Orders", "customer_id", "product_id")
                + dataset("Products", "product_id")
                + "relationships:\n"
                + "- name: Customers_Orders\n"
                + "  from: Customer\n"
                + "  to: Orders\n"
                + "  from_columns: [customer_id, tenant_id]\n"
                + "  to_columns: [customer_id]\n"
                + relationship("Orders_Products", "Orders", "Products", "product_id");

        ValidationException exception = assertThrows(ValidationException.class, () -> converter.convert(model));
        assertTrue(exception.getMessage().contains("Customers_Orders"),
                "the rejection must name the relationship that cannot be converted");
        assertTrue(exception.getMessage().contains("got 2 and 1"),
                "the rejection must come from the column-count rule, not the endpoint lookup");
    }

    @Test
    void testRelationshipDeclaringNoJoinColumnsIsRejected() {
        // The Ossie schema requires from_columns and to_columns and gives each a minimum of
        // one entry, so a relationship reaches the handler without join columns only when a
        // caller builds the model in memory instead of parsing a document. It has nothing to
        // join on either way, so it must still be rejected.
        Map<String, Object> noJoinColumns = new LinkedHashMap<>(
                Map.of("name", "Customers_Orders", "from", "Customers", "to", "Orders"));
        Map<String, Object> joined = new LinkedHashMap<>(Map.of(
                "name", "Orders_Products", "from", "Orders", "to", "Products",
                "from_columns", List.of("product_id"), "to_columns", List.of("product_id")));
        Map<String, Object> sourceData = new LinkedHashMap<>(
                Map.of("relationships", new ArrayList<>(List.of(noJoinColumns, joined))));
        Map<String, Object> outputData = new LinkedHashMap<>(Map.of("semanticDataObjects", List.of(
                dataObjectWithDimension("Customers", "customer_id"),
                dataObjectWithDimension("Orders", "product_id"),
                dataObjectWithDimension("Products", "product_id"))));

        RelationshipMappingHandler handler = new RelationshipMappingHandler(ConversionDirection.OSSIE_TO_SALESFORCE,
                new CustomExtensionHandler(jsonMapper));

        ValidationException exception = assertThrows(ValidationException.class,
                () -> handler.execute(sourceData, outputData, new LinkedHashMap<>()));
        assertTrue(exception.getMessage().contains("Customers_Orders"),
                "the rejection must name the relationship that cannot be converted");
        assertTrue(exception.getMessage().contains("got 0 and 0"),
                "the rejection must come from the column-count rule");
    }

    private static Map<String, Object> dataObjectWithDimension(String apiName, String fieldApiName) {
        return Map.of("apiName", apiName, "semanticDimensions", List.of(Map.of("apiName", fieldApiName)));
    }

    @Test
    void testAModelWhoseRelationshipsAreAllFilteredExportsNone() throws Exception {
        // Dropping the last valid relationship used to leave the relationship mappings
        // unclaimed, and whatever is left unclaimed is applied generically once every
        // handler has run -- putting the rejected relationships back into the payload in
        // their Ossie shape, which the semantic model API answers with HTTP 500.
        String ossieModel = "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + "- name: Orders\n"
                + "  source: Orders__dll\n"
                + "  fields:\n"
                + "  - name: order_id\n"
                + "    dimension: {}\n"
                + "    expression:\n"
                + "      dialects:\n"
                + "      - dialect: ANSI_SQL\n"
                + "        expression: order_id\n"
                + "- name: Products\n"
                + "  source: Products__dll\n"
                + "  fields:\n"
                + "  - name: product_id\n"
                + "    dimension: {}\n"
                + "    expression:\n"
                + "      dialects:\n"
                + "      - dialect: ANSI_SQL\n"
                + "        expression: product_id\n"
                + "relationships:\n"
                + "- name: Orders_Products\n"
                + "  from: Orders\n"
                + "  to: Products\n"
                + "  from_columns: [missing_id]\n"
                + "  to_columns: [product_id]\n";

        List<String> results = converter.convert(ossieModel);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        assertNull(sfModel.get("semanticRelationships"),
                "a relationship dropped by validation should not reach the payload");
    }

    @Test
    void testCustomExtensionsRestoration() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        assertEquals("Customer_Orders_Model", sfModel.get("label"));
        assertEquals("default", sfModel.get("dataspace"));

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> customersDataset = dataObjects.get(0);
        assertEquals("Customers", customersDataset.get("label"));
        assertEquals("Dlo", customersDataset.get("dataObjectType"));

        List<Map<String, Object>> customerDimensions = (List<Map<String, Object>>) customersDataset.get("semanticDimensions");
        Map<String, Object> customerIdDim = customerDimensions.stream()
                .filter(d -> "customer_id".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(customerIdDim);
        assertEquals("Text", customerIdDim.get("dataType"));
        assertEquals("Discrete", customerIdDim.get("displayCategory"));

        Map<String, Object> emailDim = customerDimensions.stream()
                .filter(d -> "email".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(emailDim);
        assertEquals("Email", emailDim.get("dataType"),
                "Exact Salesforce extension type should win over portable String");

        List<Map<String, Object>> customerMeasurements =
                (List<Map<String, Object>>) customersDataset.get("semanticMeasurements");
        Map<String, Object> lifetimeValue = customerMeasurements.stream()
                .filter(m -> "lifetime_value".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(lifetimeValue);
        assertEquals("Currency", lifetimeValue.get("dataType"),
                "Exact Salesforce extension type should win over portable Decimal");
    }

    @Test
    void testFieldWhoseColumnNameIsASqlKeywordIsNotDroppedAsCalculated() throws Exception {
        // A warehouse column may legitimately be named DATE, MONTH or COUNT. Such a field is a
        // direct reference, not a calculation, and must still reach the data object.
        String yamlWithKeywordColumn = ossieYaml.replace("\r\n", "\n")
                .replace("        expression: product_name__c\n", "        expression: DATE\n");
        assertFalse(yamlWithKeywordColumn.contains("expression: product_name__c"),
                "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithKeywordColumn);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> productsDataset = dataObjects.stream()
                .filter(d -> "Products".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productsDataset);

        List<Map<String, Object>> productDimensions =
                (List<Map<String, Object>>) productsDataset.get("semanticDimensions");
        Map<String, Object> productName = productDimensions.stream()
                .filter(d -> "product_name".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productName, "a dimension whose column is named DATE must not be dropped");
        assertEquals("DATE", productName.get("dataObjectFieldName"),
                "the keyword-named column should be carried through as a direct reference");

        List<Map<String, Object>> calculatedDimensions =
                (List<Map<String, Object>>) sfModel.get("semanticCalculatedDimensions");
        if (calculatedDimensions != null) {
            assertTrue(calculatedDimensions.stream()
                            .noneMatch(d -> "product_name".equals(d.get("apiName"))),
                    "a keyword-named column must not be routed to calculated dimensions");
        }
    }

    @Test
    void testTableauQuotedLiteralIsStillCalculated() throws Exception {
        // Double quotes delimit an identifier in SQL but a string literal in Tableau, so a
        // Tableau expression such as "Not Available" is a constant, not a column reference,
        // and must still be routed to the calculated dimensions.
        String yamlWithTableauLiteral = ossieYaml.replace("\r\n", "\n")
                .replace("      - dialect: ANSI_SQL\n"
                        + "        expression: product_name__c\n",
                        "      - dialect: TABLEAU\n"
                        + "        expression: '\"Not Available\"'\n");
        assertFalse(yamlWithTableauLiteral.contains("expression: product_name__c"),
                "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithTableauLiteral);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> calculatedDimensions =
                (List<Map<String, Object>>) sfModel.get("semanticCalculatedDimensions");
        assertNotNull(calculatedDimensions);
        Map<String, Object> productName = calculatedDimensions.stream()
                .filter(d -> "product_name".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productName, "a Tableau quoted literal must be exported as a calculated dimension");
        assertEquals("\"Not Available\"", productName.get("expression"));

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> productsDataset = dataObjects.stream()
                .filter(d -> "Products".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productsDataset);
        assertTrue(((List<Map<String, Object>>) productsDataset.get("semanticDimensions")).stream()
                        .noneMatch(d -> "product_name".equals(d.get("apiName"))),
                "a Tableau quoted literal must not be exported as a direct column reference");
    }

    @Test
    void testFieldLabelDefaultsToApiNameWhenOssieHasNoLabel() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitutions below are written with LF.
        // Drop the Ossie label from one dimension and one measurement; every other field keeps
        // its own, so this also pins that an explicit label is still carried through untouched.
        String yamlWithoutLabels = ossieYaml.replace("\r\n", "\n")
                .replace("  - name: product_name\n"
                        + "    datatype: String\n"
                        + "    label: Product Name\n",
                        "  - name: product_name\n"
                        + "    datatype: String\n")
                .replace("  - name: stock_level\n"
                        + "    datatype: Decimal\n"
                        + "    label: Stock Level\n",
                        "  - name: stock_level\n"
                        + "    datatype: Decimal\n");
        assertFalse(yamlWithoutLabels.contains("label: Product Name"), "fixture text substitution did not match");
        assertFalse(yamlWithoutLabels.contains("label: Stock Level"), "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithoutLabels);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> productsDataset = dataObjects.stream()
                .filter(d -> "Products".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productsDataset);

        List<Map<String, Object>> productDimensions =
                (List<Map<String, Object>>) productsDataset.get("semanticDimensions");
        Map<String, Object> productName = productDimensions.stream()
                .filter(d -> "product_name".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productName);
        assertEquals("product_name", productName.get("label"),
                "a dimension with no Ossie label should default its label to apiName");

        Map<String, Object> productId = productDimensions.stream()
                .filter(d -> "product_id".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productId);
        assertEquals("Product ID", productId.get("label"),
                "an explicit Ossie label should still win over the apiName default");

        List<Map<String, Object>> productMeasurements =
                (List<Map<String, Object>>) productsDataset.get("semanticMeasurements");
        Map<String, Object> stockLevel = productMeasurements.stream()
                .filter(m -> "stock_level".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(stockLevel);
        assertEquals("stock_level", stockLevel.get("label"),
                "a measurement with no Ossie label should default its label to apiName");
    }

    @Test
    void testBlankOrNullFieldLabelIsDefaultedToApiName() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitutions below are written with LF.
        // The Ossie schema puts no minLength on a field label, so an empty one is a valid
        // model, and a label restored from custom_extensions can be null. Salesforce rejects
        // both exactly as it rejects a missing MasterLabel.
        String yamlWithBlankLabels = ossieYaml.replace("\r\n", "\n")
                .replace("    label: Unit Price\n", "    label: \"\"\n")
                .replace("    label: Order ID\n", "")
                .replace("        expression: order_id__c\n"
                        + "    custom_extensions:\n"
                        + "    - vendor_name: SALESFORCE\n"
                        + "      data: |-\n"
                        + "        {\n",
                        "        expression: order_id__c\n"
                        + "    custom_extensions:\n"
                        + "    - vendor_name: SALESFORCE\n"
                        + "      data: |-\n"
                        + "        {\n"
                        + "          \"label\" : null,\n");
        assertFalse(yamlWithBlankLabels.contains("label: Unit Price"), "fixture text substitution did not match");
        assertFalse(yamlWithBlankLabels.contains("label: Order ID"), "fixture text substitution did not match");
        assertTrue(yamlWithBlankLabels.contains("\"label\" : null"), "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithBlankLabels);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> productsDataset = dataObjects.stream()
                .filter(d -> "Products".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productsDataset);

        List<Map<String, Object>> productMeasurements =
                (List<Map<String, Object>>) productsDataset.get("semanticMeasurements");
        Map<String, Object> unitPrice = productMeasurements.stream()
                .filter(m -> "unit_price".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(unitPrice);
        assertEquals("unit_price", unitPrice.get("label"),
                "an empty Ossie label should be defaulted to apiName, not exported as is");

        Map<String, Object> ordersDataset = dataObjects.stream()
                .filter(d -> "Orders".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(ordersDataset);

        List<Map<String, Object>> orderDimensions =
                (List<Map<String, Object>>) ordersDataset.get("semanticDimensions");
        Map<String, Object> orderId = orderDimensions.stream()
                .filter(d -> "order_id".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(orderId);
        assertEquals("order_id", orderId.get("label"),
                "a null label restored from custom_extensions should be defaulted to apiName");
    }

    @Test
    void testFieldLabelFromCustomExtensionsWinsOverApiNameDefault() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitution below is written with LF.
        // Drop the Ossie label from product_name and stash a Salesforce one in its
        // custom_extensions instead, so the only label available is the restored one.
        String yamlWithExtensionLabel = ossieYaml.replace("\r\n", "\n").replace(
                "  - name: product_name\n"
                        + "    datatype: String\n"
                        + "    label: Product Name\n"
                        + "    description: Product display name\n"
                        + "    dimension:\n"
                        + "      is_time: false\n"
                        + "    expression:\n"
                        + "      dialects:\n"
                        + "      - dialect: ANSI_SQL\n"
                        + "        expression: product_name__c\n"
                        + "    custom_extensions:\n"
                        + "    - vendor_name: SALESFORCE\n"
                        + "      data: |-\n"
                        + "        {\n"
                        + "          \"dataType\" : \"Text\",\n",
                "  - name: product_name\n"
                        + "    datatype: String\n"
                        + "    description: Product display name\n"
                        + "    dimension:\n"
                        + "      is_time: false\n"
                        + "    expression:\n"
                        + "      dialects:\n"
                        + "      - dialect: ANSI_SQL\n"
                        + "        expression: product_name__c\n"
                        + "    custom_extensions:\n"
                        + "    - vendor_name: SALESFORCE\n"
                        + "      data: |-\n"
                        + "        {\n"
                        + "          \"label\" : \"Product Name (Custom Label)\",\n"
                        + "          \"dataType\" : \"Text\",\n");
        assertFalse(yamlWithExtensionLabel.contains("label: Product Name"),
                "fixture text substitution did not match");
        assertTrue(yamlWithExtensionLabel.contains("Product Name (Custom Label)"),
                "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithExtensionLabel);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> productsDataset = dataObjects.stream()
                .filter(d -> "Products".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productsDataset);

        List<Map<String, Object>> productDimensions =
                (List<Map<String, Object>>) productsDataset.get("semanticDimensions");
        Map<String, Object> productName = productDimensions.stream()
                .filter(d -> "product_name".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(productName);
        assertEquals("Product Name (Custom Label)", productName.get("label"),
                "a label restored from custom_extensions should win over the apiName default");
    }

    @Test
    void testCalculatedDimensionLabelDefaultsToApiName() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitutions below are written with LF.
        // order_year is only exported as a semanticCalculatedDimension under the Tableau dialect,
        // so switch that one field over and drop its label.
        String yamlWithTableauCalcDim = ossieYaml.replace("\r\n", "\n")
                .replace("  - name: order_year\n"
                        + "    datatype: Decimal\n"
                        + "    label: Order Year\n",
                        "  - name: order_year\n"
                        + "    datatype: Decimal\n")
                .replace("      - dialect: ANSI_SQL\n"
                        + "        expression: YEAR([Orders].[order_date])\n",
                        "      - dialect: TABLEAU\n"
                        + "        expression: YEAR([Orders].[order_date])\n");
        assertFalse(yamlWithTableauCalcDim.contains("label: Order Year"), "fixture text substitution did not match");
        assertTrue(yamlWithTableauCalcDim.contains("dialect: TABLEAU"), "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithTableauCalcDim);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> calcDimensions =
                (List<Map<String, Object>>) sfModel.get("semanticCalculatedDimensions");
        assertNotNull(calcDimensions, "the Tableau dialect should produce a semanticCalculatedDimension");
        Map<String, Object> orderYear = calcDimensions.stream()
                .filter(d -> "order_year".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(orderYear);
        assertEquals("order_year", orderYear.get("label"),
                "a calculated dimension with no Ossie label should default its label to apiName");
    }

    @Test
    void testCalculatedDimensionDeclaresTuaSyntax() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitution below is written with LF.
        // Tua is the only expression syntax the Salesforce semantic model API accepts; sending
        // the dialect name rejects the whole model with "Invalid Expression Syntax Type".
        String yamlWithTableauCalcDim = ossieYaml.replace("\r\n", "\n")
                .replace("      - dialect: ANSI_SQL\n"
                        + "        expression: YEAR([Orders].[order_date])\n",
                        "      - dialect: TABLEAU\n"
                        + "        expression: YEAR([Orders].[order_date])\n");
        assertTrue(yamlWithTableauCalcDim.contains("dialect: TABLEAU"), "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithTableauCalcDim);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> calcDimensions =
                (List<Map<String, Object>>) sfModel.get("semanticCalculatedDimensions");
        assertNotNull(calcDimensions, "the Tableau dialect should produce a semanticCalculatedDimension");
        Map<String, Object> orderYear = calcDimensions.stream()
                .filter(d -> "order_year".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(orderYear);
        assertEquals("Tua", orderYear.get("syntax"),
                "a calculated dimension must declare the Tua expression syntax");
    }

    @Test
    void testMetricsConvertedToSemanticCalculatedMeasurements() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> calcMeasurements = (List<Map<String, Object>>) sfModel.get("semanticCalculatedMeasurements");
        assertNotNull(calcMeasurements, "Metrics from Ossie should convert to semanticCalculatedMeasurements");
        assertEquals(2, calcMeasurements.size());

        Map<String, Object> totalRevenue = calcMeasurements.stream()
                .filter(m -> "total_revenue".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(totalRevenue);
        assertEquals("Sum of all order amounts", totalRevenue.get("description"));
        assertEquals("Number", totalRevenue.get("dataType"));
        // Legacy ANSI_SQL bracket references are validated and emitted as Tua.
        assertEquals("SUM([Orders].[amount])", totalRevenue.get("expression"));
        assertEquals("Tua", totalRevenue.get("syntax"));
        assertEquals("UserAgg", totalRevenue.get("aggregationType"));

        Map<String, Object> avgOrderValue = calcMeasurements.stream()
                .filter(m -> "avg_order_value".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(avgOrderValue);
        assertEquals("AVG([Orders].[amount])", avgOrderValue.get("expression"));
    }

    @Test
    void testMetricCustomExtensionsRestoredBeforeExpressionCompilation() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitution below is written with LF.
        String yamlWithMetricExtension = ossieYaml.replace("\r\n", "\n").replace(
                "metrics:\n"
                        + "- description: Sum of all order amounts\n"
                        + "  name: total_revenue\n"
                        + "  datatype: Decimal\n"
                        + "  expression:\n"
                        + "    dialects:\n"
                        + "    - dialect: ANSI_SQL\n"
                        + "      expression: SUM([Orders].[amount])\n",
                "metrics:\n"
                        + "- description: Sum of all order amounts\n"
                        + "  name: total_revenue\n"
                        + "  datatype: Decimal\n"
                        + "  expression:\n"
                        + "    dialects:\n"
                        + "    - dialect: ANSI_SQL\n"
                        + "      expression: SUM([Orders].[amount])\n"
                        + "  custom_extensions:\n"
                        + "  - vendor_name: SALESFORCE\n"
                        + "    data: |-\n"
                        + "      {\n"
                        + "        \"label\": \"Total Revenue (Custom Label)\",\n"
                        + "        \"dataType\": \"Currency\"\n"
                        + "      }\n");
        assertTrue(yamlWithMetricExtension.contains("Total Revenue (Custom Label)"),
                "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithMetricExtension);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});
        List<Map<String, Object>> calcMeasurements = (List<Map<String, Object>>) sfModel.get("semanticCalculatedMeasurements");

        Map<String, Object> totalRevenue = calcMeasurements.stream()
                .filter(m -> "total_revenue".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(totalRevenue);
        assertEquals("Total Revenue (Custom Label)", totalRevenue.get("label"),
                "custom_extensions on a metric should be restored onto its exported semanticCalculatedMeasurement");
        assertEquals("Currency", totalRevenue.get("dataType"),
                "exact Salesforce dataType restored from custom_extensions should win over the Tua compiler's derived type");
        // The compiled Tua expression is still produced normally; restoring custom_extensions
        // must not interfere with fields the expression compiler itself computes.
        assertEquals("SUM([Orders].[amount])", totalRevenue.get("expression"));
        assertEquals("Tua", totalRevenue.get("syntax"));

        Map<String, Object> avgOrderValue = calcMeasurements.stream()
                .filter(m -> "avg_order_value".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(avgOrderValue);
        assertEquals("avg_order_value", avgOrderValue.get("label"),
                "a metric with no custom_extensions label should default its label to apiName");
        assertEquals("Number", avgOrderValue.get("dataType"),
                "a metric with no custom_extensions dataType should keep the Tua compiler's derived type");
    }

    @Test
    void testMetricExpressionPrefersTableauDialectOverAnsiSql() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitution below is written with LF.
        String yamlWithTableauMetric = ossieYaml.replace("\r\n", "\n").replace(
                "metrics:\n"
                        + "- description: Sum of all order amounts\n"
                        + "  name: total_revenue\n"
                        + "  datatype: Decimal\n"
                        + "  expression:\n"
                        + "    dialects:\n"
                        + "    - dialect: ANSI_SQL\n"
                        + "      expression: SUM([Orders].[amount])\n",
                "metrics:\n"
                        + "- description: Sum of all order amounts\n"
                        + "  name: total_revenue\n"
                        + "  datatype: Decimal\n"
                        + "  expression:\n"
                        + "    dialects:\n"
                        + "    - dialect: ANSI_SQL\n"
                        + "      expression: SUM([Orders].[amount])\n"
                        + "    - dialect: TABLEAU\n"
                        + "      expression: MAX([Orders].[amount])\n");
        assertTrue(yamlWithTableauMetric.contains("dialect: TABLEAU"), "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithTableauMetric);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});
        List<Map<String, Object>> calcMeasurements = (List<Map<String, Object>>) sfModel.get("semanticCalculatedMeasurements");

        Map<String, Object> totalRevenue = calcMeasurements.stream()
                .filter(m -> "total_revenue".equals(m.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(totalRevenue);
        assertEquals("MAX([Orders].[amount])", totalRevenue.get("expression"),
                "TABLEAU dialect should be preferred over ANSI_SQL when both are present");
    }

    @Test
    void testMetricWithNoConvertibleDialectFailsConversion() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitution below is written with LF.
        // The Ossie schema requires every metric to have an expression and restricts `dialect`
        // to its own enum, so this uses BIGQUERY (a valid dialect, but neither TABLEAU nor
        // ANSI_SQL) rather than omitting the expression or inventing an unrecognized dialect.
        String yamlWithUnconvertibleDialect = ossieYaml.replace("\r\n", "\n").replace(
                "    - dialect: ANSI_SQL\n"
                        + "      expression: SUM([Orders].[amount])\n",
                "    - dialect: BIGQUERY\n"
                        + "      expression: SUM(Orders.amount)\n");
        assertTrue(yamlWithUnconvertibleDialect.contains("dialect: BIGQUERY"),
                "fixture text substitution did not match");

        Exception exception =
                assertThrows(Exception.class, () -> converter.convert(yamlWithUnconvertibleDialect));
        String message = exception.getMessage() != null ? exception.getMessage() : exception.getCause().getMessage();
        assertTrue(message.contains("total_revenue"), "error should name the unconvertible metric: " + message);
    }

    @Test
    void testTimeDimensionConversion() throws Exception {
        List<String> results = converter.convert(ossieYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> ordersDataset = dataObjects.get(1);
        List<Map<String, Object>> orderDimensions = (List<Map<String, Object>>) ordersDataset.get("semanticDimensions");

        Map<String, Object> orderDateDim = orderDimensions.stream()
                .filter(d -> "order_date".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(orderDateDim);
        assertEquals("Date", orderDateDim.get("dataType"));
        assertEquals("Discrete", orderDateDim.get("displayCategory"));
    }

    @Test
    void testOutputCompilesWithSalesforceSchema() throws Exception {
        assumeTrue(salesforceSchemaExists, "Salesforce schema file is required but not found. See README for setup instructions.");

        List<String> results = converter.convert(ossieYaml);
        String salesforceJson = results.get(0);

        Map<String, Object> sfModel = jsonMapper.readValue(salesforceJson, new TypeReference<Map<String, Object>>() {});

        SchemaValidator validator = new SchemaValidator(jsonMapper, SchemaValidator.SALESFORCE_SCHEMA_PATH);
        assertDoesNotThrow(() -> validator.validate(sfModel), "Output should comply with Salesforce schema");
    }

    @Test
    void testPipelineConfigLoadsSuccessfully() {
        // Verify pipeline configuration is loaded correctly
        PipelineConfig config =
            PipelineConfigLoader.loadFromResource();

        assertNotNull(config);
        assertNotNull(config.getPipelines());
        assertTrue(config.getPipelines().containsKey("ossieToSalesforce"));
        assertTrue(config.getPipelines().containsKey("salesforceToOssie"));

        // Verify handler list for ossieToSalesforce
        List<String> handlers = config.getPipelines().get("ossieToSalesforce");
        assertNotNull(handlers);
        assertEquals(5, handlers.size());
        assertTrue(handlers.contains("DatasetMappingHandler"));
        assertTrue(handlers.contains("FieldMappingHandler"));
        assertTrue(handlers.contains("RelationshipMappingHandler"));
        assertTrue(handlers.contains("MetricMappingHandler"));
        assertTrue(handlers.contains("SemanticModelMappingHandler"));

        // Verify direction config
        assertNotNull(config.getDirectionConfigs());
        DirectionConfig dirConfig =
            config.getDirectionConfigs().get("ossieToSalesforce");
        assertNotNull(dirConfig);
        assertEquals("yaml", dirConfig.getInputFormat());
        assertEquals("json", dirConfig.getOutputFormat());
        assertEquals("/schemas/ossie-schema.json", dirConfig.getSchemaPath());
        assertEquals("apiName", dirConfig.getExtractModelNameFrom());
    }

    @Test
    void testHandlerFactoryCreatesAllHandlers() {
        // Verify HandlerFactory can create all configured handlers
        CustomExtensionHandler customExtensionHandler =
            new CustomExtensionHandler(jsonMapper);
        HandlerFactory factory =
            new HandlerFactory(customExtensionHandler);

        ConversionDirection direction = ConversionDirection.OSSIE_TO_SALESFORCE;

        assertDoesNotThrow(() -> factory.createHandler("DatasetMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("FieldMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("RelationshipMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("MetricMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("SemanticModelMappingHandler", direction));
    }

    /**
     * Builds a model that never passed through Salesforce, so it carries no
     * custom_extensions to restore the API's required properties from. Every other
     * converter in the hub hands the Salesforce side a document shaped like this.
     */
    private static String ossieModelWithoutSalesforceExtensions(String... sources) {
        StringBuilder yaml = new StringBuilder()
                .append("version: 0.2.0.dev0\n")
                .append("name: Imported_Model\n")
                .append("datasets:\n");
        for (int i = 0; i < sources.length; i++) {
            yaml.append("- name: Dataset").append(i).append("\n")
                    .append("  source: ").append(sources[i]).append("\n")
                    .append("  fields:\n")
                    .append("  - name: id\n")
                    .append("    datatype: String\n")
                    .append("    dimension:\n")
                    .append("      is_time: false\n")
                    .append("    expression:\n")
                    .append("      dialects:\n")
                    .append("      - dialect: ANSI_SQL\n")
                    .append("        expression: id\n");
        }
        return yaml.toString();
    }

    @Test
    void testDataspaceDefaultsWhenOssieCarriesNoSalesforceExtensions() throws Exception {
        // The semantic model API rejects a payload with no dataspace outright, so a model
        // that never came from Salesforce has to be given the org's default one.
        List<String> results = converter.convert(ossieModelWithoutSalesforceExtensions("Orders__dll"));
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        assertEquals("default", sfModel.get("dataspace"),
                "a model with no Salesforce custom_extensions should still carry a dataspace");
    }

    /**
     * Builds a dataset that leaves export with no Salesforce type to work from. Ossie
     * makes datatype optional and marks a field's role with nothing but the presence of
     * {@code dimension}, so the measurement declares no datatype at all and the dimension
     * declares only {@code regionDatatype}, which is either absent or a portable type.
     */
    private static String ossieModelWithUntypedFields(String regionDatatype) {
        return "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + "- name: Orders\n"
                + "  source: Orders__dll\n"
                + "  fields:\n"
                + "  - name: region\n"
                + (regionDatatype == null ? "" : "    datatype: " + regionDatatype + "\n")
                + "    dimension: {}\n"
                + "    expression:\n"
                + "      dialects:\n"
                + "      - dialect: ANSI_SQL\n"
                + "        expression: region\n"
                + "  - name: amount\n"
                + "    expression:\n"
                + "      dialects:\n"
                + "      - dialect: ANSI_SQL\n"
                + "        expression: amount\n";
    }

    @Test
    void testFieldWithNoDatatypeFallsBackToTheSalesforceTypeForItsRole() throws Exception {
        // The semantic model API rejects a field that carries no dataType, so a field whose
        // Ossie datatype is absent has to fall back to the one type signal the document
        // still carries: whether Ossie modelled it as a dimension or as a measurement.
        List<String> results = converter.convert(ossieModelWithUntypedFields(null));
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        Map<String, Object> dataObject =
                ((List<Map<String, Object>>) sfModel.get("semanticDataObjects")).get(0);
        List<Map<String, Object>> dimensions =
                (List<Map<String, Object>>) dataObject.get("semanticDimensions");
        List<Map<String, Object>> measurements =
                (List<Map<String, Object>>) dataObject.get("semanticMeasurements");

        assertEquals("Text", dimensions.get(0).get("dataType"),
                "a dimension with no Ossie datatype should still carry a dataType");
        assertEquals("Number", measurements.get(0).get("dataType"),
                "a measurement with no Ossie datatype should still carry a dataType");
    }

    @Test
    void testFieldDatatypeWithNoSalesforceMappingFallsBackToTheRoleDefault() throws Exception {
        // Time is a portable Ossie datatype with no Salesforce equivalent. Dropping the
        // dataType would leave the API with the same field it rejects.
        List<String> results = converter.convert(ossieModelWithUntypedFields("Time"));
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        Map<String, Object> dataObject =
                ((List<Map<String, Object>>) sfModel.get("semanticDataObjects")).get(0);
        List<Map<String, Object>> dimensions =
                (List<Map<String, Object>>) dataObject.get("semanticDimensions");

        assertEquals("Text", dimensions.get(0).get("dataType"),
                "a datatype with no safe Salesforce mapping should fall back, not be dropped");
    }

    /**
     * Builds a model whose Customers dataset carries a primary key and joins Orders to it
     * on that key column. A Snowflake semantic view names a key column in
     * {@code primary_key} and nowhere else, which is the case this fixture covers unless
     * {@code declareTheKeyAsAFieldToo} also puts it among the fields.
     */
    private static String ossieModelWithAPrimaryKey(boolean declareTheKeyAsAFieldToo) {
        return "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + "- name: Customers\n"
                + "  source: Customers__dll\n"
                + "  primary_key:\n"
                + "  - customer_id\n"
                + "  fields:\n"
                + (declareTheKeyAsAFieldToo ? ossieDimension("customer_id") : "")
                + ossieDimension("name")
                + "- name: Orders\n"
                + "  source: Orders__dll\n"
                + "  fields:\n"
                + ossieDimension("customer_id")
                + "relationships:\n"
                + "- name: Orders_Customers\n"
                + "  from: Orders\n"
                + "  to: Customers\n"
                + "  from_columns: [customer_id]\n"
                + "  to_columns: [customer_id]\n";
    }

    private static String ossieDimension(String name) {
        return "  - name: " + name + "\n"
                + "    dimension: {}\n"
                + "    expression:\n"
                + "      dialects:\n"
                + "      - dialect: ANSI_SQL\n"
                + "        expression: " + name + "\n";
    }

    private static List<Map<String, Object>> dimensionsOf(Map<String, Object> sfModel, String apiName) {
        Map<String, Object> dataObject = ((List<Map<String, Object>>) sfModel.get("semanticDataObjects")).stream()
                .filter(d -> apiName.equals(d.get("apiName")))
                .findFirst()
                .orElseThrow();
        return (List<Map<String, Object>>) dataObject.get("semanticDimensions");
    }

    @Test
    void testPrimaryKeyColumnIsExportedSoRelationshipsCanJoinOnIt() throws Exception {
        // Salesforce resolves a join criterion against the declared semantic fields, not
        // against the table's columns, so a key column that reaches export only through
        // primary_key takes every relationship joining on it down with it.
        List<String> results = converter.convert(ossieModelWithAPrimaryKey(false));
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        assertTrue(dimensionsOf(sfModel, "Customers").stream()
                        .anyMatch(d -> "customer_id".equals(d.get("apiName"))),
                "a primary key column should be exported as a dimension");
        assertEquals(1, ((List<Map<String, Object>>) sfModel.get("semanticRelationships")).size(),
                "the relationship joining on the primary key should survive export");
    }

    @Test
    void testPrimaryKeyColumnAlreadyDeclaredAsAFieldIsExportedOnce() throws Exception {
        // A document is free to declare the key column as a field as well, and the field
        // carries the description and datatype the primary key entry has no room for.
        List<String> results = converter.convert(ossieModelWithAPrimaryKey(true));
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        assertEquals(1, dimensionsOf(sfModel, "Customers").stream()
                        .filter(d -> "customer_id".equals(d.get("apiName")))
                        .count(),
                "a primary key column already declared as a field should not be exported twice");
    }

    @Test
    void testTheFallbackDataTypeIsNamedInTheWarning() throws Exception {
        // The fallback can change the field's meaning -- a Time measurement ships as
        // Number -- so the warning has to say which dataType was chosen, not just that
        // one was. Omitting it instead is not an option: the API rejects such a field.
        String ossieModel = "version: 0.2.0.dev0\n"
                + "name: Imported_Model\n"
                + "datasets:\n"
                + "- name: Orders\n"
                + "  source: Orders__dll\n"
                + "  fields:\n"
                + "  - name: handling_time\n"
                + "    datatype: Time\n"
                + "    expression:\n"
                + "      dialects:\n"
                + "      - dialect: ANSI_SQL\n"
                + "        expression: handling_time\n";

        Logger fieldLogger = (Logger) LoggerFactory.getLogger(FieldMappingHandler.class);
        ListAppender<ILoggingEvent> warnings = new ListAppender<>();
        warnings.start();
        fieldLogger.addAppender(warnings);
        try {
            converter.convert(ossieModel);
        } finally {
            fieldLogger.detachAppender(warnings);
        }

        String reported = warnings.list.stream()
                .filter(event -> event.getLevel() == Level.WARN)
                .map(ILoggingEvent::getFormattedMessage)
                .filter(message -> message.contains("handling_time"))
                .findFirst()
                .orElse("");
        assertTrue(reported.contains("Number"),
                "the warning should name the dataType the field was exported as: " + reported);
    }

    @Test
    void testDataObjectTypeIsDerivedFromTheDataObjectNameSuffix() throws Exception {
        // Data Cloud suffixes a data object's name with the kind of object it is, so the
        // dataset's source says which reference type the API expects. A name with neither
        // suffix came from outside Data Cloud and lands in a data lake object once ingested.
        List<String> results = converter.convert(ossieModelWithoutSalesforceExtensions(
                "Orders__dll", "Orders__dlm", "ANALYTICS.PUBLIC.ORDERS"));
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        assertEquals(3, dataObjects.size());
        assertEquals("Dlo", dataObjects.get(0).get("dataObjectType"),
                "a __dll name is a data lake object");
        assertEquals("Dmo", dataObjects.get(1).get("dataObjectType"),
                "a __dlm name is a data model object");
        assertEquals("Dlo", dataObjects.get(2).get("dataObjectType"),
                "a name from outside Data Cloud should default to a data lake object");
    }

    @Test
    void testDataObjectTypeFromCustomExtensionsWinsOverTheSuffix() throws Exception {
        // Normalize line endings first: the fixture file may check out with CRLF depending on
        // the platform's autocrlf setting, but the substitution below is written with LF.
        // The Customers dataset keeps its __dll source but declares Dmo, so the suffix rule
        // and the restored value disagree and the restored value has to win.
        String yamlWithDmoExtension = ossieYaml.replace("\r\n", "\n")
                .replace("        \"label\" : \"Customers\",\n"
                        + "        \"dataObjectType\" : \"Dlo\"\n",
                        "        \"label\" : \"Customers\",\n"
                        + "        \"dataObjectType\" : \"Dmo\"\n");
        assertTrue(yamlWithDmoExtension.contains("\"dataObjectType\" : \"Dmo\""),
                "fixture text substitution did not match");

        List<String> results = converter.convert(yamlWithDmoExtension);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> dataObjects = (List<Map<String, Object>>) sfModel.get("semanticDataObjects");
        Map<String, Object> customers = dataObjects.stream()
                .filter(d -> "Customers".equals(d.get("apiName")))
                .findFirst()
                .orElse(null);
        assertNotNull(customers);
        assertEquals("Customers__dll", customers.get("dataObjectName"));
        assertEquals("Dmo", customers.get("dataObjectType"),
                "a dataObjectType restored from custom_extensions should win over the suffix default");
    }

    @Test
    void testDirectionConfigFileExtension() {
        // Test that getFileExtension() correctly derives from outputFormat
        DirectionConfig jsonConfig =
            new DirectionConfig();
        jsonConfig.setOutputFormat("json");
        assertEquals(".json", jsonConfig.getFileExtension());

        DirectionConfig yamlConfig =
            new DirectionConfig();
        yamlConfig.setOutputFormat("yaml");
        assertEquals(".yaml", yamlConfig.getFileExtension());
    }

    @Test
    void testPipelineConfigLoaderConstructor() {
        // Test that PipelineConfigLoader can be instantiated
        PipelineConfigLoader loader =
            new PipelineConfigLoader();
        assertNotNull(loader);
    }

}
