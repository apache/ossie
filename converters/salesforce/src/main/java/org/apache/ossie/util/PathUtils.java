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

import static org.apache.ossie.util.DataStructureUtils.asMap;
import static org.apache.ossie.util.DataStructureUtils.asList;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Utility class for working with nested property paths.
 *
 * <p>Provides methods for getting and setting values in nested maps using
 * dot-notation paths (e.g., "a.b.c").</p>
 *
 */
public final class PathUtils {

    private PathUtils() {
        // Utility class, no instantiation
    }

    /**
     * Gets a value from a nested map structure using a dot-notation path.
     *
     * @param data the root map
     * @param path the dot-notation path (e.g., "a.b.c")
     * @return the value at the path, or null if not found
     */
    public static Object getValueAtPath(Map<String, Object> data, String path) {
        if (path == null || path.isEmpty()) {
            return null;
        }

        String[] parts = path.split("\\.");
        Object current = data;

        for (String part : parts) {
            if (current instanceof Map) {
                current = asMap(current).get(part);
            } else {
                return null;
            }
        }

        return current;
    }

    /**
     * Sets a value in a nested map structure using a dot-notation path.
     * Creates intermediate maps as needed.
     *
     * @param data  the root map
     * @param path  the dot-notation path (e.g., "a.b.c")
     * @param value the value to set
     */
    public static void setValueAtPath(Map<String, Object> data, String path, Object value) {
        if (path == null || path.isEmpty()) {
            return;
        }

        String[] parts = path.split("\\.");
        Map<String, Object> current = data;

        for (int i = 0; i < parts.length - 1; i++) {
            String part = parts[i];
            Object next = current.get(part);

            if (next instanceof Map) {
                current = asMap(next);
            } else {
                Map<String, Object> newMap = new LinkedHashMap<>();
                current.put(part, newMap);
                current = newMap;
            }
        }

        current.put(parts[parts.length - 1], value);
    }

    /**
     * Creates a deep copy of a map.
     */
    public static Map<String, Object> deepCopy(Map<String, Object> data) {
        if (data == null) {
            return null;
        }

        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : data.entrySet()) {
            copy.put(entry.getKey(), deepCopyValue(entry.getValue()));
        }
        return copy;
    }

    /**
     * Creates a deep copy of a list.
     */
    public static List<Object> deepCopyList(List<Object> list) {
        List<Object> copy = new ArrayList<>();
        for (Object item : list) {
            copy.add(deepCopyValue(item));
        }
        return copy;
    }

    /**
     * Deep copies a value (Map, List, or primitive).
     *
     * @param value the value to copy
     * @return the deep copy
     */
    public static Object deepCopyValue(Object value) {
        if (value instanceof Map) {
            return deepCopy(asMap(value));
        } else if (value instanceof List) {
            return deepCopyList(asList(value));
        } else {
            return value;
        }
    }
}
