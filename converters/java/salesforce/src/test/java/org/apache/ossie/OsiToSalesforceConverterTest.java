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

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;
import org.apache.ossie.converter.CustomExtensionHandler;
import org.apache.ossie.validator.SchemaValidator;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.apache.ossie.converter.pipeline.DirectionConfig;
import org.apache.ossie.converter.pipeline.HandlerFactory;
import org.apache.ossie.converter.pipeline.PipelineConfig;
import org.apache.ossie.converter.pipeline.PipelineConfigLoader;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Comprehensive integration test for Ossie → Salesforce conversion.
 * Uses real example file: osiToSalesforce.yaml
 */
class OsiToSalesforceConverterTest {

    private static boolean salesforceSchemaExists;
    private static boolean osiSchemaExists;
    private static boolean warningPrinted = false;

    private Converter converter;
    private ObjectMapper jsonMapper;
    private String osiYaml;
    private String osiYamlAnsiSql;

    @BeforeAll
    static void checkSchemaAvailability() {
        salesforceSchemaExists = OsiToSalesforceConverterTest.class
                .getResourceAsStream(SchemaValidator.SALESFORCE_SCHEMA_PATH) != null;
        osiSchemaExists = OsiToSalesforceConverterTest.class
                .getResourceAsStream(SchemaValidator.OSI_SCHEMA_PATH) != null;

        if (!warningPrinted) {
            if (!salesforceSchemaExists) {
                System.err.println("\n  WARNING: Salesforce schema not found at " + SchemaValidator.SALESFORCE_SCHEMA_PATH);
                System.err.println("  Some tests in OsiToSalesforceConverterTest will be skipped.");
                System.err.println("  To run all tests, download the schema from:");
                System.err.println("  https://developer.salesforce.com/docs/data/semantic-layer/guide/salesforce-semantic-model-schema.html");
                System.err.println("  and save it to: src/main/resources/schemas/salesforce-semantic-model-schema.json\n");
            }
            if (!osiSchemaExists) {
                System.err.println("\n  WARNING: Ossie schema not found at " + SchemaValidator.OSI_SCHEMA_PATH);
                System.err.println("  Skipping OsiToSalesforceConverterTest tests.");
                System.err.println("  To run these tests, download the schema from:");
                System.err.println("  https://github.com/apache/ossie/blob/main/core-spec/osi-schema.json");
                System.err.println("  and save it to: src/main/resources/schemas/osi-schema.json\n");
            }
            warningPrinted = true;
        }
    }

    @BeforeEach
    void setUp() throws IOException {
        assumeTrue(osiSchemaExists, "Ossie schema file is required but not found. See README for setup instructions.");

        converter = ConverterFactory.getConverter(ConversionDirection.OSI_TO_SALESFORCE);
        jsonMapper = new ObjectMapper();
        osiYamlAnsiSql = Files.readString(Paths.get("src/test/resources/examples/osiToSalesforce.yaml"));
        osiYaml = osiYamlAnsiSql;
    }

    @Test
    void testCompleteConversion() throws Exception {
        List<String> results = converter.convert(osiYaml);

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
        List<String> results = converter.convert(osiYaml);
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
        List<String> results = converter.convert(osiYaml);
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
        List<String> results = converter.convert(osiYaml);
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
        List<String> results = converter.convert(osiYaml);
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
        List<String> ansiResults = converter.convert(osiYamlAnsiSql);
        Map<String, Object> ansiModel = jsonMapper.readValue(ansiResults.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> ansiCalcDimensions = (List<Map<String, Object>>) ansiModel.get("semanticCalculatedDimensions");
        assertNull(ansiCalcDimensions, "ANSI_SQL dialect: no semanticCalculatedDimensions");
    }

    @Test
    void testInvalidRelationshipsFiltered() throws Exception {
        List<String> results = converter.convert(osiYaml);
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

    @Test
    void testCustomExtensionsRestoration() throws Exception {
        List<String> results = converter.convert(osiYaml);
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
    void testMetricsNotConvertedInOsiToSalesforce() throws Exception {
        List<String> results = converter.convert(osiYaml);
        Map<String, Object> sfModel = jsonMapper.readValue(results.get(0), new TypeReference<Map<String, Object>>() {});

        List<Map<String, Object>> calcMeasurements = (List<Map<String, Object>>) sfModel.get("semanticCalculatedMeasurements");
        assertNull(calcMeasurements, "Metrics from Ossie are not converted to semanticCalculatedMeasurements in Ossie->SF direction");
    }

    @Test
    void testTimeDimensionConversion() throws Exception {
        List<String> results = converter.convert(osiYaml);
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

        List<String> results = converter.convert(osiYaml);
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
        assertTrue(config.getPipelines().containsKey("osiToSalesforce"));
        assertTrue(config.getPipelines().containsKey("salesforceToOsi"));

        // Verify handler list for osiToSalesforce
        List<String> handlers = config.getPipelines().get("osiToSalesforce");
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
            config.getDirectionConfigs().get("osiToSalesforce");
        assertNotNull(dirConfig);
        assertEquals("yaml", dirConfig.getInputFormat());
        assertEquals("json", dirConfig.getOutputFormat());
        assertEquals("/schemas/osi-schema.json", dirConfig.getSchemaPath());
        assertEquals("apiName", dirConfig.getExtractModelNameFrom());
    }

    @Test
    void testHandlerFactoryCreatesAllHandlers() {
        // Verify HandlerFactory can create all configured handlers
        CustomExtensionHandler customExtensionHandler =
            new CustomExtensionHandler(jsonMapper);
        HandlerFactory factory =
            new HandlerFactory(customExtensionHandler);

        ConversionDirection direction = ConversionDirection.OSI_TO_SALESFORCE;

        assertDoesNotThrow(() -> factory.createHandler("DatasetMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("FieldMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("RelationshipMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("MetricMappingHandler", direction));
        assertDoesNotThrow(() -> factory.createHandler("SemanticModelMappingHandler", direction));
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
