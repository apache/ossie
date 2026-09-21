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

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.converter.pipeline.PipelineConfig;
import org.apache.ossie.converter.pipeline.PipelineConfigLoader;
import org.apache.ossie.exception.ValidationException;
import org.apache.ossie.validator.SchemaValidator;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class FlatDocumentConversionTest {
    private final ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());

    @TempDir
    Path tempDirectory;

    @Test
    void convertsFlatOssieDocumentToOneSalesforceModel() throws Exception {
        ConverterImpl converter = new ConverterImpl(ConversionDirection.OSSIE_TO_SALESFORCE);
        String input = Files.readString(Path.of("src/test/resources/examples/ossieToSalesforce.yaml"));
        List<String> results = converter.convert(input);
        assertEquals(1, results.size());
        var result = new ObjectMapper().readTree(results.get(0));
        assertEquals("Customer_Orders_Model", result.path("apiName").asText());
        assertEquals(3, result.path("semanticDataObjects").size());
        assertFalse(result.has("version"));
        assertFalse(result.has("semantic_model"));
    }

    @Test
    void rejectsArrayAndObjectWrappers() throws Exception {
        Converter converter = new ConverterImpl(ConversionDirection.OSSIE_TO_SALESFORCE);
        Map<String, Object> model = Map.of("name", "legacy", "datasets", List.of());
        for (Object wrapper : List.of(model, List.of(model), List.of())) {
            String input = yamlMapper.writeValueAsString(Map.of(
                    "version", "0.2.0.dev0", "semantic_model", wrapper));
            assertThrows(ValidationException.class, () -> converter.convert(input));
        }
    }

    @Test
    void convertsSalesforceToFlatOssieAndExtractsName() throws Exception {
        ConverterImpl converter = salesforceImportConverter();
        String input = Files.readString(Path.of("src/test/resources/examples/salesforceToOssie.json"));
        List<String> results = converter.convert(input);
        assertEquals(1, results.size());
        Map<String, Object> document = yamlMapper.readValue(results.get(0), new TypeReference<>() {});
        assertEquals("0.2.0.dev0", document.get("version"));
        assertEquals("Customer_Orders_Model", document.get("name"));
        assertEquals(3, ((List<?>) document.get("datasets")).size());
        assertFalse(document.containsKey("semantic_model"));
        assertEquals("Customer_Orders_Model", converter.extractModelName(results.get(0)));
        new SchemaValidator(yamlMapper, SchemaValidator.OSSIE_SCHEMA_PATH).validate(document);
    }

    @Test
    void fileConversionUsesRootModelName() throws Exception {
        ConverterImpl converter = salesforceImportConverter();
        converter.convert(Path.of("src/test/resources/examples/salesforceToOssie.json"), tempDirectory);
        Path output = tempDirectory.resolve("Customer_Orders_Model.yaml");
        assertTrue(Files.isRegularFile(output));
        Map<String, Object> document = yamlMapper.readValue(Files.readString(output), new TypeReference<>() {});
        assertEquals("Customer_Orders_Model", document.get("name"));
        assertFalse(document.containsKey("semantic_model"));
    }

    private ConverterImpl salesforceImportConverter() {
        PipelineConfig config = PipelineConfigLoader.loadFromResource();
        // Test the complete conversion pipeline without requiring a manually downloaded
        // Salesforce schema. This fixture schema covers the example's input envelope;
        // the generated Ossie document is validated against the canonical core schema.
        config.getDirectionConfigs().get(ConversionDirection.SALESFORCE_TO_OSSIE.toPipelineKey())
                .setSchemaPath("/schemas/salesforce-input-fixture-schema.json");
        return new ConverterImpl(ConversionDirection.SALESFORCE_TO_OSSIE, config);
    }
}
