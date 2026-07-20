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

package org.apache.ossie.converter.polaris;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.TextNode;
import org.apache.ossie.model.CustomExtension;
import org.apache.ossie.model.Field;

import java.io.IOException;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicInteger;

/** Shared Iceberg physical type and Ossie logical datatype mapping. */
final class IcebergTypeMapper {

    static final String POLARIS_VENDOR = "POLARIS";
    static final String ICEBERG_TYPE_KEY = "iceberg_type";

    private IcebergTypeMapper() {}

    /** Map an Iceberg type JSON value to the portable Ossie datatype vocabulary. */
    static String toOssieDatatype(JsonNode icebergType) {
        String baseType = baseType(icebergType);
        if (baseType == null) {
            return null;
        }
        switch (baseType) {
            case "boolean":
                return "Boolean";
            case "int":
            case "long":
                return "Integer";
            case "float":
            case "double":
                return "Float";
            case "decimal":
                return "Decimal";
            case "date":
                return "Date";
            case "time":
                return "Time";
            case "timestamp":
            case "timestamp_ns":
                return "DateTime";
            case "timestamptz":
            case "timestamptz_ns":
                return "DateTimeTz";
            case "string":
                return "String";
            case "unknown":
                return null;
            default:
                return "Opaque";
        }
    }

    /** Return the default Iceberg physical type for a portable Ossie datatype. */
    static JsonNode toDefaultIcebergType(String datatype) {
        if (datatype == null) {
            return null;
        }
        switch (datatype) {
            case "String":
                return TextNode.valueOf("string");
            case "Integer":
                return TextNode.valueOf("long");
            case "Decimal":
                return TextNode.valueOf("decimal(18, 2)");
            case "Float":
                return TextNode.valueOf("double");
            case "Boolean":
                return TextNode.valueOf("boolean");
            case "Date":
                return TextNode.valueOf("date");
            case "Time":
                return TextNode.valueOf("time");
            case "DateTime":
                return TextNode.valueOf("timestamp");
            case "DateTimeTz":
                return TextNode.valueOf("timestamptz");
            default:
                return null;
        }
    }

    static boolean isTemporalDatatype(String datatype) {
        return "Date".equals(datatype)
                || "Time".equals(datatype)
                || "DateTime".equals(datatype)
                || "DateTimeTz".equals(datatype);
    }

    /** Read the exact Iceberg type JSON stored by the Polaris importer. */
    static JsonNode exactIcebergType(Field field, ObjectMapper objectMapper) {
        for (CustomExtension extension : field.getCustomExtensions()) {
            if (!POLARIS_VENDOR.equals(extension.getVendorName())) {
                continue;
            }
            try {
                JsonNode data = objectMapper.readTree(extension.getData());
                JsonNode icebergType = data == null ? null : data.get(ICEBERG_TYPE_KEY);
                if (icebergType == null) {
                    continue;
                }
                if (!icebergType.isTextual() && !icebergType.isObject()) {
                    warn(field.getName(), "ignoring a POLARIS iceberg_type that is not a string or object");
                    continue;
                }
                return icebergType;
            } catch (IOException | RuntimeException e) {
                warn(field.getName(), "ignoring invalid POLARIS extension data: " + e.getMessage());
            }
        }
        return null;
    }

    /**
     * Clone an exact Iceberg type for a newly-created schema and assign fresh IDs
     * to every nested struct field, list element, and map key/value.
     */
    static JsonNode prepareExactTypeForExport(JsonNode icebergType, AtomicInteger nextNestedId) {
        if (icebergType == null || icebergType.isNull()) {
            return null;
        }
        if (!icebergType.isObject()) {
            return icebergType.deepCopy();
        }

        String baseType = baseType(icebergType);
        // Accept object-shaped decimal/fixed values from older or non-standard producers,
        // but emit the canonical Iceberg JSON string representation.
        if ("decimal".equals(baseType) || "fixed".equals(baseType)) {
            return TextNode.valueOf(displayIcebergType(icebergType));
        }

        ObjectNode copy = ((ObjectNode) icebergType).deepCopy();
        if ("struct".equals(baseType)) {
            JsonNode fieldsNode = copy.get("fields");
            if (fieldsNode instanceof ArrayNode) {
                for (JsonNode fieldNode : fieldsNode) {
                    if (fieldNode instanceof ObjectNode) {
                        ObjectNode nestedField = (ObjectNode) fieldNode;
                        nestedField.put("id", nextNestedId.getAndIncrement());
                        if (nestedField.has("type")) {
                            nestedField.set(
                                    "type",
                                    prepareExactTypeForExport(nestedField.get("type"), nextNestedId));
                        }
                    }
                }
            }
        } else if ("list".equals(baseType)) {
            copy.put("element-id", nextNestedId.getAndIncrement());
            if (copy.has("element")) {
                copy.set("element", prepareExactTypeForExport(copy.get("element"), nextNestedId));
            }
        } else if ("map".equals(baseType)) {
            copy.put("key-id", nextNestedId.getAndIncrement());
            if (copy.has("key")) {
                copy.set("key", prepareExactTypeForExport(copy.get("key"), nextNestedId));
            }
            copy.put("value-id", nextNestedId.getAndIncrement());
            if (copy.has("value")) {
                copy.set("value", prepareExactTypeForExport(copy.get("value"), nextNestedId));
            }
        }
        return copy;
    }

    /** Human-readable Iceberg type used by the converter's legacy descriptions. */
    static String displayIcebergType(JsonNode icebergType) {
        if (icebergType == null || icebergType.isNull()) {
            return "unknown";
        }
        if (icebergType.isTextual()) {
            return icebergType.asText();
        }
        if (!icebergType.isObject()) {
            return "unknown";
        }

        String type = baseType(icebergType);
        if ("list".equals(type)) {
            return "list<" + displayIcebergType(icebergType.get("element")) + ">";
        }
        if ("map".equals(type)) {
            return "map<" + displayIcebergType(icebergType.get("key")) + ", "
                    + displayIcebergType(icebergType.get("value")) + ">";
        }
        if ("fixed".equals(type)) {
            return "fixed[" + icebergType.path("length").asInt() + "]";
        }
        if ("decimal".equals(type)) {
            return "decimal(" + icebergType.path("precision").asInt() + ", "
                    + icebergType.path("scale").asInt() + ")";
        }
        return type == null ? "unknown" : type;
    }

    private static String baseType(JsonNode icebergType) {
        if (icebergType == null || icebergType.isNull()) {
            return null;
        }
        String type;
        if (icebergType.isTextual()) {
            type = icebergType.asText().trim().toLowerCase(Locale.ROOT);
        } else if (icebergType.isObject() && icebergType.has("type")) {
            type = icebergType.get("type").asText().trim().toLowerCase(Locale.ROOT);
        } else {
            return null;
        }

        if (type.startsWith("decimal(")) {
            return "decimal";
        }
        if (type.startsWith("fixed[")) {
            return "fixed";
        }
        if (type.startsWith("geometry(")) {
            return "geometry";
        }
        if (type.startsWith("geography(")) {
            return "geography";
        }
        return type;
    }

    static void warn(String fieldName, String message) {
        System.err.println("Warning: field '" + fieldName + "': " + message);
    }
}
