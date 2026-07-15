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

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;
import org.apache.ossie.converter.CustomExtensionHandler;
import org.apache.ossie.validator.SchemaValidator;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.apache.ossie.converter.pipeline.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Comprehensive integration test for Salesforce → Ossie conversion.
 * Uses real example file: salesforceToOsi.json
 */
class SalesforceToOsiConverterTest {

    private static boolean schemaExists;
    private static boolean osiSchemaExists;
    private static boolean warningPrinted = false;

    private Converter converter;
    private ObjectMapper yamlMapper;
    private String salesforceJson;

    @BeforeAll
    static void checkSchemaAvailability() {
        schemaExists = SalesforceToOsiConverterTest.class
                .getResourceAsStream(SchemaValidator.SALESFORCE_SCHEMA_PATH) != null;
        osiSchemaExists = SalesforceToOsiConverterTest.class
                .getResourceAsStream(SchemaValidator.OSI_SCHEMA_PATH) != null;

        if (!warningPrinted) {
            if (!schemaExists) {
                System.err.println("\n  WARNING: Salesforce schema not found at " + SchemaValidator.SALESFORCE_SCHEMA_PATH);
                System.err.println("  Skipping SalesforceToOsiConverterTest tests.");
                System.err.println("  To run these tests, download the schema from:");
                System.err.println("  https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html");
                System.err.println("  and save it to: src/main/resources/schemas/salesforce-semantic-model-schema.json\n");
            }
            if (!osiSchemaExists) {
                System.err.println("\n  WARNING: Ossie schema not found at " + SchemaValidator.OSI_SCHEMA_PATH);
                System.err.println("  Skipping SalesforceToOsiConverterTest tests.");
                System.err.println("  To run these tests, download the schema from:");
                System.err.println("  https://github.com/apache/ossie/blob/main/core-spec/osi-schema.json");
                System.err.println("  and save it to: src/main/resources/schemas/osi-schema.json\n");
            }
            warningPrinted = true;
        }
    }

    @BeforeEach
    void setUp() throws IOException {
        assumeTrue(schemaExists, "Salesforce schema file is required but not found. See README for setup instructions.");
        assumeTrue(osiSchemaExists, "Ossie schema file is required but not found. See README for setup instructions.");

        converter = ConverterFactory.getConverter(ConversionDirection.SALESFORCE_TO_OSI);
        yamlMapper = new ObjectMapper(new YAMLFactory());
        salesforceJson = Files.readString(Paths.get("src/test/resources/examples/salesforceToOsi.json"));
    }

    @Test
    void testCompleteConversion() throws Exception {
        List<String> results = converter.convert(salesforceJson);

        assertNotNull(results);
        assertEquals(1, results.size());

        String osiYaml = results.get(0);
        assertNotNull(osiYaml);
        assertTrue(osiYaml.contains("version: 0.2.0.dev0"));
        assertTrue(osiYaml.contains("semantic_model:"));

        Map<String, Object> osiRoot = yamlMapper.readValue(osiYaml, Map.class);
        assertNotNull(osiRoot);
        assertEquals("0.2.0.dev0", osiRoot.get("version"));

        List<Map<String, Object>> semanticModels = (List<Map<String, Object>>) osiRoot.get("semantic_model");
        assertNotNull(semanticModels);
        assertEquals(1, semanticModels.size());

        Map<String, Object> model = semanticModels.get(0);
        assertEquals("Customer_Orders_Model", model.get("name"));
        assertNotNull(model.get("description"));
    }

    @Test
    void testDatasetMapping() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);

        List<Map<String, Object>> datasets = (List<Map<String, Object>>) model.get("datasets");
        assertNotNull(datasets);
        assertEquals(3, datasets.size());

        // Verify dataset names
        assertEquals("Customers", datasets.get(0).get("name"));
        assertEquals("Orders", datasets.get(1).get("name"));
        assertEquals("Products", datasets.get(2).get("name"));

        // Verify source mapping
        assertEquals("Customers__dll", datasets.get(0).get("source"));
        assertEquals("Orders__dll", datasets.get(1).get("source"));
        assertEquals("Products__dll", datasets.get(2).get("source"));
    }

    @Test
    void testFieldMapping() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);
        List<Map<String, Object>> datasets = (List<Map<String, Object>>) model.get("datasets");

        Map<String, Object> customersDataset = datasets.get(0);
        List<Map<String, Object>> fields = (List<Map<String, Object>>) customersDataset.get("fields");
        assertNotNull(fields);
        assertTrue(fields.size() >= 4); // customer_id, email, total_purchases, lifetime_value

        // Check field structure
        Map<String, Object> customerIdField = fields.stream()
                .filter(f -> "customer_id".equals(f.get("name")))
                .findFirst()
                .orElse(null);
        assertNotNull(customerIdField);
        assertEquals("Customer ID", customerIdField.get("label"));
        assertNotNull(customerIdField.get("dimension"));
        assertNotNull(customerIdField.get("expression"));

        // Verify expression dialect structure
        Map<String, Object> expression = (Map<String, Object>) customerIdField.get("expression");
        List<Map<String, Object>> dialects = (List<Map<String, Object>>) expression.get("dialects");
        assertNotNull(dialects);
        assertEquals(1, dialects.size());
        assertEquals("TABLEAU", dialects.get(0).get("dialect"));
        assertEquals("customer_id__c", dialects.get(0).get("expression"));
    }

    @Test
    void testCalculatedDimensionConversion() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);
        List<Map<String, Object>> datasets = (List<Map<String, Object>>) model.get("datasets");

        // customer_email_domain should be converted to a field in Customers dataset (single dependency)
        Map<String, Object> customersDataset = datasets.get(0);
        List<Map<String, Object>> fields = (List<Map<String, Object>>) customersDataset.get("fields");
        boolean hasEmailDomain = fields.stream()
                .anyMatch(f -> "customer_email_domain".equals(f.get("name")));
        assertTrue(hasEmailDomain, "customer_email_domain should be converted to a field");

        // order_year should be converted to a field in Orders dataset (single dependency)
        Map<String, Object> ordersDataset = datasets.get(1);
        List<Map<String, Object>> orderFields = (List<Map<String, Object>>) ordersDataset.get("fields");
        boolean hasOrderYear = orderFields.stream()
                .anyMatch(f -> "order_year".equals(f.get("name")));
        assertTrue(hasOrderYear, "order_year should be converted to a field");

        // customer_order_key should remain in custom_extensions (multiple dependencies)
        List<Map<String, Object>> customExtensions = (List<Map<String, Object>>) model.get("custom_extensions");
        assertNotNull(customExtensions);
        Map<String, Object> sfExtension = customExtensions.stream()
                .filter(ext -> "SALESFORCE".equals(ext.get("vendor_name")))
                .findFirst()
                .orElse(null);
        assertNotNull(sfExtension);
        String data = (String) sfExtension.get("data");
        assertTrue(data.contains("customer_order_key"), "customer_order_key should be in custom_extensions");
    }

    @Test
    void testRelationshipMapping() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);

        List<Map<String, Object>> relationships = (List<Map<String, Object>>) model.get("relationships");
        assertNotNull(relationships);
        assertEquals(2, relationships.size());

        // Verify first relationship
        Map<String, Object> rel1 = relationships.get(0);
        assertEquals("Customers_Orders_TableField", rel1.get("name"));
        assertEquals("Customers", rel1.get("from"));
        assertEquals("Orders", rel1.get("to"));

        List<String> fromColumns = (List<String>) rel1.get("from_columns");
        List<String> toColumns = (List<String>) rel1.get("to_columns");
        assertNotNull(fromColumns);
        assertNotNull(toColumns);
        assertEquals(1, fromColumns.size());
        assertEquals(1, toColumns.size());
        assertEquals("customer_id", fromColumns.get(0));
        assertEquals("order_id", toColumns.get(0));
    }

    @Test
    void testUnsupportedRelationshipsInCustomExtensions() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);

        // Unsupported relationships (Formula/SemanticField) should be in custom_extensions
        List<Map<String, Object>> customExtensions = (List<Map<String, Object>>) model.get("custom_extensions");
        assertNotNull(customExtensions);

        Map<String, Object> sfExtension = customExtensions.stream()
                .filter(ext -> "SALESFORCE".equals(ext.get("vendor_name")))
                .findFirst()
                .orElse(null);
        assertNotNull(sfExtension);

        String data = (String) sfExtension.get("data");
        assertTrue(data.contains("semanticRelationships"));
        assertTrue(data.contains("Customers_Products_Formula"));
    }

    @Test
    void testMetricMapping() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);

        List<Map<String, Object>> metrics = (List<Map<String, Object>>) model.get("metrics");
        assertNotNull(metrics);
        assertEquals(2, metrics.size());

        // Verify first metric
        Map<String, Object> metric1 = metrics.get(0);
        assertEquals("total_revenue", metric1.get("name"));
        assertNotNull(metric1.get("expression"));

        Map<String, Object> expression = (Map<String, Object>) metric1.get("expression");
        List<Map<String, Object>> dialects = (List<Map<String, Object>>) expression.get("dialects");
        assertNotNull(dialects);
        assertEquals("TABLEAU", dialects.get(0).get("dialect"));
        assertEquals("SUM([Orders].[amount])", dialects.get(0).get("expression"));
    }

    @Test
    void testCustomExtensionsPreservation() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);

        // Check model-level custom_extensions
        List<Map<String, Object>> customExtensions = (List<Map<String, Object>>) model.get("custom_extensions");
        assertNotNull(customExtensions);
        assertTrue(customExtensions.size() > 0);

        Map<String, Object> sfExtension = customExtensions.stream()
                .filter(ext -> "SALESFORCE".equals(ext.get("vendor_name")))
                .findFirst()
                .orElse(null);
        assertNotNull(sfExtension);
        assertNotNull(sfExtension.get("data"));

        // Verify Salesforce-specific properties are preserved
        String data = (String) sfExtension.get("data");
        assertTrue(data.contains("label"));
        assertTrue(data.contains("dataspace"));
    }

    @Test
    void testTimeDimensionMapping() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        Map<String, Object> osiRoot = yamlMapper.readValue(results.get(0), Map.class);
        Map<String, Object> model = ((List<Map<String, Object>>) osiRoot.get("semantic_model")).get(0);
        List<Map<String, Object>> datasets = (List<Map<String, Object>>) model.get("datasets");

        Map<String, Object> ordersDataset = datasets.get(1);
        List<Map<String, Object>> fields = (List<Map<String, Object>>) ordersDataset.get("fields");

        Map<String, Object> orderDateField = fields.stream()
                .filter(f -> "order_date".equals(f.get("name")))
                .findFirst()
                .orElse(null);
        assertNotNull(orderDateField);

        Map<String, Object> dimension = (Map<String, Object>) orderDateField.get("dimension");
        assertNotNull(dimension);
        assertTrue((Boolean) dimension.get("is_time"));
    }

    @Test
    void testOutputCompilesWithOsiSchema() throws Exception {
        List<String> results = converter.convert(salesforceJson);
        String osiYaml = results.get(0);

        Map<String, Object> osiRoot = yamlMapper.readValue(osiYaml, Map.class);

        SchemaValidator validator = new SchemaValidator(new ObjectMapper(), SchemaValidator.OSI_SCHEMA_PATH);
        assertDoesNotThrow(() -> validator.validate(osiRoot), "Output should comply with Ossie schema");
    }

    @Test
    void testPipelineConfigForSalesforceToOsi() {
        // Verify pipeline configuration for salesforceToOsi direction
        PipelineConfig config =
            PipelineConfigLoader.loadFromResource();

        // Verify handler list for salesforceToOsi
        List<String> handlers = config.getPipelines().get("salesforceToOsi");
        assertNotNull(handlers);
        assertEquals(5, handlers.size());
        assertTrue(handlers.contains("DatasetMappingHandler"));
        assertTrue(handlers.contains("FieldMappingHandler"));
        assertTrue(handlers.contains("RelationshipMappingHandler"));
        assertTrue(handlers.contains("MetricMappingHandler"));
        assertTrue(handlers.contains("SemanticModelMappingHandler"));

        // Verify direction config for salesforceToOsi
        DirectionConfig dirConfig =
            config.getDirectionConfigs().get("salesforceToOsi");
        assertNotNull(dirConfig);
        assertEquals("json", dirConfig.getInputFormat());
        assertEquals("yaml", dirConfig.getOutputFormat());
        assertEquals("/schemas/salesforce-semantic-model-schema.json", dirConfig.getSchemaPath());
        assertEquals("name", dirConfig.getExtractModelNameFrom());
        assertEquals(".yaml", dirConfig.getFileExtension());
    }

    @Test
    void testHandlerFactoryForSalesforceToOsi() {
        // Verify HandlerFactory creates handlers for salesforceToOsi direction
        ObjectMapper testJsonMapper = new ObjectMapper();
        CustomExtensionHandler customExtensionHandler =
            new CustomExtensionHandler(testJsonMapper);
        HandlerFactory factory =
            new HandlerFactory(customExtensionHandler);

        ConversionDirection direction = ConversionDirection.SALESFORCE_TO_OSI;

        // Verify all handlers can be instantiated
        PipelineStep datasetHandler =
            factory.createHandler("DatasetMappingHandler", direction);
        assertNotNull(datasetHandler);

        PipelineStep fieldHandler =
            factory.createHandler("FieldMappingHandler", direction);
        assertNotNull(fieldHandler);

        PipelineStep relationshipHandler =
            factory.createHandler("RelationshipMappingHandler", direction);
        assertNotNull(relationshipHandler);

        PipelineStep metricHandler =
            factory.createHandler("MetricMappingHandler", direction);
        assertNotNull(metricHandler);

        PipelineStep semanticModelHandler =
            factory.createHandler("SemanticModelMappingHandler", direction);
        assertNotNull(semanticModelHandler);
    }

    @Test
    void testConverterImplExtractModelNameFromOsiFormat() throws Exception {
        // Test extractModelName specifically handles Ossie wrapped format
        List<String> results = converter.convert(salesforceJson);
        String osiYaml = results.get(0);

        // The result is wrapped Ossie format - extractModelName should handle this
        Map<String, Object> osiRoot = yamlMapper.readValue(osiYaml, Map.class);
        assertTrue(osiRoot.containsKey("semantic_model"));

        // Verify it's properly wrapped
        List<Map<String, Object>> models = (List<Map<String, Object>>) osiRoot.get("semantic_model");
        assertNotNull(models);
        assertEquals(1, models.size());
        assertEquals("Customer_Orders_Model", models.get(0).get("name"));
    }
}
