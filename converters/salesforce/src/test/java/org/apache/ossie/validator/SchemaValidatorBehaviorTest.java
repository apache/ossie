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

package org.apache.ossie.validator;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import java.io.InputStream;
import java.util.Map;
import java.util.stream.Stream;
import org.apache.ossie.exception.ValidationException;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.junit.jupiter.api.Assertions.*;

class SchemaValidatorBehaviorTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static JsonSchema offlineSchema;

    @BeforeAll
    static void loadOnlyPackagedRootSchema() throws Exception {
        try (InputStream input = SchemaValidatorBehaviorTest.class.getResourceAsStream(
                SchemaValidator.OSSIE_SCHEMA_PATH)) {
            assertNotNull(input);
            // Keep the production factory's default draft; the explicit $schema
            // selects 2020-12. Any external resource load fails immediately.
            JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V7,
                    builder -> builder.schemaLoaders(loaders -> loaders.add(iri -> {
                        throw new AssertionError("Unexpected schema resource load: " + iri);
                    })));
            offlineSchema = factory.getSchema(MAPPER.readTree(input));
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"actions", "action_types", "both"})
    void acceptsAliasesAndVendorExtensionsOffline(String alias) throws Exception {
        ObjectNode document = document();
        ObjectNode behavior = (ObjectNode) document.get("behavior");
        if (alias.equals("action_types")) {
            behavior.set(alias, behavior.remove("actions"));
        } else if (alias.equals("both")) {
            behavior.set("action_types", behavior.get("actions").deepCopy());
        }
        assertValid(document);
    }

    @ParameterizedTest
    @ValueSource(strings = {"actions", "action_types", "both"})
    void acceptsEmptyListsOffline(String alias) throws Exception {
        ObjectNode document = document();
        ObjectNode behavior = (ObjectNode) document.get("behavior");
        behavior.remove("actions");
        behavior.putArray(alias.equals("both") ? "actions" : alias);
        if (alias.equals("both")) {
            behavior.putArray("action_types");
        }
        behavior.putArray("rules");
        assertValid(document);
    }

    @Test
    void behaviorRemainsOptionalOffline() throws Exception {
        ObjectNode document = document();
        document.remove("behavior");
        assertValid(document);
    }

    @ParameterizedTest
    @MethodSource("invalidValues")
    void rejectsInvalidBehaviorOffline(String pointer, Object value) throws Exception {
        ObjectNode document = document();
        ObjectNode target = (ObjectNode) document.at(pointer.substring(0, pointer.lastIndexOf('/')));
        target.set(pointer.substring(pointer.lastIndexOf('/') + 1), MAPPER.valueToTree(value));
        assertInvalid(document);
    }

    static Stream<Arguments> invalidValues() {
        return Stream.of(
                Arguments.of("/behavior/namespace", ""),
                Arguments.of("/behavior/namespace", 1),
                Arguments.of("/behavior/behavior_layer_version", ""),
                Arguments.of("/behavior/behavior_layer_version", 1),
                Arguments.of("/behavior/actions", Map.of()),
                Arguments.of("/behavior/rules", "invalid"),
                Arguments.of("/behavior/actions/0/id", ""),
                Arguments.of("/behavior/actions/0/kind", "invalid"),
                Arguments.of("/behavior/actions/0/effects/0/entity", "invalid"),
                Arguments.of("/behavior/actions/0/effects/0/mode", "invalid"),
                Arguments.of("/behavior/actions/0/effects/0/impact_type", "invalid"),
                Arguments.of("/behavior/rules/0/severity", "invalid"),
                Arguments.of("/behavior/rules/0/when", "invalid"),
                Arguments.of("/behavior/rules/0/constraint", "invalid"),
                Arguments.of("/behavior/rules/0/message", ""));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "/behavior/namespace", "/behavior/behavior_layer_version", "/behavior/actions",
            "/behavior/rules", "/behavior/actions/0/id", "/behavior/actions/0/title",
            "/behavior/actions/0/effects/0/entity", "/behavior/actions/0/effects/0/mode",
            "/behavior/rules/0/id", "/behavior/rules/0/title", "/behavior/rules/0/severity",
            "/behavior/rules/0/when", "/behavior/rules/0/constraint", "/behavior/rules/0/message"
    })
    void rejectsMissingRequiredFieldsOffline(String pointer) throws Exception {
        ObjectNode document = document();
        ObjectNode target = (ObjectNode) document.at(pointer.substring(0, pointer.lastIndexOf('/')));
        target.remove(pointer.substring(pointer.lastIndexOf('/') + 1));
        assertInvalid(document);
    }

    private void assertValid(ObjectNode document) {
        assertTrue(offlineSchema.validate(document).isEmpty());
        new SchemaValidator(MAPPER, SchemaValidator.OSSIE_SCHEMA_PATH).validate(asMap(document));
    }

    private void assertInvalid(ObjectNode document) {
        assertFalse(offlineSchema.validate(document).isEmpty());
        ValidationException error = assertThrows(ValidationException.class,
                () -> new SchemaValidator(MAPPER, SchemaValidator.OSSIE_SCHEMA_PATH).validate(asMap(document)));
        assertTrue(error.getMessage().contains("Schema validation failed"));
    }

    private Map<String, Object> asMap(JsonNode document) {
        return MAPPER.convertValue(document, new TypeReference<>() {});
    }

    private ObjectNode document() throws Exception {
        return (ObjectNode) MAPPER.readTree("""
                {
                  "version": "0.2.0.dev0",
                  "name": "purchasing",
                  "datasets": [{"name": "orders", "source": "warehouse.public.orders"}],
                  "behavior": {
                    "namespace": "purchasing",
                    "behavior_layer_version": "1.0",
                    "actions": [{
                      "id": "approve_order", "title": "Approve order", "kind": "command",
                      "effects": [{"entity": "dataset", "mode": "write",
                        "impact_type": "state_transition", "vendor_details": {"system": "ERP"}}],
                      "vendor_details": {"system": "ERP"}
                    }],
                    "rules": [{
                      "id": "approval_required", "title": "Approval required", "severity": "error",
                      "when": {"operation": "approve_order"}, "constraint": {"approved": true},
                      "message": "An approval is required", "vendor_details": {"system": "ERP"}
                    }],
                    "vendor_details": {"system": "ERP"}
                  }
                }
                """);
    }
}
