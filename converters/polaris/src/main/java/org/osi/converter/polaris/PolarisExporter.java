package org.osi.converter.polaris;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.osi.converter.polaris.model.OsiModel;
import org.osi.converter.polaris.model.OsiModel.*;

import java.io.IOException;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Exports an OSI semantic model to an Apache Polaris catalog.
 * <p>
 * Creates namespaces and Iceberg tables in Polaris based on the OSI model's
 * datasets, mapping OSI fields to Iceberg schema columns.
 */
public class PolarisExporter {

    private final PolarisClient client;
    private final ObjectMapper objectMapper;

    public PolarisExporter(PolarisClient client) {
        this.client = client;
        this.objectMapper = client.getObjectMapper();
    }

    /**
     * Export the OSI model to the Polaris catalog.
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
     * Build an Iceberg create-table request JSON from an OSI dataset.
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
     * Build an Iceberg schema from an OSI dataset's fields.
     */
    private ObjectNode buildSchema(Dataset dataset) {
        ObjectNode schema = objectMapper.createObjectNode();
        schema.put("type", "struct");
        schema.put("schema-id", 0);

        ArrayNode fields = schema.putArray("fields");
        List<String> pk = dataset.getPrimaryKey();

        int fieldId = 1;
        for (Field osiField : dataset.getFields()) {
            ObjectNode field = objectMapper.createObjectNode();
            field.put("id", fieldId);
            field.put("name", osiField.getName());
            field.put("type", inferIcebergType(osiField));
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
     * Infer an Iceberg type from an OSI field.
     * <p>
     * Since OSI fields are expression-based and don't carry explicit type information,
     * we use heuristics based on field name, description, and dimension metadata.
     */
    private String inferIcebergType(Field field) {
        // Check description for Iceberg type hint (from round-trip)
        if (field.getDescription() != null && field.getDescription().startsWith("Iceberg type: ")) {
            String typeHint = field.getDescription().substring("Iceberg type: ".length());
            // Strip optional/required suffix
            int parenIdx = typeHint.indexOf(" (");
            if (parenIdx > 0) {
                typeHint = typeHint.substring(0, parenIdx);
            }
            return typeHint;
        }

        // Time dimension -> timestamp
        if (field.isTime()) {
            return "timestamptz";
        }

        // Name-based heuristics
        String name = field.getName().toLowerCase();
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
