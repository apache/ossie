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

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ossie.converter.ConverterConstants.Level;
import org.apache.ossie.util.PathUtils;
import java.util.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Handler for adding and restoring custom_extensions in the Semantic Model.
 *
 * <p>Handles identifying unmapped properties during Salesforce-to-Ossie conversion
 * and restoring them during Ossie-to-Salesforce conversion.</p>
 *
 */
public class CustomExtensionHandler {

    private static final Logger logger = LoggerFactory.getLogger(CustomExtensionHandler.class);

    private final ObjectMapper jsonMapper;

    public CustomExtensionHandler(ObjectMapper jsonMapper) {
        this.jsonMapper = jsonMapper;
    }

    /**
     * Unified method to restore custom_extensions at any level.
     *
     * @param outputData The output data containing semanticModel
     * @param sourceData The source Ossie data
     * @param level The level to restore
     */
    public void restoreCustomExtensionsAtLevel(
            Map<String, Object> outputData, Map<String, Object> sourceData, Level level) {

        switch (level) {
            case SEMANTIC_MODEL:
                restoreSalesforceCustomExtension(outputData, sourceData);
                break;

            case DATASETS:
                restoreArrayLevelExtensions(outputData, sourceData, ConverterConstants.DATASETS, SEMANTIC_DATA_OBJECTS, NAME, API_NAME);
                break;

            case RELATIONSHIPS:
                restoreArrayLevelExtensions(
                        outputData, sourceData, ConverterConstants.RELATIONSHIPS, SEMANTIC_RELATIONSHIPS, NAME, API_NAME);
                break;

            case METRICS:
                restoreArrayLevelExtensions(
                        outputData, sourceData, ConverterConstants.METRICS, SEMANTIC_CALCULATED_MEASUREMENTS, NAME, API_NAME);
                break;
        }
    }

    /**
     * Restores custom_extensions for an array at a specific level.
     * Generic method that works for datasets, relationships, etc.
     *
     * @param semanticModel The semanticModel object
     * @param sourceData The source Ossie data
     * @param sourceArrayKey The key in source data (e.g., "datasets", "relationships")
     * @param targetArrayKey The key in semanticModel (e.g., "semanticDataObjects", "semanticRelationships")
     * @param sourceIdKey The identifying key in source items (e.g., "name")
     * @param targetIdKey The identifying key in target items (e.g., "apiName")
     */
    private void restoreArrayLevelExtensions(
            Map<String, Object> semanticModel,
            Map<String, Object> sourceData,
            String sourceArrayKey,
            String targetArrayKey,
            String sourceIdKey,
            String targetIdKey) {

        List<Object> sourceArray = getList(sourceData, sourceArrayKey);
        if (sourceArray == null) {
            return;
        }

        List<Object> targetArray = getList(semanticModel, targetArrayKey);
        if (targetArray == null) {
            return;
        }

        streamMaps(sourceArray).forEach(sourceItem -> {
            // Check if source item has SALESFORCE custom_extensions
            if (!hasSalesforceCustomExtension(sourceItem)) {
                return;
            }

            // Find matching target item by identifier
            String itemId = getString(sourceItem, sourceIdKey);
            if (itemId == null) return;

            Map<String, Object> targetItem = findItemById(targetArray, targetIdKey, itemId);
            if (targetItem == null) return;

            // Restore custom_extensions
            restoreSalesforceCustomExtension(targetItem, sourceItem);
        });
    }

    /**
     * Checks if an item has SALESFORCE vendor custom_extensions.
     *
     * @param item The item to check
     * @return true if the item has SALESFORCE custom_extensions
     */
    private boolean hasSalesforceCustomExtension(Map<String, Object> item) {
        Object customExtensionsObj = item.get(CUSTOM_EXTENSIONS);
        if (customExtensionsObj == null) {
            return false;
        }

        List<Object> customExtensions = asList(customExtensionsObj);

        return streamMaps(customExtensions)
                .anyMatch(ext -> VENDOR_NAME_VALUE.equals(ext.get(VENDOR_NAME)));
    }

    /**
     * Unified method to store unmapped properties as custom_extensions at any level.
     * This is the inverse of restoreCustomExtensionsAtLevel.
     *
     * @param outputData The output Ossie data
     * @param sourceData The source Salesforce data
     * @param allHandledProps All properties that were handled (mapped + programmatic)
     * @param level The level to store
     */
    public void storeUnmappedProperties(
            Map<String, Object> outputData, Map<String, Object> sourceData, Set<String> allHandledProps, Level level) {

        switch (level) {
            case SEMANTIC_MODEL:
                storeUnmappedItemProperties(outputData, sourceData, allHandledProps);
                break;

            case DATASETS:
                storeArrayLevelUnmappedProperties(
                        outputData, sourceData, allHandledProps, ConverterConstants.DATASETS, SEMANTIC_DATA_OBJECTS, NAME, API_NAME);
                break;

            case RELATIONSHIPS:
                storeArrayLevelUnmappedProperties(
                        outputData, sourceData, allHandledProps, ConverterConstants.RELATIONSHIPS, SEMANTIC_RELATIONSHIPS, NAME, API_NAME);
                break;

            case METRICS:
                storeArrayLevelUnmappedProperties(
                        outputData,
                        sourceData,
                        allHandledProps,
                        ConverterConstants.METRICS,
                        SEMANTIC_CALCULATED_MEASUREMENTS,
                        NAME,
                        API_NAME);
                break;
        }
    }

    /**
     * Stores unmapped properties for array items (datasets, relationships, metrics).
     *
     * @param osiData The Ossie output data
     * @param sfData The Salesforce source data
     * @param allHandledProps All properties that were handled (from mappings + programmatic)
     * @param osiArrayKey The Ossie array key (e.g., "datasets")
     * @param sfArrayKey The SF array key (e.g., "semanticDataObjects")
     * @param osiIdKey The Ossie identifier key (e.g., "name")
     * @param sfIdKey The SF identifier key (e.g., "apiName")
     */
    private void storeArrayLevelUnmappedProperties(
            Map<String, Object> osiData,
            Map<String, Object> sfData,
            Set<String> allHandledProps,
            String osiArrayKey,
            String sfArrayKey,
            String osiIdKey,
            String sfIdKey) {

        List<Object> osiArray = getList(osiData, osiArrayKey);
        if (osiArray == null) {
            return;
        }

        List<Object> sfArray = getList(sfData, sfArrayKey);
        if (sfArray == null) {
            return;
        }

        // Process each Ossie item and find its matching SF item
        streamMaps(osiArray).forEach(osiItem -> {
            // Find matching SF item by identifier
            String itemId = getString(osiItem, osiIdKey);
            if (itemId == null) return;

            Map<String, Object> sfItem = findItemById(sfArray, sfIdKey, itemId);
            if (sfItem == null) return;

            // Find unmapped properties: SF properties NOT handled
            Map<String, Object> unmappedProperties = new LinkedHashMap<>();
            for (Map.Entry<String, Object> entry : sfItem.entrySet()) {
                // If property is not handled → it's unmapped
                if (!allHandledProps.contains(entry.getKey())) {
                    unmappedProperties.put(entry.getKey(), PathUtils.deepCopyValue(entry.getValue()));
                }
            }

            // Store unmapped properties in custom_extensions
            if (!unmappedProperties.isEmpty()) {
                addCustomExtension(osiItem, unmappedProperties);
            }
        });
    }

    /**
     * Stores unmapped properties from a source item into an Ossie item's custom_extensions.
     * Generic method that can be used for fields or any other individual items.
     *
     * @param osiItem The target Ossie item to add custom_extensions to
     * @param sfItem The source Salesforce item
     * @param handledProps Set of property keys that were handled (mapped or programmatically processed)
     */
    public void storeUnmappedItemProperties(
            Map<String, Object> osiItem, Map<String, Object> sfItem, Set<String> handledProps) {
        // Find unmapped properties: everything in SF item that wasn't handled
        Map<String, Object> unmappedProps = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : sfItem.entrySet()) {
            if (!handledProps.contains(entry.getKey())) {
                unmappedProps.put(entry.getKey(), PathUtils.deepCopyValue(entry.getValue()));
            }
        }

        // Store in custom_extensions
        if (!unmappedProps.isEmpty()) {
            String itemName = getString(osiItem, NAME);
            if (itemName == null) {
                logger.warn("Item has no name, skipping custom_extensions storage for unmapped properties");
                return;
            }
            addCustomExtension(osiItem, unmappedProps);
        }
    }

    /**
     * Adds a custom_extensions entry with SALESFORCE vendor data.
     * If a SALESFORCE custom_extension already exists, merges the data.
     */
    public void addCustomExtension(Map<String, Object> target, Map<String, Object> customData) {
        try {
            List<Object> customExtensions =
                    asList(target.computeIfAbsent(CUSTOM_EXTENSIONS, k -> new ArrayList<>()));

            if (!customExtensions.isEmpty()) {
                // Merge into existing extension
                Map<String, Object> salesforceExtension = asMap(customExtensions.get(0));
                String existingDataJson = (String) salesforceExtension.get(DATA);
                Map<String, Object> existingData = jsonMapper.readValue(
                    existingDataJson, new TypeReference<LinkedHashMap<String, Object>>() {});

                existingData.putAll(customData);
                salesforceExtension.put(DATA, jsonMapper.writeValueAsString(existingData));
            } else {
                // Create new extension
                Map<String, Object> extension = new LinkedHashMap<>();
                extension.put(VENDOR_NAME, VENDOR_NAME_VALUE);
                extension.put(DATA, jsonMapper.writeValueAsString(customData));
                customExtensions.add(extension);
            }

        } catch (Exception e) {
            logger.warn("Failed to store custom_extensions: {}", e.getMessage());
        }
    }

    /**
     * Extracts SALESFORCE vendor custom_extension from Ossie item and merges into SF item.
     * This is a generic method that can be used for any level (dataset, field, relationship, metric).
     *
     * @param sfItem The Salesforce item to merge properties into
     * @param osiItem The Ossie item containing custom_extensions
     */
    public void restoreSalesforceCustomExtension(Map<String, Object> sfItem, Map<String, Object> osiItem) {
        Object customExtensionsObj = osiItem.get(CUSTOM_EXTENSIONS);
        if (customExtensionsObj == null) {
            return;
        }

        List<Object> customExtensions = asList(customExtensionsObj);

        streamMaps(customExtensions).forEach(ext -> {
            if (!VENDOR_NAME_VALUE.equals(ext.get(VENDOR_NAME))) {
                return;
            }

            Object dataObj = ext.get(DATA);
            if (dataObj == null) {
                return;
            }

            try {
                // Parse JSON string to Map
                Map<String, Object> salesforceProperties = jsonMapper.readValue(
                    (String) dataObj,
                    new TypeReference<LinkedHashMap<String, Object>>() {}
                );

                String itemName = getString(osiItem, NAME);
                if (itemName == null) {
                    logger.warn("Item has no name, skipping custom_extensions restoration");
                    return;
                }

                for (Map.Entry<String, Object> entry : salesforceProperties.entrySet()) {
                    if (!sfItem.containsKey(entry.getKey())) {
                        sfItem.put(entry.getKey(), PathUtils.deepCopyValue(entry.getValue()));
                    }
                }

            } catch (Exception e) {
                logger.warn("Failed to restore custom_extensions: {}", e.getMessage());
            }
        });
    }
}
