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

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import java.io.InputStream;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

import org.apache.ossie.exception.ValidationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Validates Ossie semantic model data against JSON Schema.
 *
 * <p>Uses networknt/json-schema-validator for schema validation.
 * Collects all validation errors for better developer experience.
 *
 */
public class SchemaValidator {

    private static final Logger logger = LoggerFactory.getLogger(SchemaValidator.class);
    public static final String OSI_SCHEMA_PATH = "/schemas/osi-schema.json";
    public static final String SALESFORCE_SCHEMA_PATH = "/schemas/salesforce-semantic-model-schema.json";

    private final JsonSchema schema;
    private final ObjectMapper objectMapper;
    private final String schemaPath;

    /**
     * Creates a validator with a custom schema.
     *
     * @param objectMapper Jackson ObjectMapper for converting data to JsonNode
     * @param schemaPath Path to the JSON schema file in classpath
     */
    public SchemaValidator(ObjectMapper objectMapper, String schemaPath) {
        this.objectMapper = objectMapper;
        this.schemaPath = schemaPath;
        this.schema = loadSchema();
    }

    /**
     * Loads the JSON Schema from classpath.
     */
    private JsonSchema loadSchema() {
        try (InputStream schemaStream = getClass().getResourceAsStream(schemaPath)) {
            if (schemaStream == null) {
                throw new ValidationException("Schema file not found: " + schemaPath);
            }

            JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V7);
            JsonNode schemaNode = objectMapper.readTree(schemaStream);

            logger.info("Loaded schema from {}", schemaPath);
            return factory.getSchema(schemaNode);

        } catch (Exception e) {
            throw new ValidationException("Failed to load schema: " + e.getMessage(), e);
        }
    }

    /**
     * Validates Ossie semantic model data against the schema.
     *
     * @param data The semantic model data to validate
     * @throws ValidationException if validation fails with details of all errors
     */
    public void validate(Map<String, Object> data) {
        try {
            // Convert Map to JsonNode
            JsonNode jsonNode = objectMapper.valueToTree(data);

            // Validate against schema
            Set<ValidationMessage> errors = schema.validate(jsonNode);

            if (!errors.isEmpty()) {
                String errorMessage = formatValidationErrors(errors);
                logger.error("Schema validation failed:\n{}", errorMessage);
                throw new ValidationException("Schema validation failed:\n" + errorMessage);
            }

            logger.debug("Schema validation passed");

        } catch (ValidationException e) {
            throw e;
        } catch (Exception e) {
            throw new ValidationException("Validation error: " + e.getMessage(), e);
        }
    }

    /**
     * Formats validation errors into a readable message.
     */
    private String formatValidationErrors(Set<ValidationMessage> errors) {
        return errors.stream()
                .map(ValidationMessage::toString)
                .collect(Collectors.joining("\n  - ", "  - ", ""));
    }
}
