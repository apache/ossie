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

import static org.apache.ossie.util.DataStructureUtils.*;

import org.apache.ossie.util.MappingUtils;
import org.apache.ossie.util.PathUtils;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Simplified mapping engine that applies straightforward property mappings from mappings.yaml.
 *
 * <p>Supports:
 * - Simple property mappings (apiName -> name)
 * - Array mappings (semanticDataObjects -> datasets)
 * - Nested property mappings (semanticDataObjects.apiName -> datasets.name)
 * </p>
 *
 * <p>Complex transformations (fields, relationships, metrics) are handled by dedicated handlers.
 *
 */
public class GenericMappingEngine {

    private static final Logger logger = LoggerFactory.getLogger(GenericMappingEngine.class);

    /**
     * Applies mappings from source to target format.
     * Assumes mappings are pre-filtered to the same top-level key.
     */
    public static Map<String, Object> applyMappings(Map<String, Object> sourceData, Map<String, String> mappings) {
        Map<String, Object> outputData = new LinkedHashMap<>();

        if (mappings.isEmpty()) {
            return outputData;
        }

        // All mappings are pre-filtered to the same top-level key
        String topLevelKey = MappingUtils.getFirstPathSegment(mappings.keySet().iterator().next());

        Object sourceValue = sourceData.get(topLevelKey);

        // Check if this is an array mapping
        boolean isArray = sourceValue instanceof List;

        if (isArray) {
            logger.debug("Processing array mapping for key: {}", topLevelKey);
            processArrayMapping(sourceData, outputData, topLevelKey, mappings);
        } else {
            logger.debug("Processing simple mapping for key: {}", topLevelKey);
            processSimpleMapping(sourceData, outputData, topLevelKey, mappings);
        }

        return outputData;
    }

    /**
     * Processes simple (non-array) property mappings.
     */
    private static void processSimpleMapping(
            Map<String, Object> sourceData,
            Map<String, Object> outputData,
            String topLevelKey,
            Map<String, String> mappings) {

        for (Map.Entry<String, String> entry : mappings.entrySet()) {
            String sourcePath = entry.getKey();
            String targetPath = entry.getValue();

            Object value = sourcePath.equals(topLevelKey)
                    ? sourceData.get(topLevelKey)
                    : PathUtils.getValueAtPath(sourceData, sourcePath);

            if (value != null) {
                PathUtils.setValueAtPath(outputData, targetPath, value);
            }
        }
    }

    /**
     * Processes array mappings (e.g., datasets -> semanticDataObjects).
     */
    private static void processArrayMapping(
            Map<String, Object> sourceData,
            Map<String, Object> outputData,
            String sourceArrayKey,
            Map<String, String> mappings) {

        List<Object> sourceArray = getList(sourceData, sourceArrayKey);

        // Get the target array path from mappings
        String targetArrayPath = mappings.get(sourceArrayKey);

        // Process each item in the source array
        List<Object> targetArray = streamMaps(sourceArray)
                .map(sourceItem -> {
                    Map<String, Object> targetItem = new LinkedHashMap<>();

                    // Map nested properties for this array item
                    for (Map.Entry<String, String> entry : mappings.entrySet()) {
                        String sourcePath = entry.getKey();
                        String targetPath = entry.getValue();

                        if (sourcePath.equals(sourceArrayKey)) continue; // Skip the array-level mapping

                        if (!sourcePath.startsWith(sourceArrayKey + ".")) continue; // Not a nested property of this array

                        // Extract the nested path (remove array prefix)
                        String nestedSourcePath = sourcePath.substring(sourceArrayKey.length() + 1);
                        String nestedTargetPath =
                                targetPath.contains(".") ? targetPath.substring(targetPath.lastIndexOf(".") + 1) : targetPath;

                        Object value = PathUtils.getValueAtPath(sourceItem, nestedSourcePath);
                        if (value != null) {
                            targetItem.put(nestedTargetPath, value);
                        }
                    }

                    return targetItem;
                })
                .collect(java.util.stream.Collectors.toList());

        PathUtils.setValueAtPath(outputData, targetArrayPath, targetArray);
    }
}
