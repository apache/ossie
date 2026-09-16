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

import static org.apache.ossie.converter.ConverterConstants.*;
import static org.apache.ossie.util.DataStructureUtils.*;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ossie.converter.pipeline.*;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.validator.SchemaValidator;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Unified converter that executes pipelines configured in ossie-salesforce-converter-config.yaml.
 *
 */
public class ConverterImpl extends AbstractConverter {

    private final ConversionDirection direction;
    private final DirectionConfig directionConfig;
    private final List<PipelineStep> steps;
    private final SchemaValidator schemaValidator;
    private final SchemaValidator targetSchemaValidator;
    private final SalesforceBindings bindings;

    public ConverterImpl(ConversionDirection direction) {
        this(direction, PipelineConfigLoader.loadFromResource(), SalesforceBindings.none());
    }

    public ConverterImpl(ConversionDirection direction, SalesforceBindings bindings) {
        this(direction, PipelineConfigLoader.loadFromResource(), bindings);
    }

    ConverterImpl(ConversionDirection direction, PipelineConfig config) {
        this(direction, config, SalesforceBindings.none());
    }

    private ConverterImpl(ConversionDirection direction, PipelineConfig config, SalesforceBindings bindings) {
        super();
        this.direction = direction;
        this.bindings = java.util.Objects.requireNonNull(bindings, "bindings");
        if (direction != ConversionDirection.OSSIE_TO_SALESFORCE && !bindings.isEmpty()) {
            throw new ConversionException("Salesforce bindings apply only to toSF conversion");
        }

        // Get handler list for this direction
        List<String> handlerNames = config.getPipelines().get(direction.toPipelineKey());
        if (handlerNames == null || handlerNames.isEmpty()) {
            throw new ConversionException("No pipeline defined for direction: " + direction);
        }

        // Get direction-specific configuration
        this.directionConfig = config.getDirectionConfigs().get(direction.toPipelineKey());
        if (this.directionConfig == null) {
            throw new ConversionException("No configuration found for direction: " + direction);
        }

        // Initialize schema validator
        ObjectMapper schemaMapper = YAML.equals(directionConfig.getInputFormat())
            ? yamlMapper : jsonMapper;
        this.schemaValidator = new SchemaValidator(
            schemaMapper,
            directionConfig.getSchemaPath()
        );

        // Output validation is part of conversion, not only an optional test assertion.
        this.targetSchemaValidator = new SchemaValidator(jsonMapper,
                direction == ConversionDirection.OSSIE_TO_SALESFORCE
                        ? SchemaValidator.SALESFORCE_SCHEMA_PATH : SchemaValidator.OSSIE_SCHEMA_PATH);

        // Initialize pipeline steps using factory
        HandlerFactory factory = new HandlerFactory(customExtensionHandler);
        this.steps = handlerNames.stream()
            .map(handlerName -> factory.createHandler(handlerName, direction))
            .toList();
    }

    @Override
    public List<String> convert(String content) {
        Map<String, Object> sourceData = YAML.equals(directionConfig.getInputFormat())
            ? parseYaml(content)
            : parseJson(content);

        schemaValidator.validate(sourceData);

        if (direction == ConversionDirection.OSSIE_TO_SALESFORCE) {
            return convertOssieToSalesforce(sourceData);
        } else {
            return convertSalesforceToOssie(sourceData);
        }
    }

    private List<String> convertOssieToSalesforce(Map<String, Object> ossieRoot) {
        List<Object> semanticModels = getList(ossieRoot, SEMANTIC_MODEL);
        List<String> results = new ArrayList<>();
        java.util.Set<String> names = new java.util.HashSet<>();
        for (Object modelObj : semanticModels) {
            String name = getString(asMap(modelObj), NAME);
            if (!names.add(name)) throw new ConversionException("Duplicate model name '" + name + "'");
        }
        bindings.validateModels(names);

        for (Object modelObj : semanticModels) {
            Map<String, Object> sourceData = asMap(modelObj);
            String result = executePipeline(sourceData);
            results.add(result);
        }
        return results;
    }

    private List<String> convertSalesforceToOssie(Map<String, Object> sourceData) {
        String result = executePipeline(sourceData);

        // Wrap output in Ossie root structure
        try {
            Map<String, Object> outputData = yamlMapper.readValue(result, new TypeReference<>() {});
            Map<String, Object> ossieRoot = new LinkedHashMap<>();
            ossieRoot.put(VERSION, OSSIE_VERSION);
            ossieRoot.put(SEMANTIC_MODEL, List.of(outputData));
            targetSchemaValidator.validate(ossieRoot);
            return List.of(toYaml(ossieRoot));
        } catch (JsonProcessingException e) {
            throw new ConversionException("Failed to wrap output in Ossie root", e);
        }
    }

    private String executePipeline(Map<String, Object> sourceData) {
        Map<String, Object> outputData = new LinkedHashMap<>();
        Map<String, String> mappings = new LinkedHashMap<>(direction == ConversionDirection.OSSIE_TO_SALESFORCE
            ? mapper.getOssieToSalesforceMappings()
            : mapper.getSalesforceToOssieMappings());

        ConversionContext context = new ConversionContext(sourceData, outputData);
        for (PipelineStep step : steps) {
            try {
                step.execute(context, mappings);
            } catch (IllegalArgumentException e) {
                String name = getString(sourceData,
                        direction == ConversionDirection.OSSIE_TO_SALESFORCE ? NAME : API_NAME);
                throw new ConversionException("Model '" + name + "': " + e.getMessage(), e);
            }
        }
        if (direction == ConversionDirection.OSSIE_TO_SALESFORCE) {
            bindings.apply(sourceData, outputData);
            new SalesforceModelValidator().validate(sourceData, outputData, context.fieldPlan());
            targetSchemaValidator.validate(outputData);
        }
        return serialize(outputData);
    }

    private String serialize(Map<String, Object> data) {
        return JSON.equals(directionConfig.getOutputFormat())
            ? toJson(data)
            : toYaml(data);
    }

    @Override
    protected String getFileExtension() {
        return directionConfig.getFileExtension();
    }

    @Override
    protected String extractModelName(String result) {
        try {
            Map<String, Object> data = JSON.equals(directionConfig.getOutputFormat())
                ? jsonMapper.readValue(result, new TypeReference<>() {})
                : yamlMapper.readValue(result, new TypeReference<>() {});

            String field = directionConfig.getExtractModelNameFrom();

            // Handle Ossie format (wrapped in semantic_model array)
            if (direction == ConversionDirection.SALESFORCE_TO_OSSIE) {
                List<Object> models = getList(data, SEMANTIC_MODEL);
                if (models != null && !models.isEmpty()) {
                    Map<String, Object> firstModel = asMap(models.get(0));
                    return firstModel.get(field).toString();
                }
            }

            return data.get(field).toString();
        } catch (JsonProcessingException e) {
            throw new ConversionException("Failed to extract model name", e);
        }
    }
}
