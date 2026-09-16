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

    private final ConversionDirection direction;
    private final CustomExtensionHandler customExtensionHandler;

    public FieldMappingHandler(ConversionDirection direction, CustomExtensionHandler customExtensionHandler) {
        this.direction = direction;
        this.customExtensionHandler = customExtensionHandler;
    }

    /**
     * Executes field mapping based on conversion direction.
     */
    @Override
    public void execute(Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {
        execute(new ConversionContext(sourceData, outputData), mappings);
    }

    @Override
    public void execute(ConversionContext context, Map<String, String> mappings) {
        logger.debug("Mapping fields in {} direction", direction);
        if (direction == ConversionDirection.OSSIE_TO_SALESFORCE) {
            FieldExpressionPlan plan = new FieldExpressionPlan(context.sourceData(), context.outputData());
            mapOssieToSalesforce(context.sourceData(), context.outputData(), plan);
            context.fieldPlan(plan);
        } else {
            mapSalesforceToOssie(context.sourceData(), context.outputData());
        }
    }

    /** Emit physical bindings first, then compile every derived field against those bindings. */
    private void mapOssieToSalesforce(Map<String, Object> sourceData,
            Map<String, Object> outputData, FieldExpressionPlan plan) {
        List<Object> targets = getList(outputData, SEMANTIC_DATA_OBJECTS);
        Map<String, Map<String, Object>> targetsByName = new LinkedHashMap<>();
        if (targets != null) {
            for (Object item : targets) {
                Map<String, Object> target = asMap(item);
                String name = getString(target, API_NAME);
                if (targetsByName.putIfAbsent(name, target) != null) {
                    throw new IllegalArgumentException("Duplicate exported dataset '" + name + "'");
                }
            }
        }
        for (Object item : getList(sourceData, DATASETS)) {
            Map<String, Object> dataset = asMap(item);
            String datasetName = getString(dataset, NAME);
            Map<String, Object> target = targetsByName.get(datasetName);
            if (target == null) {
                throw new IllegalArgumentException("Dataset '" + datasetName + "' was not exported before field mapping");
            }
            for (FieldExpressionPlan.PlannedField field : plan.fields(datasetName)) {
                if (!field.direct()) continue;
                Map<String, Object> sfField = mapFieldProperties(field.source(), field.physicalColumn());
                customExtensionHandler.restoreSalesforceCustomExtension(sfField, field.source());
                applyOssieDatatype(sfField, field.source());
                applyFieldDefaults(sfField);
                getOrCreateList(target, field.source().containsKey(DIMENSION)
                        ? SEMANTIC_DIMENSIONS : SEMANTIC_MEASUREMENTS).add(sfField);
            }
        }
        plan.compileAll();
        for (Object item : getList(sourceData, DATASETS)) {
            String datasetName = getString(asMap(item), NAME);
            for (FieldExpressionPlan.PlannedField field : plan.fields(datasetName)) {
                if (field.direct()) continue;
                ExpressionCompiler.Binding binding = plan.resolve(datasetName, field.name());
                Map<String, Object> calc = createSemanticCalculatedDimension(field.source(), binding.expression());
                calc.put(API_NAME, field.calculatedApiName());
                calc.put(DEPENDENCIES, plan.dependencies(datasetName, field.name()));
                customExtensionHandler.restoreSalesforceCustomExtension(calc, field.source());
                applyOssieDatatype(calc, field.source());
                String exactType = getString(calc, DATA_TYPE);
                if (exactType != null && !SalesforceDataTypeMapper.areCompatible(binding.datatype(), exactType)) {
                    throw new IllegalArgumentException("Field '" + datasetName + "." + field.name()
                            + "' calculated datatype conflicts with Salesforce extension dataType '" + exactType + "'");
                }
                calc.putIfAbsent(DATA_TYPE, SalesforceDataTypeMapper.toSalesforce(binding.datatype()));
                applyFieldDefaults(calc);
                getOrCreateList(outputData, SEMANTIC_CALCULATED_DIMENSIONS).add(calc);
            }
        }
    }

    /**
     * Maps Salesforce SemanticDimensions and SemanticMeasurements to Ossie dataset fields.
     */
    private void mapSalesforceToOssie(
            Map<String, Object> sourceData, Map<String, Object> outputData) {

        List<Object> sfDataObjects = getList(sourceData, SEMANTIC_DATA_OBJECTS);
        if (sfDataObjects == null) {
            return;
        }

        List<Object> ossieDatasets = getList(outputData, DATASETS);

        for (Object sfDataObjectObj : sfDataObjects) {
            Map<String, Object> sfDataObject = asMap(sfDataObjectObj);

            String apiName = getString(sfDataObject, API_NAME);
            if (apiName == null) continue;

            // Find matching Ossie dataset by name (mapped from apiName)
            Map<String, Object> ossieDataset = findItemById(ossieDatasets, NAME, apiName);
            if (ossieDataset == null) continue;

            // Convert SF dimensions and measurements to Ossie fields
            convertSalesforceFieldsToOssie(sfDataObject, ossieDataset);
        }

        // Process model-level calculated dimensions
        processModelLevelCalculatedDimensions(sourceData, outputData);

        // Cleanup: remove processed structural key
        sourceData.remove(SEMANTIC_DATA_OBJECTS);
    }

    /**
     * Converts Salesforce dimensions and measurements to Ossie fields for a dataset.
     */
    private void convertSalesforceFieldsToOssie(
            Map<String, Object> sfDataObject, Map<String, Object> ossieDataset) {
        List<Object> ossieFields = getOrCreateList(ossieDataset, FIELDS);

        // Process semanticDimensions → Ossie fields
        List<Object> sfDimensions = getList(sfDataObject, SEMANTIC_DIMENSIONS);
        if (sfDimensions != null) {
            for (Object sfDimObj : sfDimensions) {
                Map<String, Object> sfDim = asMap(sfDimObj);
                Map<String, Object> ossieField = convertDimensionToOssieField(sfDim);
                ossieFields.add(ossieField);
            }
        }

        // Process semanticMeasurements → Ossie fields
        List<Object> sfMeasurements = getList(sfDataObject, SEMANTIC_MEASUREMENTS);
        if (sfMeasurements != null) {
            for (Object sfMeasObj : sfMeasurements) {
                Map<String, Object> sfMeas = asMap(sfMeasObj);
                Map<String, Object> ossieField = convertMeasurementToOssieField(sfMeas);
                ossieFields.add(ossieField);
            }
        }
    }

    /**
     * Converts a Salesforce dimension to an Ossie field with dimension property.
     */
    private Map<String, Object> convertDimensionToOssieField(Map<String, Object> sfDimension) {
        Map<String, Object> ossieField = new LinkedHashMap<>();

        mapCommonFieldProperties(sfDimension, ossieField);

        // Add dimension property with is_time based on dataType
        Map<String, Object> dimensionProp = new LinkedHashMap<>();
        String dataType = getString(sfDimension, DATA_TYPE);
        if (SalesforceDataTypeMapper.isTemporalSalesforceType(dataType)) {
            dimensionProp.put(IS_TIME, true);
        } else {
            dimensionProp.put(IS_TIME, false);
        }
        ossieField.put(DIMENSION, dimensionProp);

        // Wrap dataObjectFieldName in expression structure
        String dataObjectFieldName = getString(sfDimension, DATA_OBJECT_FIELD_NAME);
        if (dataObjectFieldName != null) {
            ossieField.put(EXPRESSION, wrapPhysicalExpression(dataObjectFieldName));
        }

        // Store unmapped properties in custom_extensions
        customExtensionHandler.storeUnmappedItemProperties(ossieField, sfDimension, SF_FIELD_HANDLED_PROPS);

        return ossieField;
    }

    /**
     * Converts a Salesforce measurement to an Ossie field without dimension property.
     */
    private Map<String, Object> convertMeasurementToOssieField(Map<String, Object> sfMeasurement) {
        Map<String, Object> ossieField = new LinkedHashMap<>();

        mapCommonFieldProperties(sfMeasurement, ossieField);

        String dataObjectFieldName = getString(sfMeasurement, DATA_OBJECT_FIELD_NAME);
        if (dataObjectFieldName != null) {
            ossieField.put(EXPRESSION, wrapPhysicalExpression(dataObjectFieldName));
        }

        // Store unmapped properties in custom_extensions
        customExtensionHandler.storeUnmappedItemProperties(ossieField, sfMeasurement, SF_FIELD_HANDLED_PROPS);

        return ossieField;
    }

    /**
     * Maps common field properties from Salesforce to Ossie format.
     * Common properties: name (from apiName), label, description.
     */
    private void mapCommonFieldProperties(Map<String, Object> sfField, Map<String, Object> ossieField) {
        String apiName = getString(sfField, API_NAME);
        ossieField.put(NAME, apiName);

        String label = getString(sfField, LABEL);
        if (label != null) {
            ossieField.put(LABEL, label);
        }

        String description = getString(sfField, DESCRIPTION);
        if (description != null) {
            ossieField.put(DESCRIPTION, description);
        }

        String datatype = SalesforceDataTypeMapper.toOssie(getString(sfField, DATA_TYPE));
        if (datatype != null) {
            ossieField.put(OSSIE_DATATYPE, datatype);
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

    /** Physical columns are SQL identifiers, even when they contain operators or spaces. */
    private Map<String, Object> wrapPhysicalExpression(String column) {
        return Map.of(DIALECTS, List.of(Map.of(DIALECT, "ANSI_SQL", EXPRESSION,
                "\"" + column.replace("\"", "\"\"") + "\"")));
    }

    /**
     * Maps field properties based on whether the field is calculated.
     * Includes common properties plus type-specific properties.
     *
     * @param ossieField The Ossie field
     * @param expression The extracted expression string
     * @return A map with Salesforce field properties
     */
    private Map<String, Object> mapFieldProperties(
            Map<String, Object> ossieField, String expression) {

        Map<String, Object> sfField = new LinkedHashMap<>();

        String name = getString(ossieField, NAME);
        sfField.put(API_NAME, name);

        String description = getString(ossieField, DESCRIPTION);
        if (description != null) {
            sfField.put(DESCRIPTION, description);
        }

        String label = getString(ossieField, LABEL);
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
     * @param ossieField The Ossie field
     * @param expression The calculated expression
     * @return A map representing a semanticCalculatedDimension
     */
    private Map<String, Object> createSemanticCalculatedDimension(
            Map<String, Object> ossieField, String expression) {

        Map<String, Object> calcDim = new LinkedHashMap<>();

        // Required properties
        String name = getString(ossieField, NAME);
        calcDim.put(API_NAME, name);
        calcDim.put(EXPRESSION, expression);

        // Optional properties
        String description = getString(ossieField, DESCRIPTION);
        if (description != null) {
            calcDim.put(DESCRIPTION, description);
        }

        String label = getString(ossieField, LABEL);
        if (label != null) {
            calcDim.put(LABEL, label);
        }

        // Set syntax for Tableau expressions
        calcDim.put("syntax", "Tua");
        calcDim.put("level", "Row");

        return calcDim;
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
     * Applies the portable Ossie datatype when no exact Salesforce dataType was
     * restored from custom_extensions. Exact extension data wins to preserve
     * Salesforce-specific distinctions such as Email and Currency.
     */
    private void applyOssieDatatype(Map<String, Object> sfField, Map<String, Object> ossieField) {
        String ossieDatatype = getString(ossieField, OSSIE_DATATYPE);
        String exactSalesforceDataType = getString(sfField, DATA_TYPE);
        String mappedSalesforceDataType = SalesforceDataTypeMapper.toSalesforce(ossieDatatype);
        String selectedSalesforceDataType = exactSalesforceDataType != null
                ? exactSalesforceDataType
                : mappedSalesforceDataType;

        if (SalesforceDataTypeMapper.isTimezoneLossyMapping(
                ossieDatatype, selectedSalesforceDataType)) {
            logger.warn(
                    "Field '{}' has Ossie datatype 'DateTime'; Salesforce dataType 'DateTime' "
                            + "cannot preserve the timezone-free distinction and re-imports as 'DateTimeTz'",
                    getString(ossieField, NAME));
        }

        if (exactSalesforceDataType != null) {
            if (ossieDatatype != null
                    && !SalesforceDataTypeMapper.areCompatible(ossieDatatype, exactSalesforceDataType)) {
                throw new IllegalArgumentException("Field '" + getString(ossieField, NAME)
                        + "' has Ossie datatype '" + ossieDatatype
                        + "' that conflicts with Salesforce extension dataType '" + exactSalesforceDataType + "'");
            }
            return;
        }

        if (ossieDatatype == null) {
            return;
        }

        if (mappedSalesforceDataType == null) {
            throw new IllegalArgumentException("Field '" + getString(ossieField, NAME)
                    + "' has Ossie datatype '" + ossieDatatype
                    + "' with no safe Salesforce mapping; provide a compatible native type");
        }
        sfField.put(DATA_TYPE, mappedSalesforceDataType);
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

        List<Object> ossieDatasets = getList(outputData, DATASETS);
        List<Object> remainingCalcDims = new ArrayList<>();

        for (Object calcDimObj : calcDims) {
            Map<String, Object> calcDim = asMap(calcDimObj);

            List<Object> dependencies = getList(calcDim, DEPENDENCIES);
            String targetDataObject = getSingleDataObjectFromDependencies(dependencies);

            if (targetDataObject != null) {
                Map<String, Object> ossieDataset = findItemById(ossieDatasets, NAME, targetDataObject);
                if (ossieDataset != null) {
                    Map<String, Object> ossieField = convertCalculatedDimensionToField(calcDim);
                    List<Object> fields = getOrCreateList(ossieDataset, FIELDS);
                    fields.add(ossieField);

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
     * Similar to convertDimensionToOssieField but handles expression (not dataObjectFieldName).
     *
     * @param calcDim The Salesforce calculated dimension
     * @return An Ossie field map with dimension property and wrapped expression
     */
    private Map<String, Object> convertCalculatedDimensionToField(Map<String, Object> calcDim) {
        Map<String, Object> ossieField = new LinkedHashMap<>();

        // Common properties: name, label, description
        mapCommonFieldProperties(calcDim, ossieField);

        // Add dimension property with is_time based on dataType
        Map<String, Object> dimensionProp = new LinkedHashMap<>();
        String dataType = getString(calcDim, DATA_TYPE);
        if (SalesforceDataTypeMapper.isTemporalSalesforceType(dataType)) {
            dimensionProp.put(IS_TIME, true);
        } else {
            dimensionProp.put(IS_TIME, false);
        }
        ossieField.put(DIMENSION, dimensionProp);

        // Get expression (calculated dimensions have expression, not dataObjectFieldName)
        String expressionValue = getString(calcDim, EXPRESSION);
        if (expressionValue != null) {
            ossieField.put(EXPRESSION, wrapExpression(expressionValue));
        }

        Set<String> handledProps = Set.of(API_NAME, LABEL, DESCRIPTION, EXPRESSION, DEPENDENCIES);
        customExtensionHandler.storeUnmappedItemProperties(ossieField, calcDim, handledProps);
        return ossieField;
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
