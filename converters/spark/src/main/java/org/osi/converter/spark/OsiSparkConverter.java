package org.osi.converter.spark;

import org.osi.converter.spark.model.OsiModel;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * CLI entry point: reads an OSI YAML semantic model and generates PySpark code.
 *
 * <pre>
 * Usage:
 *   java -jar osi-spark-converter.jar &lt;osi_model.yaml&gt; [-o output.py] [-d DIALECT]
 * </pre>
 */
public class OsiSparkConverter {

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("Usage: osi-spark-converter <osi_model.yaml> [-o output.py] [-d DIALECT]");
            System.err.println();
            System.err.println("Options:");
            System.err.println("  -o FILE     Write generated PySpark code to FILE (default: stdout)");
            System.err.println("  -d DIALECT  Preferred SQL dialect: ANSI_SQL, SNOWFLAKE, DATABRICKS (default: ANSI_SQL)");
            System.exit(1);
        }

        String inputFile = args[0];
        String outputFile = null;
        String dialect = "ANSI_SQL";

        for (int i = 1; i < args.length; i++) {
            switch (args[i]) {
                case "-o":
                    if (i + 1 < args.length) {
                        outputFile = args[++i];
                    }
                    break;
                case "-d":
                    if (i + 1 < args.length) {
                        dialect = args[++i];
                    }
                    break;
                default:
                    break;
            }
        }

        // Parse the OSI model
        OsiModelParser parser = new OsiModelParser();
        OsiModel model = parser.parse(Paths.get(inputFile));

        if (model.getSemanticModels().isEmpty()) {
            System.err.println("Error: no semantic_model found in " + inputFile);
            System.exit(1);
        }

        // Generate PySpark code
        SparkCodeGenerator generator = new SparkCodeGenerator(dialect);
        String code = generator.generate(model);

        if (outputFile != null) {
            Files.write(Paths.get(outputFile), code.getBytes(StandardCharsets.UTF_8));
            System.out.println("Generated PySpark code written to " + outputFile);
        } else {
            System.out.println(code);
        }
    }
}
