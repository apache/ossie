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

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.ossie.exception.ConversionException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

class CustomExtensionHandlerTest {
    private final ObjectMapper json = new ObjectMapper();
    private final CustomExtensionHandler handler = new CustomExtensionHandler(json);

    @ParameterizedTest
    @ValueSource(strings = {"{", "{} {}", "{} null", "{\"label\":\"a\",\"label\":\"b\"}",
            "{\"nested\":{\"x\":1,\"x\":2}}", "null", "[]", "42", "\"text\""})
    void rejectsMalformedAmbiguousOrNonObjectJsonWithoutWritingPartialProperties(String data) {
        Map<String, Object> target = new LinkedHashMap<>(Map.of("apiName", "owner"));
        ConversionException error = assertThrows(ConversionException.class,
                () -> handler.restoreSalesforceCustomExtension(target, item("owner", data)));
        assertTrue(error.getMessage().contains("owner"), error.getMessage());
        assertTrue(error.getMessage().contains("JSON object"), error.getMessage());
        assertEquals(Map.of("apiName", "owner"), target);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(ints = {1})
    void rejectsMissingOrNonStringData(Object data) {
        ConversionException error = assertThrows(ConversionException.class,
                () -> handler.restoreSalesforceCustomExtension(new LinkedHashMap<>(), item("owner", data)));
        assertTrue(error.getMessage().contains("owner"));
        assertTrue(error.getMessage().contains("encoded as a string"));
    }

    @Test
    void preservesNativePropertiesAndCorePrecedenceWithoutChangingSharedMapper() {
        Map<String, Object> target = new LinkedHashMap<>(Map.of("label", "core"));
        handler.restoreSalesforceCustomExtension(target,
                item("owner", "{\"label\":\"native\",\"description\":\"kept\",\"nested\":{\"x\":1}}"));
        assertEquals("core", target.get("label"));
        assertEquals("kept", target.get("description"));
        assertEquals(Map.of("x", 1), target.get("nested"));
        assertFalse(json.isEnabled(DeserializationFeature.FAIL_ON_TRAILING_TOKENS));
        assertFalse(json.isEnabled(JsonParser.Feature.STRICT_DUPLICATE_DETECTION));
    }

    @Test
    void ignoresOtherVendorData() {
        Map<String, Object> source = new LinkedHashMap<>(Map.of("name", "owner", "custom_extensions",
                List.of(Map.of("vendor_name", "SNOWFLAKE", "data", "not Salesforce JSON"))));
        Map<String, Object> target = new LinkedHashMap<>();
        assertDoesNotThrow(() -> handler.restoreSalesforceCustomExtension(target, source));
        assertTrue(target.isEmpty());
    }

    @ParameterizedTest
    @ValueSource(strings = {"model", "dataset", "field", "metric"})
    void publicConversionRejectsMalformedMetadataAtEveryOwnerLevel(String ownerKind) throws Exception {
        Map<String, Object> field = new LinkedHashMap<>(Map.of("name", "amount", "datatype", "Decimal",
                "expression", dialect("amount__c")));
        Map<String, Object> dataset = item("orders", "{\"dataObjectType\":\"Dlo\"}");
        dataset.put("source", "orders__dll");
        dataset.put("fields", List.of(field));
        Map<String, Object> metric = new LinkedHashMap<>(Map.of("name", "total", "datatype", "Decimal",
                "expression", dialect("SUM(orders.amount)")));
        Map<String, Object> model = item("sales", "{\"dataspace\":\"default\"}");
        model.put("datasets", List.of(dataset));
        model.put("metrics", List.of(metric));
        Map<String, Object> owner = switch (ownerKind) {
            case "model" -> model;
            case "dataset" -> dataset;
            case "field" -> field;
            default -> metric;
        };
        owner.put("custom_extensions", item("ignored", "{").get("custom_extensions"));
        String input = json.writeValueAsString(Map.of("version", "0.2.0.dev0", "semantic_model", List.of(model)));
        ConversionException error = assertThrows(ConversionException.class,
                () -> ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE).convert(input));
        assertTrue(error.getMessage().contains((String) owner.get("name")), error.getMessage());
        assertTrue(error.getMessage().contains("JSON"), error.getMessage());
    }

    private static Map<String, Object> dialect(String expression) {
        return Map.of("dialects", List.of(Map.of("dialect", "SNOWFLAKE", "expression", expression)));
    }

    private static Map<String, Object> item(String name, Object data) {
        Map<String, Object> extension = new LinkedHashMap<>();
        extension.put("vendor_name", "SALESFORCE");
        extension.put("data", data);
        return new LinkedHashMap<>(Map.of("name", name, "custom_extensions", List.of(extension)));
    }
}
