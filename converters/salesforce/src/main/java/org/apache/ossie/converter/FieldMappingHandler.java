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

package org.apache.ossie.converter;

import org.apache.ossie.converter.pipeline.PipelineStep;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.util.regex.Pattern;

import static org.apache.ossie.converter.ConverterConstants.*;
import static org.apache.ossie.util.DataStructureUtils.*;

/**
 * Bidirectional handler for mapping fields between Ossie and Salesforce formats.
 *
 * <p>Ossie → SF: Maps dataset fields to SemanticDimensions and SemanticMeasurements
 * <p>SF → Ossie: Maps SemanticDimensions and SemanticMeasurements to dataset fields
 *
 */
public class FieldMappingHandler implements PipelineStep {

    private static final Logger logger = LoggerFactory.getLogger(FieldMappingHandler.class);

    // Properties handled when converting SF dimensions/measurements to Ossie fields
    private static final Set<String> SF_FIELD_HANDLED_PROPS =
        Set.of(API_NAME, LABEL, DESCRIPTION, DATA_OBJECT_FIELD_NAME);

    // Compiled regex pattern for SQL keywords that indicate calculated expressions
    private static final Pattern CALCULATED_KEYWORDS_PATTERN = Pattern.compile(
        "\\b(CASE|WHEN|THEN|ELSE|END|CAST|CONVERT|EXTRACT|SUBSTRING|SUBSTR|" +
        "COALESCE|NULLIF|IFNULL|CONCAT|UPPER|LOWER|TRIM|LENGTH|" +
        "AND|OR|NOT|IN|BETWEEN|LIKE|IS\\s+NULL|IS\\s+NOT\\s+NULL|DISTINCT|" +
        "COUNT|SUM|AVG|MIN|MAX|DATE|YEAR|MONTH|DAY)\\b"
    );

    private final ConversionDirection direction;
    private final CustomExtensionHandler customExtensionHandler;

    /**
     * Enum representing the four possible field types in Salesforce Semantic Model.
     */
    private enum FieldType {
        DIMENSION, // Direct dimension: !isCalculated + hasDimension
        MEASUREMENT, // Direct measurement: !isCalculated + !hasDimension
        CALCULATED_DIMENSION, // Calculated dimension: isCalculated + hasDimension
        CALCULATED_MEASUREMENT // Calculated measurement: isCalculated + !hasDimension
    }

    public FieldMappingHandler(ConversionDirection direction, CustomExtensionHandler customExtensionHandler) {
        this.direction = direction;
        this.customExtensionHandler = customExtensionHandler;
    }

    /**
     * Executes field mapping based on conversion direction.
     */
    @Override
    public void execute(Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {
        logger.debug("Mapping fields in {} direction", direction);
        if (direction == ConversionDirection.OSI_TO_SALESFORCE) {
            mapOsiToSalesforce(sourceData, outputData);
        } else {
            mapSalesforceToOsi(sourceData, outputData);
        }
    }

    /**
     * Maps Ossie dataset fields to Salesforce SemanticDimensions and SemanticMeasurements.
     *
     * @param outputData The output map containing semanticModel
     * @param sourceData The source Ossie data
     */
    private void mapOsiToSalesforce(
            Map<String, Object> sourceData, Map<String, Object> outputData) {

        List<Object> osiDatasets = getList(sourceData, DATASETS);

        List<Object> sfDataObjects = getList(outputData, SEMANTIC_DATA_OBJECTS);

        for (Object osiDatasetObj : osiDatasets) {
            Map<String, Object> osiDataset = asMap(osiDatasetObj);

            String datasetName = getString(osiDataset, NAME);
            if (datasetName == null) continue;

            // Find matching SemanticDataObject
            Map<String, Object> sfDataObject = findItemById(sfDataObjects, API_NAME, datasetName);
            if (sfDataObject == null) continue;

            processFieldsForDataset(osiDataset, sfDataObject, outputData);
        }
    }

    /**
     * Maps Salesforce SemanticDimensions and SemanticMeasurements to Ossie dataset fields.
     */
    private void mapSalesforceToOsi(
            Map<String, Object> sourceData, Map<String, Object> outputData) {

        List<Object> sfDataObjects = getList(sourceData, SEMANTIC_DATA_OBJECTS);
        if (sfDataObjects == null) {
            return;
        }

        List<Object> osiDatasets = getList(outputData, DATASETS);

        for (Object sfDataObjectObj : sfDataObjects) {
            Map<String, Object> sfDataObject = asMap(sfDataObjectObj);

            String apiName = getString(sfDataObject, API_NAME);
            if (apiName == null) continue;

            // Find matching Ossie dataset by name (mapped from apiName)
            Map<String, Object> osiDataset = findItemById(osiDatasets, NAME, apiName);
            if (osiDataset == null) continue;

            // Convert SF dimensions and measurements to Ossie fields
            convertSalesforceFieldsToOsi(sfDataObject, osiDataset);
        }

        // Process model-level calculated dimensions
        processModelLevelCalculatedDimensions(sourceData, outputData);

        // Cleanup: remove processed structural key
        sourceData.remove(SEMANTIC_DATA_OBJECTS);
    }

    /**
     * Converts Salesforce dimensions and measurements to Ossie fields for a dataset.
     */
    private void convertSalesforceFieldsToOsi(
            Map<String, Object> sfDataObject, Map<String, Object> osiDataset) {
        List<Object> osiFields = getOrCreateList(osiDataset, FIELDS);

        // Process semanticDimensions → Ossie fields
        List<Object> sfDimensions = getList(sfDataObject, SEMANTIC_DIMENSIONS);
        if (sfDimensions != null) {
            for (Object sfDimObj : sfDimensions) {
                Map<String, Object> sfDim = asMap(sfDimObj);
                Map<String, Object> osiField = convertDimensionToOsiField(sfDim);
                osiFields.add(osiField);
            }
        }

        // Process semanticMeasurements → Ossie fields
        List<Object> sfMeasurements = getList(sfDataObject, SEMANTIC_MEASUREMENTS);
        if (sfMeasurements != null) {
            for (Object sfMeasObj : sfMeasurements) {
                Map<String, Object> sfMeas = asMap(sfMeasObj);
                Map<String, Object> osiField = convertMeasurementToOsiField(sfMeas);
                osiFields.add(osiField);
            }
        }
    }

    /**
     * Converts a Salesforce dimension to an Ossie field with dimension property.
     */
    private Map<String, Object> convertDimensionToOsiField(Map<String, Object> sfDimension) {
        Map<String, Object> osiField = new LinkedHashMap<>();

        mapCommonFieldProperties(sfDimension, osiField);

        // Add dimension property with is_time based on dataType
        Map<String, Object> dimensionProp = new LinkedHashMap<>();
        String dataType = getString(sfDimension, DATA_TYPE);
        if (DATA_TYPE_DATE.equals(dataType) || DATA_TYPE_DATE_TIME.equals(dataType)) {
            dimensionProp.put(IS_TIME, true);
        } else {
            dimensionProp.put(IS_TIME, false);
        }
        osiField.put(DIMENSION, dimensionProp);

        // Wrap dataObjectFieldName in expression structure
        String dataObjectFieldName = getString(sfDimension, DATA_OBJECT_FIELD_NAME);
        if (dataObjectFieldName != null) {
            osiField.put(EXPRESSION, wrapExpression(dataObjectFieldName));
        }

        // Store unmapped properties in custom_extensions
        customExtensionHandler.storeUnmappedItemProperties(osiField, sfDimension, SF_FIELD_HANDLED_PROPS);

        return osiField;
    }

    /**
     * Converts a Salesforce measurement to an Ossie field without dimension property.
     */
    private Map<String, Object> convertMeasurementToOsiField(Map<String, Object> sfMeasurement) {
        Map<String, Object> osiField = new LinkedHashMap<>();

        mapCommonFieldProperties(sfMeasurement, osiField);

        String dataObjectFieldName = getString(sfMeasurement, DATA_OBJECT_FIELD_NAME);
        if (dataObjectFieldName != null) {
            osiField.put(EXPRESSION, wrapExpression(dataObjectFieldName));
        }

        // Store unmapped properties in custom_extensions
        customExtensionHandler.storeUnmappedItemProperties(osiField, sfMeasurement, SF_FIELD_HANDLED_PROPS);

        return osiField;
    }

    /**
     * Maps common field properties from Salesforce to Ossie format.
     * Common properties: name (from apiName), label, description.
     */
    private void mapCommonFieldProperties(Map<String, Object> sfField, Map<String, Object> osiField) {
        String apiName = getString(sfField, API_NAME);
        osiField.put(NAME, apiName);

        String label = getString(sfField, LABEL);
        if (label != null) {
            osiField.put(LABEL, label);
        }

        String description = getString(sfField, DESCRIPTION);
        if (description != null) {
            osiField.put(DESCRIPTION, description);
        }
    }

    /**
     * Wraps a simple expression string in Ossie's expression.dialects structure.
     * Tags expressions with TABLEAU dialect as they come from Salesforce (Tableau CRM).
     */
    private Map<String, Object> wrapExpression(String expressionValue) {
        Map<String, Object> dialect = new LinkedHashMap<>();
        dialect.put(DIALECT, DIALECT_TABLEAU);
        dialect.put(EXPRESSION, expressionValue);

        List<Object> dialects = new ArrayList<>();
        dialects.add(dialect);

        Map<String, Object> expression = new LinkedHashMap<>();
        expression.put(DIALECTS, dialects);

        return expression;
    }

    /**
     * Processes all fields for a single dataset.
     *
     * <p><b>Routing Logic:</b>
     * <table border="1">
     *   <tr><th>Expression Type</th><th>Has dimension?</th><th>Routes To</th></tr>
     *   <tr><td>Direct</td><td>Yes</td><td>dataObject.semanticDimensions</td></tr>
     *   <tr><td>Direct</td><td>No</td><td>dataObject.semanticMeasurements</td></tr>
     *   <tr><td>Calculated</td><td>N/A</td><td>MODEL.semanticCalculatedDimensions</td></tr>
     * </table>
     *
     * @param osiDataset The Ossie dataset
     * @param sfDataObject The Salesforce data object to add direct fields to
     * @param outputData The Salesforce model for adding calculated dimensions
     */
    private void processFieldsForDataset(
            Map<String, Object> osiDataset, Map<String, Object> sfDataObject, Map<String, Object> outputData) {
        List<Object> osiFields = getList(osiDataset, FIELDS);
        if (osiFields == null) {
            return;
        }

        List<Object> sfDimensions = getList(sfDataObject, SEMANTIC_DIMENSIONS);
        List<Object> sfMeasurements = getList(sfDataObject, SEMANTIC_MEASUREMENTS);

        for (Object osiFieldObj : osiFields) {
            Map<String, Object> osiField = asMap(osiFieldObj);

            // Determine field type based on Ossie structure
            boolean hasDimension = osiField.containsKey(DIMENSION);
            ExpressionInfo expressionInfo = unwrapExpression(osiField);

            String expression = expressionInfo.expression();
            String dialect = expressionInfo.dialect();

            // Skip calculated fields for non-Tableau dialects till we agree on a common dialect.
            if (!DIALECT_TABLEAU.equals(dialect) && isCalculatedExpression(expression)) {
                continue;
            }

            // Check if this is a calculated field (Tableau dialect with calculated expression)
            boolean isCalculated = DIALECT_TABLEAU.equals(dialect) && isCalculatedExpression(expression);

            if (isCalculated) {
                // Create a semantic calculated dimension
                Map<String, Object> calcDim = createSemanticCalculatedDimension(osiField, expression);

                customExtensionHandler.restoreSalesforceCustomExtension(calcDim, osiField);

                applyFieldDefaults(calcDim);

                // Add to semantic calculated dimensions array
                List<Object> calcDimensions = getOrCreateList(outputData, SEMANTIC_CALCULATED_DIMENSIONS);
                calcDimensions.add(calcDim);
            } else {
                // Non calculated field - add to data object
                FieldType fieldType = hasDimension? FieldType.DIMENSION : FieldType.MEASUREMENT;

                Map<String, Object> sfField = mapFieldProperties(osiField, expression);

                customExtensionHandler.restoreSalesforceCustomExtension(sfField, osiField);

                applyFieldDefaults(sfField);

                RoutingResult result =
                        routeFieldToArray(sfField, fieldType, sfDataObject, sfDimensions, sfMeasurements);
                sfDimensions = result.dataObjectDimensions();
                sfMeasurements = result.dataObjectMeasurements();
            }
        }
    }


    /**
     * Maps field properties based on whether the field is calculated.
     * Includes common properties plus type-specific properties.
     *
     * @param osiField The Ossie field
     * @param expression The extracted expression string
     * @return A map with Salesforce field properties
     */
    private Map<String, Object> mapFieldProperties(
            Map<String, Object> osiField, String expression) {

        Map<String, Object> sfField = new LinkedHashMap<>();

        String name = getString(osiField, NAME);
        sfField.put(API_NAME, name);

        String description = getString(osiField, DESCRIPTION);
        if (description != null) {
            sfField.put(DESCRIPTION, description);
        }

        String label = getString(osiField, LABEL);
        if (label != null) {
            sfField.put(LABEL, label);
        }

        sfField.put(DATA_OBJECT_FIELD_NAME, expression);
        return sfField;
    }

    /**
     * Creates a Salesforce semanticCalculatedDimension from an Ossie field with a calculated expression.
     * Per schema: required properties are apiName and expression.
     *
     * @param osiField The Ossie field
     * @param expression The calculated expression
     * @return A map representing a semanticCalculatedDimension
     */
    private Map<String, Object> createSemanticCalculatedDimension(
            Map<String, Object> osiField, String expression) {

        Map<String, Object> calcDim = new LinkedHashMap<>();

        // Required properties
        String name = getString(osiField, NAME);
        calcDim.put(API_NAME, name);
        calcDim.put(EXPRESSION, expression);

        // Optional properties
        String description = getString(osiField, DESCRIPTION);
        if (description != null) {
            calcDim.put(DESCRIPTION, description);
        }

        String label = getString(osiField, LABEL);
        if (label != null) {
            calcDim.put(LABEL, label);
        }

        // Set syntax for Tableau expressions
        calcDim.put("syntax", DIALECT_TABLEAU);

        return calcDim;
    }

    /**
     * Routes a field to the appropriate array based on its type.
     * Initializes arrays lazily using computeIfAbsent.
     *
     * @param sfField The Salesforce field to route
     * @param fieldType The field type
     * @param sfDataObject The data object (for data object-level arrays)
     * @return Updated arrays for all levels
     */
    private RoutingResult routeFieldToArray(
            Map<String, Object> sfField,
            FieldType fieldType,
            Map<String, Object> sfDataObject,
            List<Object> currentDataObjectDimensions,
            List<Object> currentDataObjectMeasurements) {

        List<Object> dataObjectDimensions = currentDataObjectDimensions;
        List<Object> dataObjectMeasurements = currentDataObjectMeasurements;

        switch (fieldType) {
            case DIMENSION:
                dataObjectDimensions = getOrCreateList(sfDataObject, SEMANTIC_DIMENSIONS);
                dataObjectDimensions.add(sfField);
                break;

            case MEASUREMENT:
                dataObjectMeasurements = getOrCreateList(sfDataObject, SEMANTIC_MEASUREMENTS);
                dataObjectMeasurements.add(sfField);
                break;
        }

        return new RoutingResult(dataObjectDimensions, dataObjectMeasurements);
    }

    /**
     * Helper record to return updated data object arrays.
     */
    private record RoutingResult(List<Object> dataObjectDimensions, List<Object> dataObjectMeasurements) {}

    /**
     * Helper record to return expression value along with its dialect type.
     */
    private record ExpressionInfo(String expression, String dialect) {}

    /**
     * Extracts the expression value and dialect from Ossie field's expression.dialects[0].expression.
     * This unwraps the nested structure to get the simple column reference and its dialect.
     *
     * @param osiField The Ossie field containing expression structure
     * @return ExpressionInfo containing the expression string and dialect type, or null if not found
     */
    private ExpressionInfo unwrapExpression(Map<String, Object> osiField) {
        Object expressionObj = osiField.get(EXPRESSION);

        Map<String, Object> expression = asMap(expressionObj);
        Object dialectsObj = expression.get(DIALECTS);

        List<Object> dialects = asList(dialectsObj);

        Object selectedDialectObj = null;
        for (Object dialectObj : dialects) {
            Map<String, Object> dialect = asMap(dialectObj);
            String dialectType = getString(dialect, DIALECT);
            if (DIALECT_TABLEAU.equals(dialectType)) {
                selectedDialectObj = dialectObj;
                break;
            }
        }

        if (selectedDialectObj == null) {
            selectedDialectObj = dialects.get(0);
        }

        Map<String, Object> selectedDialect = asMap(selectedDialectObj);
        Object expressionValue = selectedDialect.get(EXPRESSION);
        String dialectType = getString(selectedDialect, DIALECT);

        return new ExpressionInfo((String) expressionValue, dialectType);
    }

    /**
     * Determines if an expression is calculated or a direct column reference.
     *
     * <p>A calculated expression contains:
     * <ul>
     *   <li>SQL functions: CONCAT(), SUM(), CAST(), etc.</li>
     *   <li>Operators: +, -, *, /, %, ||</li>
     *   <li>SQL keywords: CASE, WHEN, AND, OR, etc.</li>
     *   <li>Comparisons: {@literal >, <, =, !=, <>}</li>
     * </ul>
     *
     * <p>A direct reference is a simple column name (possibly table-qualified):
     * <ul>
     *   <li>customer_name</li>
     *   <li>customers.customer_name</li>
     *   <li>schema.table.column</li>
     * </ul>
     *
     * @param expression The SQL expression to evaluate
     * @return true if calculated, false if direct reference
     */
    private boolean isCalculatedExpression(String expression) {
        if (expression == null || expression.isEmpty()) {
            return false;
        }

        String normalized = expression.trim().toUpperCase();

        // Check for function calls (presence of parentheses)
        if (normalized.contains("(") || normalized.contains("[")) {
            return true;
        }

        // Check for operators (arithmetic, comparison, string concatenation)
        if (normalized.contains("*") || normalized.contains("/") || normalized.contains("%") ||
            normalized.contains("||") || normalized.contains("::") ||
            normalized.contains(">") || normalized.contains("<") ||
            normalized.contains("!=") || normalized.contains("<>")) {
            return true;
        }

        // Check for arithmetic/comparison operators with spaces (avoid false positives like "customer-id")
        if (normalized.contains(" + ") || normalized.contains(" - ") ||
            normalized.contains(" * ") || normalized.contains(" / ") ||
            normalized.contains(" = ")) {
            return true;
        }

        // Check for SQL keywords using compiled pattern
        return CALCULATED_KEYWORDS_PATTERN.matcher(normalized).find();
    }

    /**
     * Applies default values for required Salesforce field properties.
     * Only sets defaults if the property is not already present.
     * Defaults are applied AFTER custom extensions and mappings.
     *
     * @param sfField The Salesforce field to apply defaults to
     */
    private void applyFieldDefaults(Map<String, Object> sfField) {
        sfField.putIfAbsent(DISPLAY_CATEGORY, DISPLAY_CATEGORY_CONTINUOUS);
    }

    /**
     * Processes model-level semanticCalculatedDimensions and converts them to dataset fields
     * if all their dependencies point to the same data object.
     *
     * <p>Logic:
     * <ul>
     *   <li>If all dependencies have the same dependentDefinitionApiName:
     *       convert to field and add to that dataset</li>
     *   <li>Otherwise: leave in sourceData for custom extension handling</li>
     * </ul>
     *
     * @param sourceData The source Salesforce data (will be modified to remove converted dimensions)
     * @param outputData The output Ossie data containing datasets
     */
    private void processModelLevelCalculatedDimensions(
            Map<String, Object> sourceData, Map<String, Object> outputData) {

        List<Object> calcDims = getList(sourceData, SEMANTIC_CALCULATED_DIMENSIONS);
        if (calcDims == null) {
            return;
        }

        logger.debug("Processing {} semanticCalculatedDimensions", calcDims.size());

        List<Object> osiDatasets = getList(outputData, DATASETS);
        List<Object> remainingCalcDims = new ArrayList<>();

        for (Object calcDimObj : calcDims) {
            Map<String, Object> calcDim = asMap(calcDimObj);

            List<Object> dependencies = getList(calcDim, DEPENDENCIES);
            String targetDataObject = getSingleDataObjectFromDependencies(dependencies);

            if (targetDataObject != null) {
                Map<String, Object> osiDataset = findItemById(osiDatasets, NAME, targetDataObject);
                if (osiDataset != null) {
                    Map<String, Object> osiField = convertCalculatedDimensionToField(calcDim);
                    List<Object> fields = getOrCreateList(osiDataset, FIELDS);
                    fields.add(osiField);

                    // Update relationships for this converted field
                    String calcFieldName = getString(calcDim, API_NAME);
                    updateRelationshipsForConvertedField(sourceData, calcFieldName);

                    logger.debug("Converted calculated dimension '{}' to field in dataset '{}'",
                            getString(calcDim, API_NAME), targetDataObject);
                    continue;
                }
            }
            remainingCalcDims.add(calcDim);
        }

        // Update sourceData with only the remaining calculated dimensions
        // The ones that couldn't be converted to dataset fields
        if (remainingCalcDims.isEmpty()) {
            sourceData.remove(SEMANTIC_CALCULATED_DIMENSIONS);
            logger.debug("All calculated dimensions converted to dataset fields");
        } else {
            sourceData.put(SEMANTIC_CALCULATED_DIMENSIONS, remainingCalcDims);
            logger.debug("{} calculated dimensions kept for custom extension handling", remainingCalcDims.size());
        }
    }

    /**
     * Checks if all dependencies point to the same data object.
     *
     * @param dependencies List of dependency objects
     * @return The common data object API name if all dependencies reference the same object,
     *         null if dependencies are empty, mixed, or missing dependentDefinitionApiName
     */
    private String getSingleDataObjectFromDependencies(List<Object> dependencies) {
        if (dependencies == null || dependencies.isEmpty()) {
            return null;
        }

        String commonDataObject = null;
        for (Object depObj : dependencies) {
            Map<String, Object> dep = asMap(depObj);
            String defApiName = getString(dep, DEPENDENT_DEFINITION_API_NAME);

            if (defApiName == null) {
                continue;
            }

            if (commonDataObject == null) {
                commonDataObject = defApiName;
            } else if (!commonDataObject.equals(defApiName)) {
                return null;
            }
        }

        return commonDataObject;
    }

    /**
     * Converts a Salesforce semanticCalculatedDimension to an Ossie field with dimension property.
     * Similar to convertDimensionToOsiField but handles expression (not dataObjectFieldName).
     *
     * @param calcDim The Salesforce calculated dimension
     * @return An Ossie field map with dimension property and wrapped expression
     */
    private Map<String, Object> convertCalculatedDimensionToField(Map<String, Object> calcDim) {
        Map<String, Object> osiField = new LinkedHashMap<>();

        // Common properties: name, label, description
        mapCommonFieldProperties(calcDim, osiField);

        // Add dimension property with is_time based on dataType
        Map<String, Object> dimensionProp = new LinkedHashMap<>();
        String dataType = getString(calcDim, DATA_TYPE);
        if (DATA_TYPE_DATE.equals(dataType) || DATA_TYPE_DATE_TIME.equals(dataType)) {
            dimensionProp.put(IS_TIME, true);
        } else {
            dimensionProp.put(IS_TIME, false);
        }
        osiField.put(DIMENSION, dimensionProp);

        // Get expression (calculated dimensions have expression, not dataObjectFieldName)
        String expressionValue = getString(calcDim, EXPRESSION);
        if (expressionValue != null) {
            osiField.put(EXPRESSION, wrapExpression(expressionValue));
        }

        Set<String> handledProps = Set.of(API_NAME, LABEL, DESCRIPTION, EXPRESSION, DEPENDENCIES);
        customExtensionHandler.storeUnmappedItemProperties(osiField, calcDim, handledProps);
        return osiField;
    }

    /**
     * Updates relationships that reference a converted calculated field.
     * Changes fieldType from "SemanticField" to "TableField" for the specific field.
     *
     * @param sourceData The source Salesforce data containing relationships
     * @param calcFieldName The API name of the calculated field that was converted
     */
    private void updateRelationshipsForConvertedField(Map<String, Object> sourceData, String calcFieldName) {
        List<Object> relationships = getList(sourceData, SEMANTIC_RELATIONSHIPS);
        if (relationships == null) {
            return;
        }

        for (Object relObj : relationships) {
            Map<String, Object> rel = asMap(relObj);
            List<Object> criteria = getList(rel, CRITERIA);
            if (criteria == null) {
                continue;
            }

            for (Object critObj : criteria) {
                Map<String, Object> criterion = asMap(critObj);

                if (FIELD_TYPE_SEMANTIC_FIELD.equals(getString(criterion, LEFT_FIELD_TYPE))) {
                    if (calcFieldName.equals(getString(criterion, LEFT_SEMANTIC_FIELD_API_NAME))) {
                        criterion.put(LEFT_FIELD_TYPE, FIELD_TYPE_TABLE_FIELD);
                        logger.debug("Updated left field '{}' type from SemanticField to TableField", calcFieldName);
                    }
                }

                if (FIELD_TYPE_SEMANTIC_FIELD.equals(getString(criterion, RIGHT_FIELD_TYPE))) {
                    if (calcFieldName.equals(getString(criterion, RIGHT_SEMANTIC_FIELD_API_NAME))) {
                        criterion.put(RIGHT_FIELD_TYPE, FIELD_TYPE_TABLE_FIELD);
                        logger.debug("Updated right field '{}' type from SemanticField to TableField", calcFieldName);
                    }
                }
            }
        }
    }

}
