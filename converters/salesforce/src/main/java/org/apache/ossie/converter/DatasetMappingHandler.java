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

import org.apache.ossie.converter.ConverterConstants.Level;
import org.apache.ossie.converter.pipeline.PipelineStep;
import java.util.*;
import java.util.stream.Collectors;

import org.apache.ossie.util.MappingUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Bidirectional handler for mapping datasets between Ossie and Salesforce formats.
 *
 * <p>Supports both conversion directions:
 * <ul>
 *   <li>Ossie → Salesforce: datasets → semanticDataObjects, apply SF defaults, restore custom_extensions</li>
 *   <li>Salesforce → Ossie: semanticDataObjects → datasets, store unmapped properties in custom_extensions</li>
 * </ul>
 *
 */
public class DatasetMappingHandler implements PipelineStep {

    private static final Logger logger = LoggerFactory.getLogger(DatasetMappingHandler.class);

    private final ConversionDirection direction;
    private final CustomExtensionHandler customExtensionHandler;

    public DatasetMappingHandler(ConversionDirection direction, CustomExtensionHandler customExtensionHandler) {
        this.direction = direction;
        this.customExtensionHandler = customExtensionHandler;
    }

    @Override
    public void execute(Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {
        logger.debug("Mapping datasets in {} direction", direction);
        if (direction == ConversionDirection.OSI_TO_SALESFORCE) {
            mapOsiToSalesforce(sourceData, outputData, mappings);
        } else {
            mapSalesforceToOsi(sourceData, outputData, mappings);
        }
    }

    /**
     * Maps Ossie datasets to Salesforce semanticDataObjects.
     */
    private void mapOsiToSalesforce(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        Map<String, String> datasetMappings = MappingUtils.filterMappingsByPrefix(mappings, DATASETS);

        var mappedData = GenericMappingEngine.applyMappings(sourceData, datasetMappings);
        datasetMappings.keySet().forEach(mappings::remove);

        outputData.putAll(mappedData);

        customExtensionHandler.restoreCustomExtensionsAtLevel(outputData, sourceData, Level.DATASETS);

        List<Object> sfDataObjects = getList(outputData, SEMANTIC_DATA_OBJECTS);
        applyDefaults(sfDataObjects);
    }

    /**
     * Maps Salesforce semanticDataObjects to Ossie datasets.
     */
    private void mapSalesforceToOsi(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        List<Object> sfDataObjects = getList(sourceData, SEMANTIC_DATA_OBJECTS);
        if (sfDataObjects == null) {
            return;
        }

        Map<Boolean, List<Object>> partitioned = sfDataObjects.stream()
                .collect(Collectors.partitioningBy(obj -> {
                    Map<String, Object> dataObject = asMap(obj);
                    String tableType = getString(dataObject, TABLE_TYPE);
                    return tableType == null || STANDARD_TABLE_TYPE.equals(tableType);
                }));

        List<Object> standardEntities = partitioned.get(true);
        List<Object> sharedEntities = partitioned.get(false);

        // Create filtered source data with only standard semantic data objects
        Map<String, Object> filteredSourceData = new LinkedHashMap<>(sourceData);
        filteredSourceData.put(SEMANTIC_DATA_OBJECTS, standardEntities);

        Map<String, String> datasetMappings = MappingUtils.filterMappingsByPrefix(mappings, SEMANTIC_DATA_OBJECTS);

        Set<String> allHandledProps = datasetMappings.isEmpty()? new HashSet<>() : MappingUtils.extractHandledProperties(datasetMappings);

        // Add child array keys as handled since FieldMappingHandler processes them
        allHandledProps.add(SEMANTIC_DIMENSIONS);
        allHandledProps.add(SEMANTIC_MEASUREMENTS);

        var mappedData = GenericMappingEngine.applyMappings(filteredSourceData, datasetMappings);
        // Remove consumed mappings from original map
        datasetMappings.keySet().forEach(mappings::remove);

        outputData.putAll(mappedData);

        // Store unmapped SF properties in custom_extensions
        customExtensionHandler.storeUnmappedProperties(outputData, filteredSourceData, allHandledProps, Level.DATASETS);

        if (!sharedEntities.isEmpty()) {
            storeSharedEntitiesInCustomExtensions(outputData, sharedEntities);
        }
    }

    /**
     * Applies default values for required Salesforce data object properties.
     * Used when converting Ossie → Salesforce.
     */
    private void applyDefaults(List<Object> dataObjects) {
        for (Object dataObjectObj : dataObjects) {
            Map<String, Object> dataObject = asMap(dataObjectObj);

            if (!dataObject.containsKey(LABEL) && dataObject.containsKey(API_NAME)) {
                String apiName = getString(dataObject, API_NAME);
                dataObject.put(LABEL, apiName);
            }
            dataObject.putIfAbsent(TABLE_TYPE, STANDARD_TABLE_TYPE);
        }
    }

    /**
     * Stores shared entities in semanticDataObjects in top-level custom_extensions.
     *
     * @param outputData The output Ossie data
     * @param sharedEntities List of non-standard semanticDataObjects
     */
    private void storeSharedEntitiesInCustomExtensions(
            Map<String, Object> outputData, List<Object> sharedEntities) {

        Map<String, Object> customData = new LinkedHashMap<>();
        customData.put(SEMANTIC_DATA_OBJECTS, sharedEntities);

        customExtensionHandler.addCustomExtension(outputData, customData);
    }
}
