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
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.apache.ossie.converter.polaris.model.OssieModel;
import org.apache.ossie.converter.polaris.model.OssieModel.*;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class OssiePolarisConverterTest {

    @TempDir
    Path tempDirectory;

    private static final String MINIMAL_MODEL =
            "version: \"0.2.0.dev0\"\n"
            + "\n"
            + "name: test_model\n"
            + "description: A test model\n"
            + "datasets:\n"
            + "  - name: orders\n"
            + "    source: catalog.ns.orders\n"
            + "    primary_key: [order_id]\n"
            + "    description: Order fact table\n"
            + "    fields:\n"
            + "      - name: order_id\n"
            + "        datatype: Integer\n"
            + "        expression:\n"
            + "          dialects:\n"
            + "            - dialect: ANSI_SQL\n"
            + "              expression: order_id\n"
            + "      - name: total_amount\n"
            + "        datatype: Decimal\n"
            + "        expression:\n"
            + "          dialects:\n"
            + "            - dialect: ANSI_SQL\n"
            + "              expression: \"quantity * unit_price\"\n"
            + "        description: Computed total\n"
            + "        custom_extensions:\n"
            + "          - vendor_name: POLARIS\n"
            + "            data: '{\"iceberg_type\":\"decimal(18,2)\"}'\n"
            + "      - name: order_date\n"
            + "        datatype: Date\n"
            + "        expression:\n"
            + "          dialects:\n"
            + "            - dialect: ANSI_SQL\n"
            + "              expression: order_date\n"
            + "        dimension:\n"
            + "          is_time: true\n"
            + "  - name: customer\n"
            + "    source: catalog.ns.customer\n"
            + "    primary_key: [customer_id]\n"
            + "    fields:\n"
            + "      - name: customer_id\n"
            + "        datatype: String\n"
            + "        expression:\n"
            + "          dialects:\n"
            + "            - dialect: ANSI_SQL\n"
            + "              expression: customer_id\n"
            + "      - name: full_name\n"
            + "        datatype: String\n"
            + "        expression:\n"
            + "          dialects:\n"
            + "            - dialect: ANSI_SQL\n"
            + "              expression: \"first_name || ' ' || last_name\"\n"
            + "relationships:\n"
            + "  - name: orders_to_customer\n"
            + "    from: orders\n"
            + "    to: customer\n"
            + "    from_columns: [customer_id]\n"
            + "    to_columns: [customer_id]\n"
            + "metrics:\n"
            + "  - name: total_revenue\n"
            + "    expression:\n"
            + "      dialects:\n"
            + "        - dialect: ANSI_SQL\n"
            + "          expression: SUM(orders.total_amount)\n"
            + "    description: Total revenue across all orders\n";

    // -- Parser tests -------------------------------------------------------

    @Test
    void testParseMinimalModel() {
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        assertEquals("0.2.0.dev0", model.getVersion());
        assertNotNull(model.getSemanticModel());

        SemanticModel sm = model.getSemanticModel();
        assertEquals("test_model", sm.getName());
        assertEquals(2, sm.getDatasets().size());
        assertEquals(1, sm.getRelationships().size());
        assertEquals(1, sm.getMetrics().size());
    }

    @Test
    void testParseDatasetFields() {
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        Dataset orders = model.getSemanticModel().getDatasets().get(0);
        assertEquals("orders", orders.getName());
        assertEquals("catalog.ns.orders", orders.getSource());
        assertEquals(3, orders.getFields().size());
        assertEquals(Collections.singletonList("order_id"), orders.getPrimaryKey());

        Field computed = orders.getFields().get(1);
        assertEquals("total_amount", computed.getName());
        assertEquals("Decimal", computed.getDatatype());
        assertEquals("quantity * unit_price", computed.getExpressions().get(0).getExpression());
    }

    @Test
    void testParseTimeDimension() {
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        Dataset orders = model.getSemanticModel().getDatasets().get(0);
        Field orderDate = orders.getFields().get(2);
        assertEquals("order_date", orderDate.getName());
        assertEquals("Date", orderDate.getDatatype());
        assertTrue(orderDate.isTime());
    }

    @Test
    void testParseRelationship() {
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        Relationship rel = model.getSemanticModel().getRelationships().get(0);
        assertEquals("orders_to_customer", rel.getName());
        assertEquals("orders", rel.getFrom());
        assertEquals("customer", rel.getTo());
        assertEquals(Collections.singletonList("customer_id"), rel.getFromColumns());
        assertEquals(Collections.singletonList("customer_id"), rel.getToColumns());
    }

    // -- YAML generation tests ----------------------------------------------

    @Test
    void testYamlGenerationRoundTrip() {
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        OssieYamlGenerator generator = new OssieYamlGenerator();
        String yaml = generator.generate(model);

        // Verify key elements are present in generated YAML
        assertTrue(yaml.contains("version: \"0.2.0.dev0\""));
        assertTrue(yaml.contains("name: test_model"));
        assertTrue(yaml.contains("name: orders"));
        assertTrue(yaml.contains("source: catalog.ns.orders"));
        assertTrue(yaml.contains("primary_key: [order_id]"));
        assertTrue(yaml.contains("name: total_amount"));
        assertTrue(yaml.contains("datatype: Decimal"));
        assertTrue(yaml.contains("datatype: Date"));
        assertTrue(yaml.contains("is_time: true"));
        assertTrue(yaml.contains("name: orders_to_customer"));
        assertTrue(yaml.contains("from_columns: [customer_id]"));
        assertTrue(yaml.contains("name: total_revenue"));
        assertTrue(yaml.contains("SUM(orders.total_amount)"));

        // Re-parse the generated YAML to verify it's valid
        OssieModel reparsed = parser.parse(
                new ByteArrayInputStream(yaml.getBytes(StandardCharsets.UTF_8)));
        assertNotNull(reparsed.getSemanticModel());
        assertEquals("test_model", reparsed.getSemanticModel().getName());
        assertEquals(2, reparsed.getSemanticModel().getDatasets().size());
    }

    // -- Exporter tests (Iceberg schema generation) -------------------------

    @Test
    void testExporterBuildCreateTableRequest() throws Exception {
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        PolarisClient client = new PolarisClient("http://localhost:8181", "test_catalog");
        PolarisExporter exporter = new PolarisExporter(client);

        Dataset orders = model.getSemanticModel().getDatasets().get(0);
        String json = exporter.buildCreateTableRequest(orders);

        ObjectMapper mapper = new ObjectMapper();
        JsonNode root = mapper.readTree(json);

        assertEquals("orders", root.get("name").asText());

        // Verify schema
        JsonNode schema = root.get("schema");
        assertNotNull(schema);
        assertEquals("struct", schema.get("type").asText());

        JsonNode fields = schema.get("fields");
        assertNotNull(fields);
        assertEquals(3, fields.size());

        // order_id should be required (it's in the primary key)
        JsonNode orderIdField = fields.get(0);
        assertEquals("order_id", orderIdField.get("name").asText());
        assertTrue(orderIdField.get("required").asBoolean());
        assertEquals("long", orderIdField.get("type").asText());

        // total_amount should infer decimal type
        JsonNode amountField = fields.get(1);
        assertEquals("total_amount", amountField.get("name").asText());
        assertFalse(amountField.get("required").asBoolean());
        assertEquals("decimal(18,2)", amountField.get("type").asText());

        // Explicit datatype determines the physical type independently of its time role.
        JsonNode dateField = fields.get(2);
        assertEquals("order_date", dateField.get("name").asText());
        assertEquals("date", dateField.get("type").asText());

        // Verify identifier-field-ids for primary key
        JsonNode identifierFieldIds = schema.get("identifier-field-ids");
        assertNotNull(identifierFieldIds);
        assertEquals(1, identifierFieldIds.size());
        assertEquals(1, identifierFieldIds.get(0).asInt()); // order_id is field 1

        // Verify properties
        JsonNode properties = root.get("properties");
        assertEquals("Order fact table", properties.get("comment").asText());
        assertEquals("catalog.ns.orders", properties.get("ossie.source").asText());
    }

    // -- Importer tests (Iceberg metadata parsing) --------------------------

    @Test
    void testImporterMapTableToDataset() throws Exception {
        // Simulate Iceberg table metadata JSON
        String tableMetadataJson = "{\n"
                + "  \"metadata\": {\n"
                + "    \"format-version\": 2,\n"
                + "    \"table-uuid\": \"abc-123\",\n"
                + "    \"current-schema-id\": 0,\n"
                + "    \"schemas\": [{\n"
                + "      \"schema-id\": 0,\n"
                + "      \"type\": \"struct\",\n"
                + "      \"fields\": [\n"
                + "        {\"id\": 1, \"name\": \"id\", \"type\": \"long\", \"required\": true},\n"
                + "        {\"id\": 2, \"name\": \"name\", \"type\": \"string\", \"required\": false},\n"
                + "        {\"id\": 3, \"name\": \"created_at\", \"type\": \"timestamptz\", \"required\": false},\n"
                + "        {\"id\": 4, \"name\": \"amount\", \"type\": \"decimal(18,2)\", \"required\": false},\n"
                + "        {\"id\": 5, \"name\": \"event_nanos\", \"type\": \"timestamp_ns\", \"required\": false},\n"
                + "        {\"id\": 6, \"name\": \"tags\", \"type\": {\"type\": \"list\", \"element-id\": 99, \"element\": \"string\", \"element-required\": false}, \"required\": false}\n"
                + "      ],\n"
                + "      \"identifier-field-ids\": [1]\n"
                + "    }],\n"
                + "    \"properties\": {\n"
                + "      \"owner\": \"test_user\"\n"
                + "    }\n"
                + "  }\n"
                + "}";

        ObjectMapper mapper = new ObjectMapper();
        JsonNode tableMetadata = mapper.readTree(tableMetadataJson);

        PolarisClient client = new FakePolarisClient(tableMetadata);
        PolarisImporter importer = new PolarisImporter(client);
        List<OssieModel> models = importer.importCatalog();
        assertEquals(1, models.size());
        OssieModel model = models.get(0);

        Dataset ds = model.getSemanticModel().getDatasets().get(0);
        assertEquals("test_table", ds.getName());
        assertEquals("test_catalog.test_ns.test_table", ds.getSource());
        assertEquals(Collections.singletonList("id"), ds.getPrimaryKey());
        assertEquals(6, ds.getFields().size());

        assertEquals("Integer", ds.getFields().get(0).getDatatype());
        assertEquals("String", ds.getFields().get(1).getDatatype());
        assertEquals("DateTimeTz", ds.getFields().get(2).getDatatype());
        assertTrue(ds.getFields().get(2).isTime());
        assertEquals("Decimal", ds.getFields().get(3).getDatatype());
        assertEquals("DateTime", ds.getFields().get(4).getDatatype());
        assertTrue(ds.getFields().get(4).isTime());
        assertEquals("Opaque", ds.getFields().get(5).getDatatype());

        JsonNode tagsExtension = mapper.readTree(
                ds.getFields().get(5).getCustomExtensions().get(0).getData());
        assertEquals("list", tagsExtension.path("iceberg_type").path("type").asText());
        assertEquals(99, tagsExtension.path("iceberg_type").path("element-id").asInt());

        // Generate YAML and verify
        OssieYamlGenerator generator = new OssieYamlGenerator();
        String yaml = generator.generate(model);

        assertTrue(yaml.contains("name: test_table"));
        assertTrue(yaml.contains("source: test_catalog.test_ns.test_table"));
        assertTrue(yaml.contains("primary_key: [id]"));
        assertTrue(yaml.contains("name: id"));
        assertTrue(yaml.contains("name: name"));
        assertTrue(yaml.contains("name: created_at"));
        assertTrue(yaml.contains("is_time: true"));
        assertTrue(yaml.contains("name: amount"));
        assertTrue(yaml.contains("name: tags"));
        assertTrue(yaml.contains("datatype: Integer"));
        assertTrue(yaml.contains("datatype: DateTimeTz"));
        assertTrue(yaml.contains("datatype: DateTime"));
        assertTrue(yaml.contains("datatype: Decimal"));
        assertTrue(yaml.contains("datatype: Opaque"));
        assertTrue(yaml.contains("vendor_name: POLARIS"));

        // Reparse the generated Ossie YAML and export it again. Exact physical
        // distinctions survive, while nested IDs are regenerated for the new schema.
        OssieModel reparsed = new OssieModelParser().parse(
                new ByteArrayInputStream(yaml.getBytes(StandardCharsets.UTF_8)));
        Dataset reparsedDataset = reparsed.getSemanticModel().getDatasets().get(0);
        JsonNode exportedSchema = mapper.readTree(
                new PolarisExporter(client).buildCreateTableRequest(reparsedDataset)).path("schema");
        JsonNode exportedFields = exportedSchema.path("fields");
        assertEquals("long", exportedFields.get(0).path("type").asText());
        assertEquals("timestamptz", exportedFields.get(2).path("type").asText());
        assertEquals("decimal(18,2)", exportedFields.get(3).path("type").asText());
        assertEquals("timestamp_ns", exportedFields.get(4).path("type").asText());
        assertEquals("list", exportedFields.get(5).path("type").path("type").asText());
        assertEquals(7, exportedFields.get(5).path("type").path("element-id").asInt());
    }

    @Test
    void testDatatypePrecedenceAndLegacyFallbacks() throws Exception {
        Dataset ds = new Dataset();
        ds.setName("precedence_test");
        ds.setSource("cat.ns.precedence_test");

        Field typedStringWithTimeRole = makeField("typed_string", true);
        typedStringWithTimeRole.setDatatype("String");

        Field typedLocalTimestamp = makeField("local_timestamp", false);
        typedLocalTimestamp.setDatatype("DateTime");

        Field legacyUuid = makeField("legacy_value", false);
        legacyUuid.setDescription("Iceberg type: uuid (optional)");

        Field exactConflict = makeField("exact_conflict", false);
        exactConflict.setDatatype("String");
        exactConflict.setCustomExtensions(Collections.singletonList(
                new CustomExtension("POLARIS", "{\"iceberg_type\":\"int\"}")));

        Field opaqueWithoutExtension = makeField("opaque_id", false);
        opaqueWithoutExtension.setDatatype("Opaque");

        Field decimalWithoutExtension = makeField("amount", false);
        decimalWithoutExtension.setDatatype("Decimal");

        ds.setFields(java.util.Arrays.asList(
                typedStringWithTimeRole,
                typedLocalTimestamp,
                legacyUuid,
                exactConflict,
                opaqueWithoutExtension,
                decimalWithoutExtension));

        PolarisClient client = new PolarisClient("http://localhost:8181", "cat");
        ByteArrayOutputStream warningBytes = new ByteArrayOutputStream();
        PrintStream originalError = System.err;
        String requestJson;
        try {
            System.setErr(new PrintStream(warningBytes, true, StandardCharsets.UTF_8));
            requestJson = new PolarisExporter(client).buildCreateTableRequest(ds);
        } finally {
            System.setErr(originalError);
        }
        JsonNode fields = new ObjectMapper().readTree(requestJson).path("schema").path("fields");

        assertEquals("string", fields.get(0).path("type").asText());
        assertEquals("timestamp", fields.get(1).path("type").asText());
        assertEquals("uuid", fields.get(2).path("type").asText());
        assertEquals("int", fields.get(3).path("type").asText());
        assertEquals("long", fields.get(4).path("type").asText());
        assertEquals("decimal(18, 2)", fields.get(5).path("type").asText());

        String warnings = warningBytes.toString(StandardCharsets.UTF_8);
        assertTrue(warnings.contains("conflicts with exact Iceberg type 'int'"));
        assertTrue(warnings.contains("datatype 'Opaque' has no exact Iceberg type"));
        assertTrue(warnings.contains("datatype 'Decimal' has no precision or scale"));
    }

    @Test
    void testTypeInference() throws Exception {
        // Test that the exporter correctly infers Iceberg types from field names
        OssieModel model = new OssieModel();
        model.setVersion("0.2.0.dev0");

        SemanticModel sm = new SemanticModel();
        sm.setName("type_test");

        Dataset ds = new Dataset();
        ds.setName("test_table");
        ds.setSource("cat.ns.test_table");
        ds.setPrimaryKey(Collections.singletonList("user_id"));

        List<Field> fields = new java.util.ArrayList<>();
        fields.add(makeField("user_id", false));
        fields.add(makeField("created_at", false));
        fields.add(makeField("order_date", false));
        fields.add(makeField("total_amount", false));
        fields.add(makeField("quantity", false));
        fields.add(makeField("is_active", false));
        fields.add(makeField("description", false));
        fields.add(makeField("event_time", true));
        ds.setFields(fields);

        sm.setDatasets(Collections.singletonList(ds));
        model.setSemanticModel(sm);

        PolarisClient client = new PolarisClient("http://localhost:8181", "cat");
        PolarisExporter exporter = new PolarisExporter(client);

        String json = exporter.buildCreateTableRequest(ds);
        ObjectMapper mapper = new ObjectMapper();
        JsonNode root = mapper.readTree(json);
        JsonNode schemaFields = root.get("schema").get("fields");

        assertEquals("long", schemaFields.get(0).get("type").asText());          // user_id
        assertEquals("timestamptz", schemaFields.get(1).get("type").asText());    // created_at
        assertEquals("date", schemaFields.get(2).get("type").asText());           // order_date
        assertEquals("decimal(18, 2)", schemaFields.get(3).get("type").asText()); // total_amount
        assertEquals("int", schemaFields.get(4).get("type").asText());            // quantity
        assertEquals("boolean", schemaFields.get(5).get("type").asText());        // is_active
        assertEquals("string", schemaFields.get(6).get("type").asText());         // description
        assertEquals("timestamptz", schemaFields.get(7).get("type").asText());    // event_time (time dimension)
    }

    @Test
    void testEmptyModel() {
        String emptyYaml = "version: \"0.2.0.dev0\"\n";
        OssieModelParser parser = new OssieModelParser();
        assertThrows(IllegalArgumentException.class, () -> parser.parse(
                new ByteArrayInputStream(emptyYaml.getBytes(StandardCharsets.UTF_8))));
    }

    @Test
    void testRejectsLegacyWrappedDocuments() {
        for (String value : List.of("[]", "[{name: legacy, datasets: []}]", "{name: legacy, datasets: []}")) {
            String yaml = "version: 0.2.0.dev0\nsemantic_model: " + value + "\n";
            IllegalArgumentException exception = assertThrows(IllegalArgumentException.class,
                    () -> new OssieModelParser().parse(
                            new ByteArrayInputStream(yaml.getBytes(StandardCharsets.UTF_8))));
            assertTrue(exception.getMessage().contains("Legacy semantic_model wrappers"));
        }
    }

    @Test
    void testEmptyDatasetListRoundTrips() {
        String yaml = "version: 0.2.0.dev0\nname: empty\ndatasets: []\n";
        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(new ByteArrayInputStream(yaml.getBytes(StandardCharsets.UTF_8)));
        String generated = new OssieYamlGenerator().generate(model);
        assertFalse(generated.contains("semantic_model:"));
        OssieModel reparsed = parser.parse(new ByteArrayInputStream(generated.getBytes(StandardCharsets.UTF_8)));
        assertEquals("empty", reparsed.getSemanticModel().getName());
        assertTrue(reparsed.getSemanticModel().getDatasets().isEmpty());
    }

    @Test
    void testBulkImportWritesSeparateDocumentsWithSafeDistinctNames() throws Exception {
        PolarisClient client = namespaceClient(List.of(
                List.of("a_b"), List.of("a", "b"), List.of("../outside")));
        Path directory = tempDirectory.resolve("models");
        OssiePolarisConverter.doImport(client, null, directory.toString());

        List<Path> files;
        try (var paths = Files.list(directory)) {
            files = paths.sorted().toList();
        }
        assertEquals(List.of("0001-___outside.yaml", "0002-a_b.yaml", "0003-a_b.yaml"),
                files.stream().map(path -> path.getFileName().toString()).toList());
        assertEquals(List.of("../outside", "a_b", "a_b"), files.stream().map(path -> {
            try {
                return new OssieModelParser().parse(path).getSemanticModel().getName();
            } catch (Exception exception) {
                throw new AssertionError(exception);
            }
        }).toList());
        assertEquals("test_catalog.a.b.test_table",
                new OssieModelParser().parse(files.get(1)).getSemanticModel().getDatasets().get(0).getSource());
        assertEquals("test_catalog.a_b.test_table",
                new OssieModelParser().parse(files.get(2)).getSemanticModel().getDatasets().get(0).getSource());

        // API enumeration order does not change the output filenames or contents.
        Path reordered = tempDirectory.resolve("reordered");
        OssiePolarisConverter.doImport(namespaceClient(List.of(
                List.of("../outside"), List.of("a", "b"), List.of("a_b"))), null, reordered.toString());
        for (Path file : files) {
            assertEquals(Files.readString(file), Files.readString(reordered.resolve(file.getFileName())));
        }
    }

    @Test
    void testMultipleNamespacesRequireOutputDirectory() {
        PolarisClient client = namespaceClient(List.of(List.of("sales"), List.of("support")));
        Path output = tempDirectory.resolve("model.yaml");
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class,
                () -> OssiePolarisConverter.doImport(client, output.toString(), null));
        assertTrue(exception.getMessage().contains("--output-dir"));
        assertFalse(Files.exists(output));
        assertThrows(IllegalArgumentException.class, () -> OssiePolarisConverter.doImport(client, null, null));
    }

    @Test
    void testSingleNamespaceFileAndStdout() throws Exception {
        PolarisClient client = namespaceClient(List.of(List.of("sales")));
        Path output = tempDirectory.resolve("model.yaml");
        OssiePolarisConverter.doImport(client, output.toString(), null);
        assertEquals("sales", new OssieModelParser().parse(output).getSemanticModel().getName());

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        PrintStream originalOutput = System.out;
        try {
            System.setOut(new PrintStream(bytes, true, StandardCharsets.UTF_8));
            OssiePolarisConverter.doImport(client, null, null);
        } finally {
            System.setOut(originalOutput);
        }
        assertEquals("sales", new OssieModelParser().parse(
                new ByteArrayInputStream(bytes.toByteArray())).getSemanticModel().getName());
    }

    @Test
    void testBulkImportRefusesExistingFilesBeforeWriting() throws Exception {
        PolarisClient client = namespaceClient(List.of(List.of("sales"), List.of("support")));
        Path existing = tempDirectory.resolve("0002-support.yaml");
        Files.writeString(existing, "keep this file");
        assertThrows(java.io.IOException.class,
                () -> OssiePolarisConverter.doImport(client, null, tempDirectory.toString()));
        assertFalse(Files.exists(tempDirectory.resolve("0001-sales.yaml")));
        assertEquals("keep this file", Files.readString(existing));
    }

    @Test
    void testEmptyCatalogDoesNotWriteInvalidDocument() throws Exception {
        Path output = tempDirectory.resolve("empty.yaml");
        OssiePolarisConverter.doImport(namespaceClient(List.of()), output.toString(), null);
        assertFalse(Files.exists(output));
    }

    @Test
    void testOutputOptionsAreMutuallyExclusive() {
        assertThrows(IllegalArgumentException.class, () -> OssiePolarisConverter.doImport(
                namespaceClient(List.of()), "model.yaml", tempDirectory.toString()));
    }

    // -- Helpers ------------------------------------------------------------

    private PolarisClient namespaceClient(List<List<String>> namespaces) {
        return new FakePolarisClient(new ObjectMapper().createObjectNode()) {
            @Override
            public List<List<String>> listNamespaces() {
                return namespaces;
            }
        };
    }

    private Field makeField(String name, boolean isTime) {
        Field f = new Field();
        f.setName(name);
        f.setExpressions(Collections.singletonList(new DialectExpression("ANSI_SQL", name)));
        f.setTime(isTime);
        return f;
    }

    private static class FakePolarisClient extends PolarisClient {
        private final JsonNode tableMetadata;

        FakePolarisClient(JsonNode tableMetadata) {
            super("http://localhost:8181", "test_catalog");
            this.tableMetadata = tableMetadata;
        }

        @Override
        public List<List<String>> listNamespaces() {
            return Collections.singletonList(Collections.singletonList("test_ns"));
        }

        @Override
        public List<String> listTables(List<String> namespace) {
            return Collections.singletonList("test_table");
        }

        @Override
        public JsonNode loadTable(List<String> namespace, String tableName) {
            return tableMetadata;
        }
    }
}
