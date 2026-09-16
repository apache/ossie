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
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.converter.ConversionDirection;
import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.SalesforceBindings;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.exception.ValidationException;
import org.apache.ossie.validator.SchemaValidator;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/** Exercises metric compilation through the public converter and target schema. */
class MetricExportIntegrationTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final ObjectMapper YAML = new ObjectMapper(new YAMLFactory());
    private static boolean salesforceSchemaExists;
    private static boolean ossieSchemaExists;

    private Converter converter;

    @TempDir
    Path temporaryDirectory;

    @BeforeAll
    static void checkSchemaAvailability() {
        salesforceSchemaExists = MetricExportIntegrationTest.class
                .getResource(SchemaValidator.SALESFORCE_SCHEMA_PATH) != null;
        ossieSchemaExists = MetricExportIntegrationTest.class
                .getResource(SchemaValidator.OSSIE_SCHEMA_PATH) != null;
        if (Boolean.getBoolean("requireSalesforceSchema")) {
            assertTrue(salesforceSchemaExists,
                    "-DrequireSalesforceSchema=true requires the Salesforce schema; see README setup instructions");
        }
    }

    @BeforeEach
    void setUp() {
        assumeTrue(salesforceSchemaExists, "Salesforce schema is required; see README setup instructions");
        assumeTrue(ossieSchemaExists, "Ossie schema is required; see README setup instructions");
        converter = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void translatesComposedMetricsThroughStringApi(String dialect) throws Exception {
        Map<String, Object> output = convertOne(model("sales", List.of(
                metric("margin", dialect, "SUM(orders.profit) / NULLIF(SUM(orders.revenue), 0)"),
                metric("adjusted_average", dialect,
                        "ROUND(AVG(CASE WHEN orders.revenue IS NOT NULL AND NOT orders.profit < 0 "
                                + "THEN COALESCE(orders.profit, 0) ELSE 0 END), 2)"),
                metric("customers", dialect, "COUNT(DISTINCT orders.customer_id)"))));

        List<Map<String, Object>> measurements = measurements(output);
        assertEquals(List.of("margin", "adjusted_average", "customers"),
                measurements.stream().map(item -> item.get("apiName")).toList());
        for (Map<String, Object> measurement : measurements) {
            assertMeasurementMetadata(measurement);
        }
        assertEquals("(SUM([orders].[profit]) / (IF (SUM([orders].[revenue]) = 0) "
                        + "THEN NULL ELSE SUM([orders].[revenue]) END))",
                measurements.get(0).get("expression"));
        String conditional = (String) measurements.get(1).get("expression");
        assertTrue(conditional.startsWith("ROUND(AVG((IF "), conditional);
        assertTrue(conditional.contains("ISNULL([orders].[revenue])"), conditional);
        assertTrue(conditional.contains("IFNULL([orders].[profit], 0)"), conditional);
        assertFalse(conditional.contains("CASE"), conditional);
        assertEquals("COUNTD([orders].[customer_id])", measurements.get(2).get("expression"));

        Map<String, Object> dataset = items(output, "semanticDataObjects").get(0);
        Map<String, Object> profit = items(dataset, "semanticMeasurements").stream()
                .filter(item -> item.get("apiName").equals("profit")).findFirst().orElseThrow();
        assertEquals("profit__c", profit.get("dataObjectFieldName"));
        assertFalse(measurements.get(0).get("expression").toString().contains("profit__c"),
                "A metric binds semantic field names, not physical source columns");
    }

    @Test
    void fileApiWritesTheSameCompleteModelAsStringApi() throws Exception {
        String input = document(List.of(model("sales", List.of(
                metric("revenue", "SNOWFLAKE", "SUM(orders.revenue)")))));
        Path source = temporaryDirectory.resolve("input.yaml");
        Path outputDirectory = Files.createDirectory(temporaryDirectory.resolve("output"));
        Files.writeString(source, input);

        converter.convert(source, outputDirectory);

        assertEquals(JSON.readTree(converter.convert(input).get(0)),
                JSON.readTree(Files.readString(outputDirectory.resolve("sales.json"))));
        try (var files = Files.list(outputDirectory)) {
            assertEquals(List.of("sales.json"), files.map(path -> path.getFileName().toString()).toList());
        }
    }

    @Test
    void missingFieldFailsWithMetricNameAndDoesNotWriteAnyModelFiles() throws Exception {
        String input = document(List.of(
                model("valid", List.of(metric("revenue", "ANSI_SQL", "SUM(orders.revenue)"))),
                model("invalid", List.of(metric("broken_margin", "SNOWFLAKE", "SUM(orders.missing)")))));
        Path source = temporaryDirectory.resolve("input.yaml");
        Path outputDirectory = Files.createDirectory(temporaryDirectory.resolve("output"));
        Path existing = outputDirectory.resolve("existing.json");
        Files.writeString(source, input);
        Files.writeString(existing, "preserve this file");

        ConversionException error = assertThrows(ConversionException.class,
                () -> converter.convert(source, outputDirectory));

        assertTrue(error.getMessage().contains("broken_margin"), error.getMessage());
        assertTrue(error.getMessage().contains("orders.missing"), error.getMessage());
        assertTrue(error.getMessage().contains("declare"), error.getMessage());
        assertEquals("preserve this file", Files.readString(existing));
        try (var files = Files.list(outputDirectory)) {
            assertEquals(List.of("existing.json"), files.map(path -> path.getFileName().toString()).toList(),
                    "A failure in a later model must not leave an earlier model's output behind");
        }
    }

    @Test
    void selectedUnsupportedTableauExpressionDoesNotFallBackToSql() throws Exception {
        Map<String, Object> metric = metric("chosen_tableau", "TABLEAU", "BOGUS([orders].[profit])");
        metric.put("expression", Map.of("dialects", List.of(
                dialect("ANSI_SQL", "SUM(orders.profit)"),
                dialect("TABLEAU", "BOGUS([orders].[profit])"),
                dialect("SNOWFLAKE", "SUM(orders.profit)"))));
        String input = document(List.of(model("sales", List.of(metric))));

        ConversionException error = assertThrows(ConversionException.class, () -> converter.convert(input));

        assertTrue(error.getMessage().contains("chosen_tableau"), error.getMessage());
        assertTrue(error.getMessage().contains("BOGUS"), error.getMessage());
    }

    @Test
    void preservesSupportedTableauExpressionMeaningAndValidatesReferences() throws Exception {
        Map<String, Object> output = convertOne(model("sales", List.of(
                metric("revenue", "TABLEAU", "IFNULL(SUM([orders].[revenue]), 0)"))));

        Map<String, Object> measurement = measurements(output).get(0);
        assertEquals("IFNULL(SUM([orders].[revenue]), 0)", measurement.get("expression"));
        assertMeasurementMetadata(measurement);

        String invalid = document(List.of(model("sales", List.of(
                metric("unknown_tableau", "TABLEAU", "SUM([orders].[missing])")))));
        ConversionException error = assertThrows(ConversionException.class, () -> converter.convert(invalid));
        assertTrue(error.getMessage().contains("unknown_tableau"), error.getMessage());
        assertTrue(error.getMessage().contains("orders.missing"), error.getMessage());
    }

    @Test
    void modelWithoutMetricsRetainsDatasetExport() throws Exception {
        Map<String, Object> source = model("sales", List.of());
        source.remove("metrics");

        Map<String, Object> output = convertOne(source);

        assertTrue(measurements(output).isEmpty());
        assertEquals(3, items(items(output, "semanticDataObjects").get(0), "semanticMeasurements").size());
    }

    @Test
    void emptyMetricsRemainEmpty() throws Exception {
        Map<String, Object> output = convertOne(model("sales", List.of()));

        assertTrue(measurements(output).isEmpty());
        assertEquals("sales", output.get("apiName"));
    }

    @Test
    void metricNamesAndFieldTypesAreIsolatedAcrossSemanticModels() throws Exception {
        Map<String, Object> first = model("first", List.of(
                metric("value", "SNOWFLAKE", "SUM(orders.profit)")));
        Map<String, Object> second = model("second", List.of(
                metric("value", "ANSI_SQL", "COUNT(orders.profit)")));
        items(items(second, "datasets").get(0), "fields").get(0).put("datatype", "String");
        items(items(second, "datasets").get(0), "fields").get(0).put("dimension", Map.of("is_time", false));

        List<String> outputs = converter.convert(document(List.of(first, second)));

        assertEquals(2, outputs.size());
        assertEquals("SUM([orders].[profit])", measurements(parse(outputs.get(0))).get(0).get("expression"));
        assertEquals("COUNT([orders].[profit])", measurements(parse(outputs.get(1))).get(0).get("expression"));
        assertEquals("first", parse(outputs.get(0)).get("apiName"));
        assertEquals("second", parse(outputs.get(1)).get("apiName"));
    }

    @Test
    void fieldsFromAnotherSemanticModelCannotSatisfyAMetricReference() throws Exception {
        Map<String, Object> first = model("first", List.of(
                metric("valid", "ANSI_SQL", "SUM(orders.profit)")));
        Map<String, Object> second = model("second", List.of(
                metric("must_not_leak", "ANSI_SQL", "SUM(orders.profit)")));
        items(second, "datasets").get(0).put("fields", List.of(field("revenue", "Decimal")));
        String input = document(List.of(first, second));

        ConversionException error = assertThrows(ConversionException.class, () -> converter.convert(input));

        assertTrue(error.getMessage().contains("must_not_leak"), error.getMessage());
        assertTrue(error.getMessage().contains("orders.profit"), error.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {"profit__c + 1", "profit__c+1"})
    void derivedSqlFieldIsExportedAndAvailableToMetricsRegardlessOfWhitespace(String expression) throws Exception {
        Map<String, Object> source = model("sales", List.of(metric("adjusted_total", "ANSI_SQL", "SUM(orders.adjusted)")));
        Map<String, Object> calculated = field("adjusted", "Decimal");
        calculated.put("expression", Map.of("dialects", List.of(dialect("ANSI_SQL", expression))));
        items(source, "datasets").get(0).put("fields", List.of(field("profit", "Decimal"), calculated));
        Map<String, Object> output = convertOne(source);
        assertEquals(List.of("profit"), items(items(output, "semanticDataObjects").get(0),
                "semanticMeasurements").stream().map(item -> item.get("apiName")).toList());
        assertEquals(1, items(output, "semanticCalculatedDimensions").size());
        assertEquals("SUM(([orders].[profit] + 1))", measurements(output).get(0).get("expression"));
    }

    @Test
    void duplicateMetricNamesAreRejectedBeforeExport() throws Exception {
        String input = document(List.of(model("sales", List.of(
                metric("duplicated", "ANSI_SQL", "SUM(orders.profit)"),
                metric("duplicated", "ANSI_SQL", "SUM(orders.revenue)")))));

        ConversionException error = assertThrows(ConversionException.class, () -> converter.convert(input));

        assertTrue(error.getMessage().contains("duplicated"), error.getMessage());
        assertTrue(error.getMessage().contains("duplicate metric"), error.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {"AVG(orders.quantity)", "SUM(orders.quantity) / 2", "1.5"})
    void integerMetricDeclarationRejectsFractionalResult(String expression) throws Exception {
        Map<String, Object> metric = metric("integer_result", "ANSI_SQL", expression);
        metric.put("datatype", "Integer");
        String input = document(List.of(model("sales", List.of(metric))));

        ConversionException error = assertThrows(ConversionException.class, () -> converter.convert(input));

        assertTrue(error.getMessage().contains("integer_result"), error.getMessage());
        assertTrue(error.getMessage().contains("Integer"), error.getMessage());
        assertTrue(error.getMessage().contains("incompatible"), error.getMessage());
    }

    @Test
    void integerCountIsExportedAsSalesforceNumber() throws Exception {
        Map<String, Object> metric = metric("customer_count", "ANSI_SQL", "COUNT(orders.customer_id)");
        metric.put("datatype", "Integer");

        Map<String, Object> output = convertOne(model("sales", List.of(metric)));

        assertMeasurementMetadata(measurements(output).get(0));
        assertEquals("COUNT([orders].[customer_id])", measurements(output).get(0).get("expression"));
    }

    @Test
    void translatedComposedMetricsValidateAgainstTheSalesforceSchema() throws Exception {
        assumeTrue(salesforceSchemaExists, "Salesforce schema is required; see README setup instructions");
        Map<String, Object> output = convertOne(model("sales", List.of(
                metric("margin", "SNOWFLAKE", "SUM(orders.profit) / NULLIF(SUM(orders.revenue), 0)"),
                metric("customers", "ANSI_SQL", "COUNT(DISTINCT orders.customer_id)"),
                metric("rounding", "ANSI_SQL", "ROUND(AVG(ABS(orders.profit)), 2) + CEIL(1.2) - FLOOR(1.2)"))));
        SchemaValidator validator = new SchemaValidator(JSON, SchemaValidator.SALESFORCE_SCHEMA_PATH);

        for (Map<String, Object> metric : measurements(output)) {
            assertMeasurementMetadata(metric);
        }
        assertDoesNotThrow(() -> validator.validate(output));

        // OSI expression objects cannot be emitted where Salesforce requires a scalar formula.
        Map<String, Object> measurement = measurements(output).get(0);
        Object expression = measurement.put("expression", Map.of("dialects", List.of(
                dialect("ANSI_SQL", "SUM(orders.profit)"))));
        ValidationException error = assertThrows(ValidationException.class, () -> validator.validate(output));
        assertTrue(error.getMessage().contains("expression"), error.getMessage());
        measurement.put("expression", expression);
        measurement.put("dataType", "Integer");
        assertThrows(ValidationException.class, () -> validator.validate(output));
    }

    @Test
    void metricDependenciesResolveForwardReferencesAndRetainDeclarationOrder() throws Exception {
        Map<String, Object> output = convertOne(model("sales", List.of(
                metric("margin", "SNOWFLAKE", "total_profit / NULLIF(total_sales, 0)"),
                metric("total_profit", "ANSI_SQL", "SUM(orders.profit)"),
                metric("total_sales", "ANSI_SQL", "SUM(orders.revenue)"))));
        assertEquals(List.of("margin", "total_profit", "total_sales"), measurements(output).stream().map(m -> m.get("apiName")).toList());
        String formula = measurements(output).get(0).get("expression").toString();
        assertTrue(formula.contains("SUM([orders].[profit])"), formula);
        assertTrue(formula.contains("SUM([orders].[revenue])"), formula);
        assertFalse(formula.contains("total_sales"));
    }

    @Test
    void nativeMetricMetadataSurvivesWhileFormulaMetadataIsCompiled() throws Exception {
        Map<String, Object> metric = metric("money", "ANSI_SQL", "SUM(orders.profit)");
        metric.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                JSON.writeValueAsString(Map.of("label", "Profit in USD", "decimalPlace", 2, "dataType", "Currency",
                        "expression", "stale", "syntax", "Salesforce", "aggregationType", "Avg", "level", "Row")))));
        Map<String, Object> value = measurements(convertOne(model("sales", List.of(metric)))).get(0);
        assertEquals("Profit in USD", value.get("label"));
        assertEquals(2, value.get("decimalPlace"));
        assertEquals("Currency", value.get("dataType"));
        assertEquals("SUM([orders].[profit])", value.get("expression"));
        assertEquals("Tua", value.get("syntax"));
        assertEquals("UserAgg", value.get("aggregationType"));
        assertEquals("AggregateFunction", value.get("level"));
    }

    @Test
    void incompatibleNativeMetricTypeFails() throws Exception {
        Map<String, Object> metric = metric("money", "ANSI_SQL", "SUM(orders.profit)");
        metric.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data", "{\"dataType\":\"Text\"}")));
        var error = assertThrows(ConversionException.class, () -> convertOne(model("sales", List.of(metric))));
        assertTrue(error.getMessage().contains("incompatible Salesforce dataType Text"), error.getMessage());
    }

    @Test
    void outputSchemaIsEnforcedByPublicConversionAndWritesNothingOnFailure() throws Exception {
        Map<String, Object> source = model("sales", List.of());
        source.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data", "{\"dataspace\":42}")));
        Path input = temporaryDirectory.resolve("bad-output.yaml");
        Files.writeString(input, document(List.of(source)));
        Path output = Files.createDirectory(temporaryDirectory.resolve("output"));
        assertThrows(ValidationException.class, () -> converter.convert(input, output));
        try (var files = Files.list(output)) { assertEquals(0, files.count()); }
    }

    @Test
    void sameConverterCanRecoverAfterFailureWithoutLeakingDependencyState() throws Exception {
        Map<String, Object> bad = model("sales", List.of(metric("a", "ANSI_SQL", "b + 1"), metric("b", "ANSI_SQL", "a + 1")));
        assertThrows(ConversionException.class, () -> convertOne(bad));
        Map<String, Object> good = model("sales", List.of(metric("a", "ANSI_SQL", "b + 1"), metric("b", "ANSI_SQL", "SUM(orders.profit)")));
        assertEquals(convertOne(good), convertOne(good));
    }

    @Test
    void bindingsRetargetPhysicalObjectsWithoutChangingTheOsiDocumentOrDerivedReferences() throws Exception {
        Map<String, Object> source = model("sales", List.of(metric("total", "ANSI_SQL", "SUM(orders.adjusted)")));
        Map<String, Object> calculated = field("adjusted", "Decimal");
        calculated.put("expression", Map.of("dialects", List.of(dialect("SNOWFLAKE", "profit__c + 1"))));
        items(source, "datasets").get(0).put("fields", List.of(field("profit", "Decimal"), calculated));
        String document = document(List.of(source));
        SalesforceBindings bindings = SalesforceBindings.fromString("""
                models:
                  sales:
                    dataspace: analytics
                    datasets:
                      orders:
                        dataObjectName: OrdersProduction__dll
                        dataObjectType: Dlo
                        fields:
                          profit: NetProfit__c
                """);
        Converter bound = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE, bindings);
        Map<String, Object> output = parse(bound.convert(document).get(0));
        assertEquals("analytics", output.get("dataspace"));
        Map<String, Object> dataset = items(output, "semanticDataObjects").get(0);
        assertEquals("OrdersProduction__dll", dataset.get("dataObjectName"));
        assertEquals("NetProfit__c", items(dataset, "semanticMeasurements").get(0).get("dataObjectFieldName"));
        assertEquals("SUM(([orders].[profit] + 1))", measurements(output).get(0).get("expression"));
        assertEquals(document, document(List.of(source)));
        assertEquals(output, parse(bound.convert(document).get(0)));
    }

    @Test
    void unusedDirectFieldCannotSilentlyChangeDatatype() throws Exception {
        Map<String, Object> source = model("sales", List.of());
        items(items(source, "datasets").get(0), "fields").get(0).put("custom_extensions",
                List.of(Map.of("vendor_name", "SALESFORCE", "data", "{\"dataType\":\"Text\"}")));
        var error = assertThrows(ConversionException.class, () -> convertOne(source));
        assertTrue(error.getMessage().contains("conflicts"), error.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {"Time", "Opaque"})
    void unsupportedDirectDatatypeNeedsExplicitNativeMapping(String datatype) throws Exception {
        Map<String, Object> source = model("sales", List.of());
        items(items(source, "datasets").get(0), "fields").get(0).put("datatype", datatype);
        var error = assertThrows(ConversionException.class, () -> convertOne(source));
        assertTrue(error.getMessage().contains("no safe Salesforce mapping"), error.getMessage());
    }

    private Map<String, Object> convertOne(Map<String, Object> model) throws IOException {
        List<String> outputs = converter.convert(document(List.of(model)));
        assertEquals(1, outputs.size());
        return parse(outputs.get(0));
    }

    private static String document(List<Map<String, Object>> models) throws IOException {
        return YAML.writeValueAsString(Map.of("version", "0.2.0.dev0", "semantic_model", models));
    }

    private static Map<String, Object> parse(String json) throws IOException {
        return JSON.readValue(json, new TypeReference<>() {});
    }

    private static Map<String, Object> model(String name, List<Map<String, Object>> metrics) {
        Map<String, Object> model = new LinkedHashMap<>();
        model.put("name", name);
        model.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE",
                "data", "{\"dataspace\":\"default\"}")));
        Map<String, Object> dataset = new LinkedHashMap<>();
        dataset.put("name", "orders");
        dataset.put("source", "orders__dll");
        dataset.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE",
                "data", "{\"dataObjectType\":\"Dlo\"}")));
        dataset.put("fields", List.of(field("profit", "Decimal"), field("revenue", "Decimal"),
                field("quantity", "Integer"), field("customer_id", "String")));
        model.put("datasets", List.of(dataset));
        model.put("metrics", metrics);
        return model;
    }

    private static Map<String, Object> field(String name, String datatype) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("name", name);
        field.put("datatype", datatype);
        field.put("expression", Map.of("dialects", List.of(dialect("ANSI_SQL", name + "__c"))));
        if (datatype.equals("String")) {
            field.put("dimension", Map.of("is_time", false));
        }
        return field;
    }

    private static Map<String, Object> metric(String name, String dialect, String expression) {
        Map<String, Object> metric = new LinkedHashMap<>();
        metric.put("name", name);
        metric.put("datatype", "Decimal");
        metric.put("expression", Map.of("dialects", List.of(dialect(dialect, expression))));
        return metric;
    }

    private static Map<String, Object> dialect(String dialect, String expression) {
        return Map.of("dialect", dialect, "expression", expression);
    }

    private static void assertMeasurementMetadata(Map<String, Object> measurement) {
        assertInstanceOf(String.class, measurement.get("expression"));
        assertEquals("Tua", measurement.get("syntax"));
        assertEquals("UserAgg", measurement.get("aggregationType"));
        assertEquals("Number", measurement.get("dataType"));
    }

    private static List<Map<String, Object>> measurements(Map<String, Object> output) {
        return items(output, "semanticCalculatedMeasurements");
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> items(Map<String, Object> object, String key) {
        return (List<Map<String, Object>>) object.getOrDefault(key, List.of());
    }
}
