package org.osi.converter.spark;

import org.osi.converter.spark.model.OsiModel;
import org.osi.converter.spark.model.OsiModel.*;

import java.util.List;

/**
 * Generates PySpark code from a parsed {@link OsiModel}.
 * <p>
 * The generated code creates DataFrames, registers temp views,
 * builds join helpers from relationships, and exposes metric functions.
 */
public class SparkCodeGenerator {

    private final String dialect;

    public SparkCodeGenerator() {
        this("ANSI_SQL");
    }

    public SparkCodeGenerator(String dialect) {
        this.dialect = dialect;
    }

    /**
     * Generate the full PySpark module for the given OSI model.
     */
    public String generate(OsiModel model) {
        StringBuilder sb = new StringBuilder();

        for (SemanticModel sm : model.getSemanticModels()) {
            generateHeader(sb, sm);
            generateDatasetLoaders(sb, sm);
            generateLoadAll(sb, sm);
            generateJoinHelpers(sb, sm);
            generateMetrics(sb, sm);
            generateMain(sb, sm);
        }

        return sb.toString();
    }

    // -----------------------------------------------------------------------
    // Header
    // -----------------------------------------------------------------------

    private void generateHeader(StringBuilder sb, SemanticModel sm) {
        sb.append("\"\"\"\n");
        sb.append("Auto-generated PySpark code from OSI semantic model: ").append(sm.getName()).append("\n");
        if (sm.getDescription() != null) {
            sb.append("\n").append(sm.getDescription()).append("\n");
        }
        sb.append("\"\"\"\n\n");
        sb.append("from pyspark.sql import SparkSession, DataFrame\n");
        sb.append("from pyspark.sql import functions as F\n\n\n");

        sb.append("def get_spark() -> SparkSession:\n");
        sb.append("    \"\"\"Return or create a SparkSession.\"\"\"\n");
        sb.append("    return (\n");
        sb.append("        SparkSession.builder\n");
        sb.append("        .appName(\"").append(sm.getName()).append("\")\n");
        sb.append("        .getOrCreate()\n");
        sb.append("    )\n\n");
    }

    // -----------------------------------------------------------------------
    // Dataset loaders
    // -----------------------------------------------------------------------

    private void generateDatasetLoaders(StringBuilder sb, SemanticModel sm) {
        for (Dataset ds : sm.getDatasets()) {
            generateDatasetLoader(sb, ds);
        }
    }

    private void generateDatasetLoader(StringBuilder sb, Dataset ds) {
        String funcName = "load_" + ds.getName();
        sb.append("\ndef ").append(funcName).append("(spark: SparkSession) -> DataFrame:\n");
        sb.append("    \"\"\"\n");
        sb.append("    Load dataset: ").append(ds.getName()).append("\n");
        if (ds.getDescription() != null) {
            sb.append("    ").append(ds.getDescription()).append("\n");
        }
        sb.append("    Source: ").append(ds.getSource()).append("\n");
        sb.append("    \"\"\"\n");

        sb.append("    df = spark.table(\"").append(ds.getSource()).append("\")\n");

        // Add computed columns for fields whose expression differs from their name
        for (Field field : ds.getFields()) {
            String expr = pickExpression(field.getExpressions());
            if (expr != null && !expr.equals(field.getName())) {
                sb.append("    df = df.withColumn(\"").append(field.getName())
                        .append("\", F.expr(\"").append(escapeString(expr)).append("\"))\n");
            }
        }

        sb.append("    df.createOrReplaceTempView(\"").append(ds.getName()).append("\")\n");
        sb.append("    return df\n\n");
    }

    // -----------------------------------------------------------------------
    // Load all
    // -----------------------------------------------------------------------

    private void generateLoadAll(StringBuilder sb, SemanticModel sm) {
        sb.append("\ndef load_all_datasets(spark: SparkSession) -> dict[str, DataFrame]:\n");
        sb.append("    \"\"\"Load all datasets and register temp views. Returns a dict of name -> DataFrame.\"\"\"\n");
        sb.append("    datasets = {}\n");
        for (Dataset ds : sm.getDatasets()) {
            sb.append("    datasets[\"").append(ds.getName()).append("\"] = load_")
                    .append(ds.getName()).append("(spark)\n");
        }
        sb.append("    return datasets\n\n");
    }

    // -----------------------------------------------------------------------
    // Join helpers
    // -----------------------------------------------------------------------

    private void generateJoinHelpers(StringBuilder sb, SemanticModel sm) {
        for (Relationship rel : sm.getRelationships()) {
            generateJoinHelper(sb, rel);
        }
    }

    private void generateJoinHelper(StringBuilder sb, Relationship rel) {
        String fromDs = rel.getFrom();
        String toDs = rel.getTo();
        String funcName = "join_" + rel.getName();

        sb.append("\ndef ").append(funcName).append("(")
                .append(fromDs).append("_df: DataFrame, ")
                .append(toDs).append("_df: DataFrame) -> DataFrame:\n");
        sb.append("    \"\"\"\n");
        sb.append("    Join ").append(fromDs).append(" -> ").append(toDs).append("\n");
        sb.append("    \"\"\"\n");

        List<String> fromCols = rel.getFromColumns();
        List<String> toCols = rel.getToColumns();

        if (fromCols.size() == 1) {
            sb.append("    return ").append(fromDs).append("_df.join(")
                    .append(toDs).append("_df, ")
                    .append(fromDs).append("_df[\"").append(fromCols.get(0)).append("\"] == ")
                    .append(toDs).append("_df[\"").append(toCols.get(0)).append("\"], \"inner\")\n\n");
        } else {
            String condition = buildCompositeCondition(fromDs, toDs, fromCols, toCols);
            sb.append("    condition = ").append(condition).append("\n");
            sb.append("    return ").append(fromDs).append("_df.join(")
                    .append(toDs).append("_df, condition, \"inner\")\n\n");
        }
    }

    private String buildCompositeCondition(String fromDs, String toDs,
                                            List<String> fromCols, List<String> toCols) {
        StringBuilder condition = new StringBuilder();
        for (int i = 0; i < fromCols.size(); i++) {
            if (i > 0) {
                condition.append(" & ");
            }
            condition.append(fromDs).append("_df[\"").append(fromCols.get(i)).append("\"] == ")
                    .append(toDs).append("_df[\"").append(toCols.get(i)).append("\"]");
        }
        return condition.toString();
    }

    // -----------------------------------------------------------------------
    // Metrics
    // -----------------------------------------------------------------------

    private void generateMetrics(StringBuilder sb, SemanticModel sm) {
        for (Metric metric : sm.getMetrics()) {
            generateMetric(sb, metric);
        }
    }

    private void generateMetric(StringBuilder sb, Metric metric) {
        String expr = pickExpression(metric.getExpressions());
        if (expr == null) {
            return;
        }

        String funcName = "compute_" + metric.getName();
        sb.append("\ndef ").append(funcName).append("(spark: SparkSession) -> DataFrame:\n");
        sb.append("    \"\"\"\n");
        sb.append("    Metric: ").append(metric.getName()).append("\n");
        if (metric.getDescription() != null) {
            sb.append("    ").append(metric.getDescription()).append("\n");
        }
        sb.append("    Expression: ").append(expr).append("\n");
        sb.append("    \"\"\"\n");
        sb.append("    return spark.sql(\"SELECT ").append(escapeString(expr))
                .append(" AS ").append(metric.getName()).append("\")\n\n");
    }

    // -----------------------------------------------------------------------
    // Main block
    // -----------------------------------------------------------------------

    private void generateMain(StringBuilder sb, SemanticModel sm) {
        sb.append("\nif __name__ == \"__main__\":\n");
        sb.append("    spark = get_spark()\n");
        sb.append("    print(f\"Spark session started: {spark.sparkContext.appName}\")\n\n");

        sb.append("    # Load all datasets\n");
        sb.append("    dfs = load_all_datasets(spark)\n");
        sb.append("    for name, df in dfs.items():\n");
        sb.append("        print(f\"Dataset {name}: {df.count()} rows\")\n\n");

        if (!sm.getRelationships().isEmpty()) {
            Relationship rel = sm.getRelationships().get(0);
            sb.append("    # Example join: ").append(rel.getName()).append("\n");
            sb.append("    joined = join_").append(rel.getName())
                    .append("(dfs[\"").append(rel.getFrom())
                    .append("\"], dfs[\"").append(rel.getTo()).append("\"])\n");
            sb.append("    joined.show(5)\n\n");
        }

        sb.append("    spark.stop()\n");
    }

    // -----------------------------------------------------------------------
    // Utilities
    // -----------------------------------------------------------------------

    /**
     * Pick the expression for the preferred dialect, falling back to the first available.
     */
    private String pickExpression(List<DialectExpression> expressions) {
        if (expressions == null || expressions.isEmpty()) {
            return null;
        }
        for (DialectExpression de : expressions) {
            if (dialect.equals(de.getDialect())) {
                return de.getExpression();
            }
        }
        return expressions.get(0).getExpression();
    }

    private String escapeString(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
