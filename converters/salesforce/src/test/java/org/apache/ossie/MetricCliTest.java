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

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.apache.ossie.app.OssieSalesforceConverter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class MetricCliTest {
    @TempDir
    Path directory;

    @Test
    void reportsMetricFailureToStderrWithoutWritingAModel() throws Exception {
        Path input = directory.resolve("input.yaml");
        Files.writeString(input, Files.readString(Path.of("src/test/resources/examples/ossieToSalesforce.yaml"))
                .replace("SUM([Orders].[amount])", "SUM([Orders].[missing])"));
        Path stderr = directory.resolve("stderr.txt");
        Process process = new ProcessBuilder(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp", System.getProperty("java.class.path"), OssieSalesforceConverter.class.getName(),
                "toSF", input.toString())
                .redirectError(stderr.toFile())
                .redirectOutput(directory.resolve("stdout.txt").toFile())
                .start();
        try {
            assertTrue(process.waitFor(30, TimeUnit.SECONDS), "CLI did not terminate");
            assertEquals(3, process.exitValue());
            String error = Files.readString(stderr);
            assertTrue(error.contains("Metric 'total_revenue'"), error);
            assertTrue(error.contains("Unknown field reference"), error);
            assertTrue(error.contains("missing"), error);
            assertFalse(Files.exists(directory.resolve("Customer_Orders_Model.json")));
        } finally {
            process.destroyForcibly();
        }
    }

    @Test
    void bindingsFlagWritesCompleteModelWithPhysicalOverrides() throws Exception {
        Path input = inputFixture();
        String before = Files.readString(input);
        Path bindings = write("bindings.yaml", """
                models:
                  Customer_Orders_Model:
                    dataspace: production
                    datasets:
                      Orders:
                        dataObjectName: OrdersProduction__dll
                        dataObjectType: Dlo
                        fields:
                          amount: NetRevenue__c
                """);
        CliResult result = run("toSF", input.toString(), "--bindings", bindings.toString());
        assertEquals(0, result.exitCode(), result.stderr());
        JsonNode output = new ObjectMapper().readTree(Files.readString(directory.resolve("Customer_Orders_Model.json")));
        assertEquals("production", output.get("dataspace").asText());
        JsonNode orders = find(output.get("semanticDataObjects"), "Orders");
        assertEquals("OrdersProduction__dll", orders.get("dataObjectName").asText());
        assertEquals("NetRevenue__c", find(orders.get("semanticMeasurements"), "amount").get("dataObjectFieldName").asText());
        assertEquals("SUM([Orders].[amount])", find(output.get("semanticCalculatedMeasurements"), "total_revenue").get("expression").asText());
        assertEquals(before, Files.readString(input));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "models: {}\nmodels: {}",
            "models: {Customer_Orders_Model: {expression: 'SUM(amount)'}}",
            "models: {}\n---\nmodels: {Customer_Orders_Model: {dataspace: hidden}}"
    })
    void invalidBindingsDocumentReportsInputErrorAndWritesNothing(String content) throws Exception {
        Path input = inputFixture();
        Path bindings = write("bindings.yaml", content);
        CliResult result = run("toSF", input.toString(), "--bindings", bindings.toString());
        assertEquals(2, result.exitCode(), result.stderr());
        assertTrue(result.stderr().contains("Invalid Salesforce bindings"), result.stderr());
        assertFalse(result.stderr().contains("Exception in thread"), result.stderr());
        assertFalse(Files.exists(directory.resolve("Customer_Orders_Model.json")));
    }

    @Test
    void schemaInvalidBindingReportsConversionErrorWithoutStackTrace() throws Exception {
        Path input = inputFixture();
        Path bindings = write("bindings.yaml", """
                models:
                  Customer_Orders_Model:
                    datasets:
                      Orders: {dataObjectType: NotANativeObjectType}
                """);
        CliResult result = run("toSF", input.toString(), "--bindings", bindings.toString());
        assertEquals(3, result.exitCode(), result.stderr());
        assertTrue(result.stderr().contains("dataObjectType"), result.stderr());
        assertTrue(result.stderr().startsWith("Error:"), result.stderr());
        assertFalse(result.stderr().contains("Exception in thread"), result.stderr());
        assertFalse(Files.exists(directory.resolve("Customer_Orders_Model.json")));
    }

    @Test
    void rejectsBindingsFlagInReverseDirectionBeforeReadingBindings() throws Exception {
        Path input = write("source.json", Files.readString(Path.of("src/test/resources/examples/salesforceToOssie.json")));
        CliResult result = run("toOssie", input.toString(), "--bindings", directory.resolve("nonexistent.yaml").toString());
        assertEquals(2, result.exitCode(), result.stderr());
        assertTrue(result.stderr().contains("Expected toSF"), result.stderr());
        try (var paths = Files.list(directory)) {
            assertFalse(paths.anyMatch(path -> path.toString().endsWith(".yaml")));
        }
    }

    @Test
    void rejectsMissingBindingsPathAndUnknownFlags() throws Exception {
        Path input = inputFixture();
        CliResult missingArgument = run("toSF", input.toString(), "--bindings");
        assertEquals(2, missingArgument.exitCode(), missingArgument.stderr());
        assertTrue(missingArgument.stderr().contains("Expected toSF"), missingArgument.stderr());
        CliResult missingFile = run("toSF", input.toString(), "--bindings", directory.resolve("missing.yaml").toString());
        assertEquals(2, missingFile.exitCode(), missingFile.stderr());
        assertTrue(missingFile.stderr().contains("Cannot read Salesforce bindings"), missingFile.stderr());
        CliResult unknown = run("toSF", input.toString(), "--mapping", "ignored");
        assertEquals(2, unknown.exitCode(), unknown.stderr());
        assertFalse(Files.exists(directory.resolve("Customer_Orders_Model.json")));
    }

    @Test
    void laterModelBindingFailurePreservesExistingOutputsAndWritesNoPartialModels() throws Exception {
        ObjectMapper yaml = new ObjectMapper(new YAMLFactory());
        Map<String, Object> document = yaml.readValue(Files.readString(
                Path.of("src/test/resources/examples/ossieToSalesforce.yaml")), new TypeReference<>() {});
        @SuppressWarnings("unchecked") Map<String, Object> first = (Map<String, Object>) ((List<?>) document.get("semantic_model")).get(0);
        Map<String, Object> second = new ObjectMapper().convertValue(first, new TypeReference<>() {});
        second.put("name", "Other_Model");
        document.put("semantic_model", List.of(first, second));
        Path input = write("input.yaml", yaml.writeValueAsString(document));
        String before = Files.readString(input);
        Path existing = write("Customer_Orders_Model.json", "preserve existing model");
        Path bindings = write("bindings.yaml", """
                models:
                  Other_Model:
                    datasets:
                      Orders:
                        fields: {missing: InvalidColumn__c}
                """);
        CliResult result = run("toSF", input.toString(), "--bindings", bindings.toString());
        assertEquals(3, result.exitCode(), result.stderr());
        assertTrue(result.stderr().contains("Other_Model"), result.stderr());
        assertTrue(result.stderr().contains("missing"), result.stderr());
        assertEquals("preserve existing model", Files.readString(existing));
        assertFalse(Files.exists(directory.resolve("Other_Model.json")));
        assertEquals(before, Files.readString(input));
    }

    private Path inputFixture() throws Exception {
        return write("input.yaml", Files.readString(Path.of("src/test/resources/examples/ossieToSalesforce.yaml")));
    }

    private Path write(String name, String content) throws Exception {
        Path path = directory.resolve(name);
        Files.writeString(path, content);
        return path;
    }

    private record CliResult(int exitCode, String stderr) {}

    private CliResult run(String... arguments) throws Exception {
        List<String> command = new ArrayList<>(List.of(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp", System.getProperty("java.class.path"), OssieSalesforceConverter.class.getName()));
        command.addAll(List.of(arguments));
        Path stderr = directory.resolve("stderr.txt");
        Process process = new ProcessBuilder(command).redirectError(stderr.toFile())
                .redirectOutput(directory.resolve("stdout.txt").toFile()).start();
        try {
            assertTrue(process.waitFor(30, TimeUnit.SECONDS), "CLI did not terminate");
            return new CliResult(process.exitValue(), Files.readString(stderr));
        } finally {
            process.destroyForcibly();
        }
    }

    private static JsonNode find(JsonNode items, String name) {
        for (JsonNode item : items) if (name.equals(item.path("apiName").asText())) return item;
        throw new AssertionError("Missing exported item " + name);
    }

}
