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
import org.apache.ossie.converter.polaris.model.OsiModel;
import org.apache.ossie.converter.polaris.model.OsiModel.*;

import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Exports an Ossie semantic model to an Apache Polaris catalog.
 * <p>
 * Creates namespaces and Iceberg tables in Polaris based on the Ossie model's
 * datasets, mapping Ossie fields to Iceberg schema columns.
 */
public class PolarisExporter {

    private final PolarisClient client;
    private final ObjectMapper objectMapper;

    public PolarisExporter(PolarisClient client) {
        this.client = client;
        this.objectMapper = client.getObjectMapper();
    }

    /**
     * Export the Ossie model to the Polaris catalog.
     * Each semantic model becomes a namespace, and each dataset becomes a table.
     */
    public void exportModel(OsiModel model) throws IOException, InterruptedException {
        for (SemanticModel sm : model.getSemanticModels()) {
            exportSemanticModel(sm);
        }
    }

    /**
     * Export a single semantic model to Polaris.
     */
    public void exportSemanticModel(SemanticModel sm) throws IOException, InterruptedException {
        List<String> namespace = Collections.singletonList(sm.getName());

        // Create namespace with description as property
        Map<String, String> properties = new HashMap<>();
        if (sm.getDescription() != null) {
            properties.put("description", sm.getDescription());
        }
        properties.put("osi.source", "true");
        client.createNamespace(namespace, properties);

        // Create tables for each dataset
        for (Dataset dataset : sm.getDatasets()) {
            String tableJson = buildCreateTableRequest(dataset);
            client.createTable(namespace, tableJson);
        }
    }

    /**
     * Build an Iceberg create-table request JSON from an Ossie dataset.
     */
    String buildCreateTableRequest(Dataset dataset) {
        ObjectNode request = objectMapper.createObjectNode();
        request.put("name", dataset.getName());

        // Build schema
        ObjectNode schema = buildSchema(dataset);
        request.set("schema", schema);

        // Table properties
        ObjectNode properties = objectMapper.createObjectNode();
        if (dataset.getDescription() != null) {
            properties.put("comment", dataset.getDescription());
        }
        if (dataset.getSource() != null) {
            properties.put("osi.source", dataset.getSource());
        }
        request.set("properties", properties);

        return request.toString();
    }

    /**
     * Build an Iceberg schema from an Ossie dataset's fields.
     */
    private ObjectNode buildSchema(Dataset dataset) {
        ObjectNode schema = objectMapper.createObjectNode();
        schema.put("type", "struct");
        schema.put("schema-id", 0);

        ArrayNode fields = schema.putArray("fields");
        List<String> pk = dataset.getPrimaryKey();
        AtomicInteger nextNestedId = new AtomicInteger(dataset.getFields().size() + 1);

        int fieldId = 1;
        for (Field osiField : dataset.getFields()) {
            ObjectNode field = objectMapper.createObjectNode();
            field.put("id", fieldId);
            field.put("name", osiField.getName());
            field.set("type", inferIcebergType(osiField, nextNestedId));
            field.put("required", pk != null && pk.contains(osiField.getName()));
            if (osiField.getDescription() != null) {
                field.put("doc", osiField.getDescription());
            }
            fields.add(field);
            fieldId++;
        }

        // Set identifier field IDs (primary key)
        if (pk != null && !pk.isEmpty()) {
            ArrayNode identifierFieldIds = schema.putArray("identifier-field-ids");
            for (String pkCol : pk) {
                int id = findFieldId(dataset.getFields(), pkCol);
                if (id > 0) {
                    identifierFieldIds.add(id);
                }
            }
        }

        return schema;
    }

    /**
     * Resolve an Iceberg type from an Ossie field.
     * <p>
     * Exact Polaris extension data wins, followed by the portable Ossie datatype.
     * Legacy description, temporal-role, and name heuristics remain as fallbacks for
     * models authored before datatype support.
     */
    private JsonNode inferIcebergType(Field field, AtomicInteger nextNestedId) {
        JsonNode exactType = IcebergTypeMapper.exactIcebergType(field, objectMapper);
        if (exactType != null) {
            String extensionDatatype = IcebergTypeMapper.toOssieDatatype(exactType);
            if (field.getDatatype() != null
                    && !Objects.equals(field.getDatatype(), extensionDatatype)) {
                IcebergTypeMapper.warn(
                        field.getName(),
                        "datatype '" + field.getDatatype() + "' conflicts with exact Iceberg type '"
                                + IcebergTypeMapper.displayIcebergType(exactType)
                                + "'; preserving the POLARIS extension value");
            }
            return IcebergTypeMapper.prepareExactTypeForExport(exactType, nextNestedId);
        }

        JsonNode portableType = IcebergTypeMapper.toDefaultIcebergType(field.getDatatype());
        if (portableType != null) {
            if ("Decimal".equals(field.getDatatype())) {
                IcebergTypeMapper.warn(
                        field.getName(),
                        "Ossie datatype 'Decimal' has no precision or scale; using decimal(18, 2)");
            }
            return portableType;
        }

        if (field.getDatatype() != null) {
            if ("Opaque".equals(field.getDatatype())) {
                IcebergTypeMapper.warn(
                        field.getName(),
                        "Ossie datatype 'Opaque' has no exact Iceberg type in a POLARIS extension; "
                                + "using legacy inference");
            } else {
                IcebergTypeMapper.warn(
                        field.getName(),
                        "unrecognized Ossie datatype '" + field.getDatatype()
                                + "'; using legacy inference");
            }
        }

        // Check the legacy description type hint produced by older importer versions.
        if (field.getDescription() != null && field.getDescription().startsWith("Iceberg type: ")) {
            String typeHint = field.getDescription().substring("Iceberg type: ".length());
            // Strip optional/required suffix
            int parenIdx = typeHint.indexOf(" (");
            if (parenIdx > 0) {
                typeHint = typeHint.substring(0, parenIdx);
            }
            return TextNode.valueOf(typeHint);
        }

        // A time role is only a fallback; it never overrides an explicit datatype.
        if (field.isTime()) {
            return TextNode.valueOf("timestamptz");
        }

        return TextNode.valueOf(inferIcebergTypeFromName(field.getName()));
    }

    private String inferIcebergTypeFromName(String fieldName) {
        String name = fieldName.toLowerCase(Locale.ROOT);
        if (name.endsWith("_id") || name.equals("id")) {
            return "long";
        }
        if (name.endsWith("_date") || name.equals("date")) {
            return "date";
        }
        if (name.endsWith("_at") || name.endsWith("_time") || name.endsWith("_timestamp")) {
            return "timestamptz";
        }
        if (name.endsWith("_amount") || name.endsWith("_price") || name.endsWith("_cost")
                || name.endsWith("_total") || name.equals("amount") || name.equals("price")) {
            return "decimal(18, 2)";
        }
        if (name.endsWith("_count") || name.equals("count") || name.equals("quantity")) {
            return "int";
        }
        if (name.startsWith("is_") || name.startsWith("has_")) {
            return "boolean";
        }

        // Default to string
        return "string";
    }

    /**
     * Find the 1-based field ID by field name.
     */
    private int findFieldId(List<Field> fields, String name) {
        for (int i = 0; i < fields.size(); i++) {
            if (fields.get(i).getName().equals(name)) {
                return i + 1;
            }
        }
        return -1;
    }
}
