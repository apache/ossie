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
import org.apache.ossie.converter.polaris.model.OsiModel.*;

import java.util.List;

/**
 * Generates Ossie YAML from an {@link OsiModel}.
 * <p>
 * Produces well-formatted YAML that conforms to the Ossie specification.
 */
public class OsiYamlGenerator {

    /**
     * Generate Ossie YAML string from a model.
     */
    public String generate(OsiModel model) {
        StringBuilder sb = new StringBuilder();
        sb.append("version: \"").append(model.getVersion()).append("\"\n\n");
        sb.append("semantic_model:\n");

        for (SemanticModel sm : model.getSemanticModels()) {
            generateSemanticModel(sb, sm);
        }

        return sb.toString();
    }

    private void generateSemanticModel(StringBuilder sb, SemanticModel sm) {
        sb.append("  - name: ").append(sm.getName()).append("\n");
        if (sm.getDescription() != null) {
            sb.append("    description: \"").append(escapeYaml(sm.getDescription())).append("\"\n");
        }

        // Datasets
        if (!sm.getDatasets().isEmpty()) {
            sb.append("    datasets:\n");
            for (Dataset ds : sm.getDatasets()) {
                generateDataset(sb, ds);
            }
        }

        // Relationships
        if (!sm.getRelationships().isEmpty()) {
            sb.append("    relationships:\n");
            for (Relationship rel : sm.getRelationships()) {
                generateRelationship(sb, rel);
            }
        }

        // Metrics
        if (!sm.getMetrics().isEmpty()) {
            sb.append("    metrics:\n");
            for (Metric metric : sm.getMetrics()) {
                generateMetric(sb, metric);
            }
        }
    }

    private void generateDataset(StringBuilder sb, Dataset ds) {
        sb.append("      - name: ").append(ds.getName()).append("\n");
        sb.append("        source: ").append(ds.getSource()).append("\n");

        if (!ds.getPrimaryKey().isEmpty()) {
            sb.append("        primary_key: [").append(String.join(", ", ds.getPrimaryKey())).append("]\n");
        }

        if (!ds.getUniqueKeys().isEmpty()) {
            sb.append("        unique_keys:\n");
            for (List<String> uk : ds.getUniqueKeys()) {
                sb.append("          - [").append(String.join(", ", uk)).append("]\n");
            }
        }

        if (ds.getDescription() != null) {
            sb.append("        description: \"").append(escapeYaml(ds.getDescription())).append("\"\n");
        }

        if (!ds.getFields().isEmpty()) {
            sb.append("        fields:\n");
            for (Field field : ds.getFields()) {
                generateField(sb, field);
            }
        }

        if (!ds.getCustomExtensions().isEmpty()) {
            generateCustomExtensions(sb, ds.getCustomExtensions(), "        ");
        }
    }

    private void generateField(StringBuilder sb, Field field) {
        sb.append("          - name: ").append(field.getName()).append("\n");

        if (field.getDatatype() != null) {
            sb.append("            datatype: ").append(field.getDatatype()).append("\n");
        }

        if (!field.getExpressions().isEmpty()) {
            sb.append("            expression:\n");
            sb.append("              dialects:\n");
            for (DialectExpression de : field.getExpressions()) {
                sb.append("                - dialect: ").append(de.getDialect()).append("\n");
                sb.append("                  expression: ");
                String expr = de.getExpression();
                if (needsQuoting(expr)) {
                    sb.append("\"").append(escapeYaml(expr)).append("\"");
                } else {
                    sb.append(expr);
                }
                sb.append("\n");
            }
        }

        if (field.isTime()) {
            sb.append("            dimension:\n");
            sb.append("              is_time: true\n");
        }

        if (field.getDescription() != null) {
            sb.append("            description: \"").append(escapeYaml(field.getDescription())).append("\"\n");
        }

        if (!field.getCustomExtensions().isEmpty()) {
            generateCustomExtensions(sb, field.getCustomExtensions(), "            ");
        }
    }

    private void generateCustomExtensions(
            StringBuilder sb, List<CustomExtension> extensions, String indent) {
        sb.append(indent).append("custom_extensions:\n");
        for (CustomExtension ext : extensions) {
            sb.append(indent).append("  - vendor_name: ").append(ext.getVendorName()).append("\n");
            sb.append(indent).append("    data: '")
                    .append(ext.getData().replace("'", "''")).append("'\n");
        }
    }

    private void generateRelationship(StringBuilder sb, Relationship rel) {
        sb.append("      - name: ").append(rel.getName()).append("\n");
        sb.append("        from: ").append(rel.getFrom()).append("\n");
        sb.append("        to: ").append(rel.getTo()).append("\n");
        sb.append("        from_columns: [").append(String.join(", ", rel.getFromColumns())).append("]\n");
        sb.append("        to_columns: [").append(String.join(", ", rel.getToColumns())).append("]\n");
    }

    private void generateMetric(StringBuilder sb, Metric metric) {
        sb.append("      - name: ").append(metric.getName()).append("\n");

        if (!metric.getExpressions().isEmpty()) {
            sb.append("        expression:\n");
            sb.append("          dialects:\n");
            for (DialectExpression de : metric.getExpressions()) {
                sb.append("            - dialect: ").append(de.getDialect()).append("\n");
                sb.append("              expression: ");
                String expr = de.getExpression();
                if (needsQuoting(expr)) {
                    sb.append("\"").append(escapeYaml(expr)).append("\"");
                } else {
                    sb.append(expr);
                }
                sb.append("\n");
            }
        }

        if (metric.getDescription() != null) {
            sb.append("        description: \"").append(escapeYaml(metric.getDescription())).append("\"\n");
        }
    }

    private boolean needsQuoting(String value) {
        if (value == null) return false;
        return value.contains("'") || value.contains("\"") || value.contains(":")
                || value.contains("{") || value.contains("}") || value.contains("[")
                || value.contains("]") || value.contains(",") || value.contains("&")
                || value.contains("*") || value.contains("#") || value.contains("?")
                || value.contains("|") || value.contains("-") || value.contains("<")
                || value.contains(">") || value.contains("=") || value.contains("!")
                || value.contains("%") || value.contains("@") || value.contains("`")
                || value.contains(" ");
    }

    private String escapeYaml(String value) {
        if (value == null) return "";
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
