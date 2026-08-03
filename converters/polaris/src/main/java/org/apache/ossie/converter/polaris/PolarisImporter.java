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
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.ossie.converter.polaris.model.OsiModel;
import org.apache.ossie.converter.polaris.model.OsiModel.*;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Imports Apache Polaris catalog metadata into an Ossie semantic model.
 * <p>
 * Reads namespaces and tables from a Polaris catalog via the Iceberg REST API,
 * maps Iceberg table schemas to Ossie datasets and fields, and produces a complete
 * {@link OsiModel}.
 */
public class PolarisImporter {

    private final PolarisClient client;

    public PolarisImporter(PolarisClient client) {
        this.client = client;
    }

    /**
     * Import all tables from all namespaces in the catalog into an Ossie model.
     * Each namespace becomes a separate semantic model.
     */
    public OsiModel importCatalog() throws IOException, InterruptedException {
        OsiModel model = new OsiModel();
        model.setVersion("0.2.0.dev0");

        List<List<String>> namespaces = client.listNamespaces();

        for (List<String> namespace : namespaces) {
            SemanticModel sm = importNamespace(namespace);
            if (sm != null && !sm.getDatasets().isEmpty()) {
                model.getSemanticModels().add(sm);
            }
        }

        return model;
    }

    /**
     * Import all tables from a specific namespace into a semantic model.
     */
    public SemanticModel importNamespace(List<String> namespace) throws IOException, InterruptedException {
        String nsName = String.join("_", namespace);

        SemanticModel sm = new SemanticModel();
        sm.setName(nsName);
        sm.setDescription("Imported from Apache Polaris catalog: " + client.getCatalog()
                + ", namespace: " + String.join(".", namespace));

        List<String> tableNames = client.listTables(namespace);
        List<Dataset> datasets = new ArrayList<>();

        for (String tableName : tableNames) {
            JsonNode tableMetadata = client.loadTable(namespace, tableName);
            Dataset dataset = mapTableToDataset(namespace, tableName, tableMetadata);
            datasets.add(dataset);
        }

        sm.setDatasets(datasets);
        return sm;
    }

    /**
     * Map an Iceberg table's metadata to an Ossie dataset.
     */
    private Dataset mapTableToDataset(List<String> namespace, String tableName, JsonNode tableMetadata) {
        Dataset dataset = new Dataset();
        dataset.setName(tableName);

        // Source: catalog.namespace.table
        String source = client.getCatalog() + "." + String.join(".", namespace) + "." + tableName;
        dataset.setSource(source);

        // Extract schema from metadata
        JsonNode metadata = tableMetadata.get("metadata");
        if (metadata != null) {
            // Get current schema
            JsonNode currentSchemaId = metadata.get("current-schema-id");
            JsonNode schemas = metadata.get("schemas");
            JsonNode schema = findCurrentSchema(schemas, currentSchemaId);

            if (schema != null) {
                List<Field> fields = mapSchemaFields(schema);
                dataset.setFields(fields);

                // Extract identifier fields as primary key
                JsonNode identifierFieldIds = schema.get("identifier-field-ids");
                if (identifierFieldIds != null && identifierFieldIds.isArray() && identifierFieldIds.size() > 0) {
                    List<String> pkColumns = resolveFieldNames(schema, identifierFieldIds);
                    dataset.setPrimaryKey(pkColumns);
                }
            }

            // Store Polaris-specific table properties as custom extension
            JsonNode properties = metadata.get("properties");
            if (properties != null && properties.isObject() && properties.size() > 0) {
                CustomExtension ext = new CustomExtension("COMMON", properties.toString());
                dataset.setCustomExtensions(Collections.singletonList(ext));
            }
        }

        return dataset;
    }

    /**
     * Find the current schema from the schemas array using the current-schema-id.
     */
    private JsonNode findCurrentSchema(JsonNode schemas, JsonNode currentSchemaId) {
        if (schemas == null || !schemas.isArray()) {
            return null;
        }

        int targetId = (currentSchemaId != null) ? currentSchemaId.asInt(0) : 0;

        for (JsonNode schema : schemas) {
            JsonNode schemaId = schema.get("schema-id");
            if (schemaId != null && schemaId.asInt() == targetId) {
                return schema;
            }
        }

        // Fallback: return the last schema (most recent)
        if (schemas.size() > 0) {
            return schemas.get(schemas.size() - 1);
        }
        return null;
    }

    /**
     * Map Iceberg schema fields to Ossie fields.
     */
    private List<Field> mapSchemaFields(JsonNode schema) {
        List<Field> fields = new ArrayList<>();
        JsonNode columns = schema.get("fields");
        if (columns == null || !columns.isArray()) {
            return fields;
        }

        for (JsonNode column : columns) {
            Field field = mapColumnToField(column);
            if (field != null) {
                fields.add(field);
            }
        }
        return fields;
    }

    /**
     * Map a single Iceberg column to an Ossie field.
     */
    private Field mapColumnToField(JsonNode column) {
        String name = column.get("name").asText();
        JsonNode icebergTypeNode = column.get("type");
        String icebergType = IcebergTypeMapper.displayIcebergType(icebergTypeNode);
        String datatype = IcebergTypeMapper.toOssieDatatype(icebergTypeNode);

        Field field = new Field();
        field.setName(name);
        field.setDatatype(datatype);

        // The expression is just the column name (direct mapping)
        DialectExpression expr = new DialectExpression("ANSI_SQL", name);
        field.setExpressions(Collections.singletonList(expr));

        // Preserve the converter's existing temporal-role classification.
        if (IcebergTypeMapper.isTemporalDatatype(datatype)) {
            field.setTime(true);
        }

        // DataType is intentionally logical and loses physical details such as
        // integer width, decimal precision/scale, timestamp precision, and nested
        // structure. Preserve the complete Iceberg type for exact round trips.
        if (icebergTypeNode != null) {
            ObjectNode extensionData = client.getObjectMapper().createObjectNode();
            extensionData.set(IcebergTypeMapper.ICEBERG_TYPE_KEY, icebergTypeNode.deepCopy());
            field.setCustomExtensions(Collections.singletonList(
                    new CustomExtension(IcebergTypeMapper.POLARIS_VENDOR, extensionData.toString())));
        }

        // Add type information as description
        field.setDescription("Iceberg type: " + icebergType
                + (isRequired(column) ? " (required)" : " (optional)"));

        return field;
    }

    private boolean isRequired(JsonNode column) {
        JsonNode required = column.get("required");
        return required != null && required.asBoolean(false);
    }

    /**
     * Resolve field IDs to field names from the schema.
     */
    private List<String> resolveFieldNames(JsonNode schema, JsonNode fieldIds) {
        List<String> names = new ArrayList<>();
        JsonNode fields = schema.get("fields");
        if (fields == null) {
            return names;
        }

        for (JsonNode fieldId : fieldIds) {
            int id = fieldId.asInt();
            for (JsonNode field : fields) {
                if (field.has("id") && field.get("id").asInt() == id) {
                    names.add(field.get("name").asText());
                    break;
                }
            }
        }
        return names;
    }
}
