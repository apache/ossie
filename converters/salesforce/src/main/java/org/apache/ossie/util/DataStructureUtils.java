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

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Utility class for working with generic Map/List data structures.
 * Provides type-safe accessors to reduce casting boilerplate.
 *
 */
public final class DataStructureUtils {

    private DataStructureUtils() {}

    /**
     * Gets a String value from a map.
     *
     * @param map The map to get the value from
     * @param key The key to look up
     * @return The String value, or null if not found
     */
    @SuppressWarnings("unchecked")
    public static String getString(Map<String, Object> map, String key) {
        return (String) map.get(key);
    }

    /**
     * Gets a Map value from a map.
     *
     * @param map The map to get the value from
     * @param key The key to look up
     * @return The Map value, or null if not found
     */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> getMap(Map<String, Object> map, String key) {
        return (Map<String, Object>) map.get(key);
    }

    /**
     * Gets a List value from a map.
     *
     * @param map The map to get the value from
     * @param key The key to look up
     * @return The List value, or null if not found
     */
    @SuppressWarnings("unchecked")
    public static List<Object> getList(Map<String, Object> map, String key) {
        return (List<Object>) map.get(key);
    }

    /**
     * Safely casts an Object to Map.
     *
     * @param obj The object to cast
     * @return The Map value
     */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> asMap(Object obj) {
        return (Map<String, Object>) obj;
    }

    /**
     * Safely casts an Object to List&lt;Object&gt;.
     * Caller should check instanceof List before calling this method.
     *
     * @param obj the object to cast
     * @return the object cast to List&lt;Object&gt;
     */
    @SuppressWarnings("unchecked")
    public static List<Object> asList(Object obj) {
        return (List<Object>) obj;
    }

    /**
     * Gets an existing list from a map, or creates a new empty ArrayList if not present.
     * Encapsulates the unchecked cast from computeIfAbsent.
     *
     * @param map The map to get or create the list in
     * @param key The key for the list
     * @return The existing or newly created list
     */
    @SuppressWarnings("unchecked")
    public static List<Object> getOrCreateList(Map<String, Object> map, String key) {
        return (List<Object>) map.computeIfAbsent(key, k -> new ArrayList<>());
    }

    /**
     * Filters a list to only Map items and returns a stream of safely cast Maps.
     * This reduces boilerplate when iterating over heterogeneous lists.
     *
     * <p>Example usage:
     * <pre>{@code
     * streamMaps(array).forEach(item -> {
     *     // process item...
     * });
     * }</pre>
     *
     * @param list The list to filter and stream
     * @return A stream of Map items from the list
     */
    public static Stream<Map<String, Object>> streamMaps(List<Object> list) {
        return list.stream()
                .filter(obj -> obj instanceof Map)
                .map(DataStructureUtils::asMap);
    }

    /**
     * Finds an item in an array by matching an identifier field.
     *
     * @param array The array to search
     * @param idKey The key to match (e.g., "apiName", "name")
     * @param idValue The value to match
     * @return The matching item or null
     */
    public static Map<String, Object> findItemById(List<Object> array, String idKey, String idValue) {
        return streamMaps(array)
                .filter(item -> idValue.equals(item.get(idKey)))
                .findFirst()
                .orElse(null);
    }
}
