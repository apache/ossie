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
import org.apache.ossie.converter.pipeline.*;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.validator.SchemaValidator;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Unified converter that executes pipelines configured in osi-salesforce-converter-config.yaml.
 *
 */
public class ConverterImpl extends AbstractConverter {

    private final ConversionDirection direction;
    private final DirectionConfig directionConfig;
    private final List<PipelineStep> steps;
    private final SchemaValidator schemaValidator;

    public ConverterImpl(ConversionDirection direction) {
        this(direction, PipelineConfigLoader.loadFromResource());
    }

    ConverterImpl(ConversionDirection direction, PipelineConfig config) {
        super();
        this.direction = direction;

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

        if (direction == ConversionDirection.OSI_TO_SALESFORCE) {
            return convertOsiToSalesforce(sourceData);
        } else {
            return convertSalesforceToOsi(sourceData);
        }
    }

    private List<String> convertOsiToSalesforce(Map<String, Object> osiRoot) {
        List<Object> semanticModels = getList(osiRoot, SEMANTIC_MODEL);
        List<String> results = new ArrayList<>();

        for (Object modelObj : semanticModels) {
            Map<String, Object> sourceData = asMap(modelObj);
            String result = executePipeline(sourceData);
            results.add(result);
        }
        return results;
    }

    private List<String> convertSalesforceToOsi(Map<String, Object> sourceData) {
        String result = executePipeline(sourceData);

        // Wrap output in Ossie root structure
        try {
            Map<String, Object> outputData = yamlMapper.readValue(result, new TypeReference<>() {});
            Map<String, Object> osiRoot = new LinkedHashMap<>();
            osiRoot.put(VERSION, OSI_VERSION);
            osiRoot.put(SEMANTIC_MODEL, List.of(outputData));
            return List.of(toYaml(osiRoot));
        } catch (JsonProcessingException e) {
            throw new ConversionException("Failed to wrap output in Ossie root", e);
        }
    }

    private String executePipeline(Map<String, Object> sourceData) {
        Map<String, Object> outputData = new LinkedHashMap<>();
        Map<String, String> mappings = new LinkedHashMap<>(direction == ConversionDirection.OSI_TO_SALESFORCE
            ? mapper.getOsiToSalesforceMappings()
            : mapper.getSalesforceToOsiMappings());

        for (PipelineStep step : steps) {
            step.execute(sourceData, outputData, mappings);
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
            if (direction == ConversionDirection.SALESFORCE_TO_OSI) {
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
