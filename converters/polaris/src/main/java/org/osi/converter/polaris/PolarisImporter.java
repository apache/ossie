package org.osi.converter.polaris;

import com.fasterxml.jackson.databind.JsonNode;
import org.osi.converter.polaris.model.OsiModel;
import org.osi.converter.polaris.model.OsiModel.*;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Imports Apache Polaris catalog metadata into an OSI semantic model.
 * <p>
 * Reads namespaces and tables from a Polaris catalog via the Iceberg REST API,
 * maps Iceberg table schemas to OSI datasets and fields, and produces a complete
 * {@link OsiModel}.
 */
public class PolarisImporter {

    private final PolarisClient client;

    public PolarisImporter(PolarisClient client) {
        this.client = client;
    }

    /**
     * Import all tables from all namespaces in the catalog into an OSI model.
     * Each namespace becomes a separate semantic model.
     */
    public OsiModel importCatalog() throws IOException, InterruptedException {
        OsiModel model = new OsiModel();
        model.setVersion("0.1.1");

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
     * Map an Iceberg table's metadata to an OSI dataset.
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
     * Map Iceberg schema fields to OSI fields.
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
     * Map a single Iceberg column to an OSI field.
     */
    private Field mapColumnToField(JsonNode column) {
        String name = column.get("name").asText();
        String icebergType = resolveType(column.get("type"));

        Field field = new Field();
        field.setName(name);

        // The expression is just the column name (direct mapping)
        DialectExpression expr = new DialectExpression("ANSI_SQL", name);
        field.setExpressions(Collections.singletonList(expr));

        // Detect time-based dimensions from Iceberg types
        if (isTemporalType(icebergType)) {
            field.setTime(true);
        }

        // Add type information as description
        field.setDescription("Iceberg type: " + icebergType
                + (isRequired(column) ? " (required)" : " (optional)"));

        return field;
    }

    /**
     * Resolve an Iceberg type node to a type string.
     * Handles both primitive types (strings) and complex types (struct, list, map).
     */
    private String resolveType(JsonNode typeNode) {
        if (typeNode == null) {
            return "unknown";
        }
        if (typeNode.isTextual()) {
            return typeNode.asText();
        }
        if (typeNode.isObject()) {
            String type = typeNode.has("type") ? typeNode.get("type").asText() : "unknown";
            switch (type) {
                case "struct":
                    return "struct";
                case "list":
                    String elementType = resolveType(typeNode.path("element"));
                    return "list<" + elementType + ">";
                case "map":
                    String keyType = resolveType(typeNode.path("key"));
                    String valueType = resolveType(typeNode.path("value"));
                    return "map<" + keyType + ", " + valueType + ">";
                case "fixed":
                    return "fixed[" + typeNode.path("length").asInt() + "]";
                case "decimal":
                    return "decimal(" + typeNode.path("precision").asInt()
                            + ", " + typeNode.path("scale").asInt() + ")";
                default:
                    return type;
            }
        }
        return "unknown";
    }

    private boolean isTemporalType(String icebergType) {
        return "timestamp".equals(icebergType)
                || "timestamptz".equals(icebergType)
                || "date".equals(icebergType)
                || "time".equals(icebergType);
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
