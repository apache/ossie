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

package org.apache.ossie.util;

import java.util.*;

/**
 * Utility class for filtering and processing property mappings.
 *
 */
public class MappingUtils {

    /**
     * Extracts the first segment of a dot-separated path.
     *
     * Example:
     * - "name" → "name"
     * - "datasets.name" → "datasets"
     * - "datasets.fields.name" → "datasets"
     *
     * @param path The dot-separated path
     * @return The first segment before the first dot
     */
    public static String getFirstPathSegment(String path) {
        return path.split("\\.")[0];
    }

    /**
     * Filters mappings to get all entries with a specific prefix.
     * Returns map with only the matching entries.
     *
     * Example: filterMappingsByPrefix(mappings, "datasets") returns:
     * - Input: {"datasets": "semanticDataObjects", "datasets.name": "semanticDataObjects.apiName", "name": "apiName"}
     * - Output: {"datasets": "semanticDataObjects", "datasets.name": "semanticDataObjects.apiName", "datasets.source": "..."}
     *
     * @param allMappings All property mappings
     * @param prefix The prefix to filter by (e.g., "datasets", "relationships", "metrics")
     * @return Filtered map containing only entries that match the prefix
     */
    public static Map<String, String> filterMappingsByPrefix(Map<String, String> allMappings, String prefix) {
        Map<String, String> filtered = new LinkedHashMap<>();
        String prefixWithDot = prefix + ".";

        for (Map.Entry<String, String> entry : allMappings.entrySet()) {
            String key = entry.getKey();
            if (key.equals(prefix) || key.startsWith(prefixWithDot)) {
                filtered.put(key, entry.getValue());
            }
        }

        return filtered;
    }

    /**
     * Extracts all unique top-level keys from mappings.
     *
     * Example:
     * - "name" → "name"
     * - "description" → "description"
     * - "datasets.name" → "datasets"
     * - "datasets.fields.name" → "datasets"
     *
     * @param mappings The mappings to extract from
     * @return Set of unique top-level keys
     */
    public static Set<String> extractTopLevelKeys(Map<String, String> mappings) {
        Set<String> topLevelKeys = new HashSet<>();
        for (String key : mappings.keySet()) {
            topLevelKeys.add(getFirstPathSegment(key));
        }
        return topLevelKeys;
    }

    /**
     * Extracts handled properties from filtered mappings.
     *
     * <p>Returns: prefix + all first-level nested properties</p>
     *
     * <p>The prefix is inferred from the filtered mappings by extracting
     * the first segment from any key.</p>
     *
     * Example: prefix "semanticDataObjects" (inferred)
     *   Input mappings: {"semanticDataObjects" → "datasets", "semanticDataObjects.apiName" → "datasets.name"}
     *   Output: {"semanticDataObjects", "apiName", "description"}
     *
     * @param filteredMappings Filtered mappings
     * @return Set including prefix + first-level properties
     */
    public static Set<String> extractHandledProperties(Map<String, String> filteredMappings) {

        Set<String> handledProps = new HashSet<>();
        String prefix = getFirstPathSegment(filteredMappings.keySet().iterator().next());
        handledProps.add(prefix);

        // Extract first-level properties from keys
        for (String sourceKey : filteredMappings.keySet()) {
            if (sourceKey.startsWith(prefix + ".")) {
                handledProps.add(getFirstPathSegment(sourceKey.substring(prefix.length() + 1)));
            }
        }
        return handledProps;
    }
}
