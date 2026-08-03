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

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ossie.converter.ConverterConstants.Level;
import org.apache.ossie.converter.pipeline.PipelineStep;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.util.MappingUtils;

import java.util.Map;
import java.util.Set;

import static org.apache.ossie.converter.ConverterConstants.AI_CONTEXT;
import static org.apache.ossie.converter.ConverterConstants.API_NAME;
import static org.apache.ossie.converter.ConverterConstants.BUSINESS_PREFERENCES;
import static org.apache.ossie.converter.ConverterConstants.LABEL;

/**
 * Bidirectional handler for mapping top-level semantic model properties.
 *
 * <p>Supports both conversion directions:
 * <ul>
 *   <li>Ossie → Salesforce: name → apiName, apply SF defaults, restore custom_extensions</li>
 *   <li>Salesforce → Ossie: apiName → name, strip SF defaults, store custom_extensions</li>
 * </ul>
 *
 * <p>Handles only top-level scalar properties (not arrays).
 *
 */
public class SemanticModelMappingHandler implements PipelineStep {

    private final ConversionDirection direction;
    private final CustomExtensionHandler customExtensionHandler;
    private final ObjectMapper jsonMapper;

    public SemanticModelMappingHandler(ConversionDirection direction, CustomExtensionHandler customExtensionHandler) {
        this.direction = direction;
        this.customExtensionHandler = customExtensionHandler;
        this.jsonMapper = new ObjectMapper();
    }

    @Override
    public void execute(Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {
        if (direction == ConversionDirection.OSI_TO_SALESFORCE) {
            mapOsiToSalesforce(sourceData, outputData, mappings);
        } else {
            mapSalesforceToOsi(sourceData, outputData, mappings);
        }
    }

    /**
     * Maps Ossie top-level properties to Salesforce format.
     * Steps: generic mappings → manual conversions → restore custom extensions
     */
    private void mapOsiToSalesforce(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        var mappedData = GenericMappingEngine.applyMappings(sourceData, mappings);
        outputData.putAll(mappedData);

        convertAiContextToBusinessPreferences(sourceData, outputData);

        customExtensionHandler.restoreCustomExtensionsAtLevel(outputData, sourceData, Level.SEMANTIC_MODEL);

        applyDefaults(outputData);
    }

    /**
     * Maps Salesforce top-level properties to Ossie format.
     * Steps: generic mappings → manual conversions → store custom extensions
     */
    private void mapSalesforceToOsi(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        var mappedData = GenericMappingEngine.applyMappings(sourceData, mappings);
        outputData.putAll(mappedData);

        convertBusinessPreferencesToAiContext(sourceData, outputData);

        Set<String> allHandledProps = MappingUtils.extractTopLevelKeys(mappings);
        allHandledProps.add(BUSINESS_PREFERENCES);

        customExtensionHandler.storeUnmappedProperties(outputData, sourceData, allHandledProps, Level.SEMANTIC_MODEL);
    }

    /**
     * Converts Salesforce businessPreferences to Ossie ai_context.
     *
     * @param sourceData Salesforce data containing businessPreferences
     * @param outputData Ossie data to populate with ai_context
     */
    private void convertBusinessPreferencesToAiContext(Map<String, Object> sourceData, Map<String, Object> outputData) {
        Object businessPreferences = sourceData.get(BUSINESS_PREFERENCES);
        if (businessPreferences != null) {
            outputData.put(AI_CONTEXT, businessPreferences);
        }
    }

    /**
     * Converts Ossie ai_context (string or object) to Salesforce businessPreferences (string).
     *
     * <p>ai_context can be:
     * <ul>
     *   <li>A simple string - copied as-is</li>
     *   <li>An object - serialized to JSON string</li>
     * </ul>
     *
     * @param sourceData Ossie data containing ai_context
     * @param outputData Salesforce data to populate with businessPreferences
     */
    private void convertAiContextToBusinessPreferences(Map<String, Object> sourceData, Map<String, Object> outputData) {
        Object aiContextObj = sourceData.get(AI_CONTEXT);
        if (aiContextObj == null) {
            return;
        }

        String businessPreferences;
        if (aiContextObj instanceof String) {
            businessPreferences = aiContextObj.toString();
        } else {
            try {
                businessPreferences = jsonMapper.writeValueAsString(aiContextObj);
            } catch (JsonProcessingException e) {
                throw new ConversionException("Failed to serialize ai_context to JSON: " + e.getMessage(), e);
            }
        }
        outputData.put(BUSINESS_PREFERENCES, businessPreferences);
    }

    /**
     * Applies default values for required Salesforce semantic model properties.
     * Used when converting Ossie → Salesforce.
     */
    private void applyDefaults(Map<String, Object> outputData) {
        if (!outputData.containsKey(LABEL)) {
            String apiName = (String) outputData.get(API_NAME);
            outputData.put(LABEL, apiName);
        }
    }
}
