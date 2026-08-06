package org.osi.converter.spark;

import org.junit.jupiter.api.Test;
import org.osi.converter.spark.model.OsiModel;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.*;

class OsiSparkConverterTest {

    private static final String MINIMAL_MODEL =
            "version: \"0.1.1\"\n"
            + "\n"
            + "semantic_model:\n"
            + "  - name: test_model\n"
            + "    description: A test model\n"
            + "    datasets:\n"
            + "      - name: orders\n"
            + "        source: db.public.orders\n"
            + "        primary_key: [order_id]\n"
            + "        description: Order fact table\n"
            + "        fields:\n"
            + "          - name: order_id\n"
            + "            expression:\n"
            + "              dialects:\n"
            + "                - dialect: ANSI_SQL\n"
            + "                  expression: order_id\n"
            + "          - name: total_amount\n"
            + "            expression:\n"
            + "              dialects:\n"
            + "                - dialect: ANSI_SQL\n"
            + "                  expression: quantity * unit_price\n"
            + "            description: Computed total\n"
            + "      - name: customer\n"
            + "        source: db.public.customer\n"
            + "        primary_key: [customer_id]\n"
            + "        fields:\n"
            + "          - name: customer_id\n"
            + "            expression:\n"
            + "              dialects:\n"
            + "                - dialect: ANSI_SQL\n"
            + "                  expression: customer_id\n"
            + "          - name: full_name\n"
            + "            expression:\n"
            + "              dialects:\n"
            + "                - dialect: ANSI_SQL\n"
            + "                  expression: \"first_name || ' ' || last_name\"\n"
            + "    relationships:\n"
            + "      - name: orders_to_customer\n"
            + "        from: orders\n"
            + "        to: customer\n"
            + "        from_columns: [customer_id]\n"
            + "        to_columns: [customer_id]\n"
            + "    metrics:\n"
            + "      - name: total_revenue\n"
            + "        expression:\n"
            + "          dialects:\n"
            + "            - dialect: ANSI_SQL\n"
            + "              expression: SUM(orders.total_amount)\n"
            + "        description: Total revenue across all orders\n";

    @Test
    void testParseMinimalModel() {
        OsiModelParser parser = new OsiModelParser();
        OsiModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        assertEquals("0.1.1", model.getVersion());
        assertEquals(1, model.getSemanticModels().size());

        OsiModel.SemanticModel sm = model.getSemanticModels().get(0);
        assertEquals("test_model", sm.getName());
        assertEquals(2, sm.getDatasets().size());
        assertEquals(1, sm.getRelationships().size());
        assertEquals(1, sm.getMetrics().size());
    }

    @Test
    void testParseDatasetFields() {
        OsiModelParser parser = new OsiModelParser();
        OsiModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        OsiModel.Dataset orders = model.getSemanticModels().get(0).getDatasets().get(0);
        assertEquals("orders", orders.getName());
        assertEquals("db.public.orders", orders.getSource());
        assertEquals(2, orders.getFields().size());

        OsiModel.Field computed = orders.getFields().get(1);
        assertEquals("total_amount", computed.getName());
        assertEquals("quantity * unit_price", computed.getExpressions().get(0).getExpression());
    }

    @Test
    void testGenerateContainsExpectedFunctions() {
        OsiModelParser parser = new OsiModelParser();
        OsiModel model = parser.parse(
                new ByteArrayInputStream(MINIMAL_MODEL.getBytes(StandardCharsets.UTF_8)));

        SparkCodeGenerator generator = new SparkCodeGenerator("ANSI_SQL");
        String code = generator.generate(model);

        // Dataset loaders
        assertTrue(code.contains("def load_orders(spark: SparkSession)"));
        assertTrue(code.contains("def load_customer(spark: SparkSession)"));
        assertTrue(code.contains("def load_all_datasets(spark: SparkSession)"));

        // Computed columns
        assertTrue(code.contains("df.withColumn(\"total_amount\", F.expr(\"quantity * unit_price\"))"));
        assertTrue(code.contains("df.withColumn(\"full_name\", F.expr(\"first_name || ' ' || last_name\"))"));

        // Temp views
        assertTrue(code.contains("df.createOrReplaceTempView(\"orders\")"));

        // Join helper
        assertTrue(code.contains("def join_orders_to_customer("));

        // Metric
        assertTrue(code.contains("def compute_total_revenue(spark: SparkSession)"));
        assertTrue(code.contains("SUM(orders.total_amount) AS total_revenue"));
    }

    @Test
    void testGenerateWithDatabricksDialect() {
        String multiDialect =
                "version: \"0.1.1\"\n"
                + "semantic_model:\n"
                + "  - name: multi\n"
                + "    datasets:\n"
                + "      - name: sales\n"
                + "        source: catalog.schema.sales\n"
                + "        fields:\n"
                + "          - name: amount\n"
                + "            expression:\n"
                + "              dialects:\n"
                + "                - dialect: ANSI_SQL\n"
                + "                  expression: amount\n"
                + "                - dialect: DATABRICKS\n"
                + "                  expression: \"CAST(amount AS DECIMAL(18,2))\"\n";

        OsiModelParser parser = new OsiModelParser();
        OsiModel model = parser.parse(
                new ByteArrayInputStream(multiDialect.getBytes(StandardCharsets.UTF_8)));

        SparkCodeGenerator generator = new SparkCodeGenerator("DATABRICKS");
        String code = generator.generate(model);

        assertTrue(code.contains("CAST(amount AS DECIMAL(18,2))"));
    }
}
