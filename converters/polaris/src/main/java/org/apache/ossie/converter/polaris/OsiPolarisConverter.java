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

import org.apache.ossie.converter.polaris.model.OsiModel;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * CLI entry point for the Ossie Polaris converter.
 * <p>
 * Supports two modes:
 * <ul>
 *   <li><b>import</b>: Reads from a Polaris catalog and generates an Ossie YAML file</li>
 *   <li><b>export</b>: Reads an Ossie YAML file and creates tables in a Polaris catalog</li>
 * </ul>
 *
 * <pre>
 * Usage:
 *   ossie-polaris-converter import --url URL --catalog CATALOG [--client-id ID --client-secret SECRET] [-o output.yaml]
 *   ossie-polaris-converter export --url URL --catalog CATALOG [--client-id ID --client-secret SECRET] &lt;osi_model.yaml&gt;
 * </pre>
 */
public class OsiPolarisConverter {

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            printUsage();
            System.exit(1);
        }

        String mode = args[0];
        String url = null;
        String catalog = null;
        String clientId = null;
        String clientSecret = null;
        String token = null;
        String outputFile = null;
        String inputFile = null;

        for (int i = 1; i < args.length; i++) {
            switch (args[i]) {
                case "--url":
                    if (i + 1 < args.length) url = args[++i];
                    break;
                case "--catalog":
                    if (i + 1 < args.length) catalog = args[++i];
                    break;
                case "--client-id":
                    if (i + 1 < args.length) clientId = args[++i];
                    break;
                case "--client-secret":
                    if (i + 1 < args.length) clientSecret = args[++i];
                    break;
                case "--token":
                    if (i + 1 < args.length) token = args[++i];
                    break;
                case "-o":
                    if (i + 1 < args.length) outputFile = args[++i];
                    break;
                default:
                    if (!args[i].startsWith("-")) {
                        inputFile = args[i];
                    }
                    break;
            }
        }

        if (url == null || catalog == null) {
            System.err.println("Error: --url and --catalog are required.");
            printUsage();
            System.exit(1);
        }

        PolarisClient client = new PolarisClient(url, catalog);

        // Authenticate
        if (clientId != null && clientSecret != null) {
            client.authenticate(clientId, clientSecret);
        } else if (token != null) {
            client.setToken(token);
        }

        switch (mode) {
            case "import":
                doImport(client, outputFile);
                break;
            case "export":
                doExport(client, inputFile);
                break;
            default:
                System.err.println("Error: unknown mode '" + mode + "'. Use 'import' or 'export'.");
                printUsage();
                System.exit(1);
        }
    }

    private static void doImport(PolarisClient client, String outputFile) throws Exception {
        PolarisImporter importer = new PolarisImporter(client);
        OsiModel model = importer.importCatalog();

        if (model.getSemanticModels().isEmpty()) {
            System.err.println("Warning: no tables found in catalog.");
        }

        OsiYamlGenerator generator = new OsiYamlGenerator();
        String yaml = generator.generate(model);

        if (outputFile != null) {
            Files.write(Paths.get(outputFile), yaml.getBytes(StandardCharsets.UTF_8));
            System.out.println("Ossie model written to " + outputFile);
        } else {
            System.out.println(yaml);
        }
    }

    private static void doExport(PolarisClient client, String inputFile) throws Exception {
        if (inputFile == null) {
            System.err.println("Error: Ossie YAML file is required for export mode.");
            System.exit(1);
        }

        OsiModelParser parser = new OsiModelParser();
        OsiModel model = parser.parse(Paths.get(inputFile));

        if (model.getSemanticModels().isEmpty()) {
            System.err.println("Error: no semantic_model found in " + inputFile);
            System.exit(1);
        }

        PolarisExporter exporter = new PolarisExporter(client);
        exporter.exportModel(model);

        System.out.println("Exported " + model.getSemanticModels().size()
                + " semantic model(s) to Polaris catalog.");
    }

    private static void printUsage() {
        System.err.println("Usage:");
        System.err.println("  ossie-polaris-converter import --url URL --catalog CATALOG [options] [-o output.yaml]");
        System.err.println("  ossie-polaris-converter export --url URL --catalog CATALOG [options] <osi_model.yaml>");
        System.err.println();
        System.err.println("Options:");
        System.err.println("  --url URL              Polaris server URL (e.g., http://localhost:8181)");
        System.err.println("  --catalog CATALOG      Catalog name");
        System.err.println("  --client-id ID         OAuth2 client ID for authentication");
        System.err.println("  --client-secret SECRET OAuth2 client secret for authentication");
        System.err.println("  --token TOKEN          Pre-existing bearer token");
        System.err.println("  -o FILE                Output file (import mode, default: stdout)");
    }
}
