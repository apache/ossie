package org.osi.converter.spark.model;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Java representation of an OSI semantic model parsed from YAML.
 */
public class OsiModel {

    private String version;
    private List<SemanticModel> semanticModels = new ArrayList<>();

    public String getVersion() {
        return version;
    }

    public void setVersion(String version) {
        this.version = version;
    }

    public List<SemanticModel> getSemanticModels() {
        return semanticModels;
    }

    public void setSemanticModels(List<SemanticModel> semanticModels) {
        this.semanticModels = semanticModels;
    }

    // -----------------------------------------------------------------------
    // Nested model classes
    // -----------------------------------------------------------------------

    public static class SemanticModel {
        private String name;
        private String description;
        private List<Dataset> datasets = new ArrayList<>();
        private List<Relationship> relationships = new ArrayList<>();
        private List<Metric> metrics = new ArrayList<>();

        public String getName() { return name; }
        public void setName(String name) { this.name = name; }

        public String getDescription() { return description; }
        public void setDescription(String description) { this.description = description; }

        public List<Dataset> getDatasets() { return datasets; }
        public void setDatasets(List<Dataset> datasets) { this.datasets = datasets; }

        public List<Relationship> getRelationships() { return relationships; }
        public void setRelationships(List<Relationship> relationships) { this.relationships = relationships; }

        public List<Metric> getMetrics() { return metrics; }
        public void setMetrics(List<Metric> metrics) { this.metrics = metrics; }
    }

    public static class Dataset {
        private String name;
        private String source;
        private List<String> primaryKey = new ArrayList<>();
        private String description;
        private List<Field> fields = new ArrayList<>();

        public String getName() { return name; }
        public void setName(String name) { this.name = name; }

        public String getSource() { return source; }
        public void setSource(String source) { this.source = source; }

        public List<String> getPrimaryKey() { return primaryKey; }
        public void setPrimaryKey(List<String> primaryKey) { this.primaryKey = primaryKey; }

        public String getDescription() { return description; }
        public void setDescription(String description) { this.description = description; }

        public List<Field> getFields() { return fields; }
        public void setFields(List<Field> fields) { this.fields = fields; }
    }

    public static class Field {
        private String name;
        private String description;
        private List<DialectExpression> expressions = new ArrayList<>();
        private boolean isTime;

        public String getName() { return name; }
        public void setName(String name) { this.name = name; }

        public String getDescription() { return description; }
        public void setDescription(String description) { this.description = description; }

        public List<DialectExpression> getExpressions() { return expressions; }
        public void setExpressions(List<DialectExpression> expressions) { this.expressions = expressions; }

        public boolean isTime() { return isTime; }
        public void setTime(boolean time) { isTime = time; }
    }

    public static class DialectExpression {
        private String dialect;
        private String expression;

        public DialectExpression() {}

        public DialectExpression(String dialect, String expression) {
            this.dialect = dialect;
            this.expression = expression;
        }

        public String getDialect() { return dialect; }
        public void setDialect(String dialect) { this.dialect = dialect; }

        public String getExpression() { return expression; }
        public void setExpression(String expression) { this.expression = expression; }
    }

    public static class Relationship {
        private String name;
        private String from;
        private String to;
        private List<String> fromColumns = new ArrayList<>();
        private List<String> toColumns = new ArrayList<>();

        public String getName() { return name; }
        public void setName(String name) { this.name = name; }

        public String getFrom() { return from; }
        public void setFrom(String from) { this.from = from; }

        public String getTo() { return to; }
        public void setTo(String to) { this.to = to; }

        public List<String> getFromColumns() { return fromColumns; }
        public void setFromColumns(List<String> fromColumns) { this.fromColumns = fromColumns; }

        public List<String> getToColumns() { return toColumns; }
        public void setToColumns(List<String> toColumns) { this.toColumns = toColumns; }
    }

    public static class Metric {
        private String name;
        private String description;
        private List<DialectExpression> expressions = new ArrayList<>();

        public String getName() { return name; }
        public void setName(String name) { this.name = name; }

        public String getDescription() { return description; }
        public void setDescription(String description) { this.description = description; }

        public List<DialectExpression> getExpressions() { return expressions; }
        public void setExpressions(List<DialectExpression> expressions) { this.expressions = expressions; }
    }
}
