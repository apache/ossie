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

import org.apache.ossie.converter.polaris.model.OssieModel;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * CLI entry point for the Ossie Polaris converter.
 * <p>
 * Supports two modes:
 * <ul>
 *   <li><b>import</b>: Reads from a Polaris catalog and generates one Ossie YAML file per nonempty namespace</li>
 *   <li><b>export</b>: Reads an Ossie YAML file and creates tables in a Polaris catalog</li>
 * </ul>
 *
 * <pre>
 * Usage:
 *   ossie-polaris-converter import --url URL --catalog CATALOG [options] [-o output.yaml | --output-dir DIR]
 *   ossie-polaris-converter export --url URL --catalog CATALOG [--client-id ID --client-secret SECRET] &lt;ossie_model.yaml&gt;
 * </pre>
 */
public class OssiePolarisConverter {

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
        String outputDirectory = null;
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
                case "--output-dir":
                    if (i + 1 >= args.length || args[i + 1].startsWith("-")) {
                        throw new IllegalArgumentException("--output-dir requires a directory");
                    }
                    outputDirectory = args[++i];
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

        if (outputDirectory != null && (!"import".equals(mode) || outputFile != null)) {
            throw new IllegalArgumentException("--output-dir is only for import and cannot be combined with -o");
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
                doImport(client, outputFile, outputDirectory);
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

    static void doImport(PolarisClient client, String outputFile, String outputDirectory) throws Exception {
        if (outputFile != null && outputDirectory != null) {
            throw new IllegalArgumentException("Use either -o or --output-dir");
        }
        PolarisImporter importer = new PolarisImporter(client);
        List<OssieModel> models = importer.importCatalog();

        if (models.isEmpty()) {
            System.err.println("Warning: no tables found in catalog.");
            return;
        }

        OssieYamlGenerator generator = new OssieYamlGenerator();
        if (outputDirectory != null) {
            Path directory = Paths.get(outputDirectory);
            Files.createDirectories(directory);
            List<Path> outputs = new ArrayList<>();
            for (int i = 0; i < models.size(); i++) {
                // The ordinal avoids collisions between sanitized or flattened namespace names.
                String name = models.get(i).getSemanticModel().getName().replaceAll("[^A-Za-z0-9_-]", "_");
                name = name.substring(0, Math.min(name.length(), 80));
                Path output = directory.resolve(String.format(Locale.ROOT, "%04d-%s.yaml", i + 1, name));
                if (Files.exists(output, LinkOption.NOFOLLOW_LINKS)) {
                    throw new IOException("Refusing to overwrite existing file: " + output);
                }
                outputs.add(output);
            }
            for (int i = 0; i < models.size(); i++) {
                Files.writeString(outputs.get(i), generator.generate(models.get(i)),
                        StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
                System.out.println("Ossie model written to " + outputs.get(i));
            }
            return;
        }
        if (models.size() != 1) {
            throw new IllegalArgumentException("Catalog contains multiple nonempty namespaces; use --output-dir");
        }
        String yaml = generator.generate(models.get(0));

        if (outputFile != null) {
            Files.writeString(Paths.get(outputFile), yaml, StandardCharsets.UTF_8);
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

        OssieModelParser parser = new OssieModelParser();
        OssieModel model = parser.parse(Paths.get(inputFile));

        PolarisExporter exporter = new PolarisExporter(client);
        exporter.exportModel(model);

        System.out.println("Exported one semantic model to Polaris catalog.");
    }

    private static void printUsage() {
        System.err.println("Usage:");
        System.err.println("  ossie-polaris-converter import --url URL --catalog CATALOG [options] [-o output.yaml | --output-dir DIR]");
        System.err.println("  ossie-polaris-converter export --url URL --catalog CATALOG [options] <ossie_model.yaml>");
        System.err.println();
        System.err.println("Options:");
        System.err.println("  --url URL              Polaris server URL (e.g., http://localhost:8181)");
        System.err.println("  --catalog CATALOG      Catalog name");
        System.err.println("  --client-id ID         OAuth2 client ID for authentication");
        System.err.println("  --client-secret SECRET OAuth2 client secret for authentication");
        System.err.println("  --token TOKEN          Pre-existing bearer token");
        System.err.println("  -o FILE                Output file for a single nonempty namespace (default: stdout)");
        System.err.println("  --output-dir DIR       One YAML file per nonempty namespace (import mode)");
    }
}
