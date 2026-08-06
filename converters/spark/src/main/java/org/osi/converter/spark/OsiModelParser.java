package org.osi.converter.spark;

import org.osi.converter.spark.model.OsiModel;
import org.osi.converter.spark.model.OsiModel.*;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;


/**
 * Parses an OSI YAML file into an {@link OsiModel}.
 */
public class OsiModelParser {

    /**
     * Parse an OSI YAML file from the given path.
     */
    public OsiModel parse(Path yamlPath) throws IOException {
        try (InputStream is = Files.newInputStream(yamlPath)) {
            return parse(is);
        }
    }

    /**
     * Parse an OSI YAML file from an input stream.
     */
    @SuppressWarnings("unchecked")
    public OsiModel parse(InputStream is) {
        Yaml yaml = new Yaml();
        Map<String, Object> root = yaml.load(is);

        OsiModel model = new OsiModel();
        model.setVersion((String) root.get("version"));

        List<Map<String, Object>> smList = (List<Map<String, Object>>) root.get("semantic_model");
        if (smList == null) {
            return model;
        }

        List<SemanticModel> semanticModels = new ArrayList<>();
        for (Map<String, Object> smMap : smList) {
            semanticModels.add(parseSemanticModel(smMap));
        }
        model.setSemanticModels(semanticModels);
        return model;
    }

    @SuppressWarnings("unchecked")
    private SemanticModel parseSemanticModel(Map<String, Object> map) {
        SemanticModel sm = new SemanticModel();
        sm.setName((String) map.get("name"));
        sm.setDescription((String) map.get("description"));

        // Datasets
        List<Map<String, Object>> dsList = (List<Map<String, Object>>) map.get("datasets");
        if (dsList != null) {
            List<Dataset> datasets = new ArrayList<>();
            for (Map<String, Object> dsMap : dsList) {
                datasets.add(parseDataset(dsMap));
            }
            sm.setDatasets(datasets);
        }

        // Relationships
        List<Map<String, Object>> relList = (List<Map<String, Object>>) map.get("relationships");
        if (relList != null) {
            List<Relationship> relationships = new ArrayList<>();
            for (Map<String, Object> relMap : relList) {
                relationships.add(parseRelationship(relMap));
            }
            sm.setRelationships(relationships);
        }

        // Metrics
        List<Map<String, Object>> metricList = (List<Map<String, Object>>) map.get("metrics");
        if (metricList != null) {
            List<Metric> metrics = new ArrayList<>();
            for (Map<String, Object> mMap : metricList) {
                metrics.add(parseMetric(mMap));
            }
            sm.setMetrics(metrics);
        }

        return sm;
    }

    @SuppressWarnings("unchecked")
    private Dataset parseDataset(Map<String, Object> map) {
        Dataset ds = new Dataset();
        ds.setName((String) map.get("name"));
        ds.setSource((String) map.get("source"));
        ds.setDescription((String) map.get("description"));

        List<String> pk = (List<String>) map.get("primary_key");
        if (pk != null) {
            ds.setPrimaryKey(new ArrayList<>(pk));
        }

        List<Map<String, Object>> fieldList = (List<Map<String, Object>>) map.get("fields");
        if (fieldList != null) {
            List<Field> fields = new ArrayList<>();
            for (Map<String, Object> fMap : fieldList) {
                fields.add(parseField(fMap));
            }
            ds.setFields(fields);
        }
        return ds;
    }

    @SuppressWarnings("unchecked")
    private Field parseField(Map<String, Object> map) {
        Field field = new Field();
        field.setName((String) map.get("name"));
        field.setDescription((String) map.get("description"));

        // Dimension
        Map<String, Object> dim = (Map<String, Object>) map.get("dimension");
        if (dim != null) {
            Object isTime = dim.get("is_time");
            field.setTime(Boolean.TRUE.equals(isTime));
        }

        // Expressions
        field.setExpressions(parseDialectExpressions(map));
        return field;
    }

    @SuppressWarnings("unchecked")
    private Relationship parseRelationship(Map<String, Object> map) {
        Relationship rel = new Relationship();
        rel.setName((String) map.get("name"));
        rel.setFrom((String) map.get("from"));
        rel.setTo((String) map.get("to"));

        List<String> fromCols = (List<String>) map.get("from_columns");
        if (fromCols != null) {
            rel.setFromColumns(new ArrayList<>(fromCols));
        }
        List<String> toCols = (List<String>) map.get("to_columns");
        if (toCols != null) {
            rel.setToColumns(new ArrayList<>(toCols));
        }
        return rel;
    }

    @SuppressWarnings("unchecked")
    private Metric parseMetric(Map<String, Object> map) {
        Metric metric = new Metric();
        metric.setName((String) map.get("name"));
        metric.setDescription((String) map.get("description"));
        metric.setExpressions(parseDialectExpressions(map));
        return metric;
    }

    @SuppressWarnings("unchecked")
    private List<DialectExpression> parseDialectExpressions(Map<String, Object> map) {
        List<DialectExpression> result = new ArrayList<>();
        Map<String, Object> exprBlock = (Map<String, Object>) map.get("expression");
        if (exprBlock == null) {
            return result;
        }
        List<Map<String, Object>> dialects = (List<Map<String, Object>>) exprBlock.get("dialects");
        if (dialects == null) {
            return result;
        }
        for (Map<String, Object> d : dialects) {
            String dialect = (String) d.get("dialect");
            Object exprValue = d.get("expression");
            String expression = exprValue != null ? exprValue.toString() : null;
            result.add(new DialectExpression(dialect, expression));
        }
        return result;
    }
}
