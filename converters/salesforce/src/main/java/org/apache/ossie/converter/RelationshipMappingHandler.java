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

import static org.apache.ossie.converter.ConverterConstants.*;
import static org.apache.ossie.util.DataStructureUtils.*;

import org.apache.ossie.converter.ConverterConstants.Level;
import org.apache.ossie.converter.pipeline.PipelineStep;
import java.util.*;

import org.apache.ossie.util.MappingUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Bidirectional handler for mapping relationships between Ossie and Salesforce formats.
 *
 * <p>Supports both conversion directions:
 * <ul>
 *   <li>Ossie → Salesforce: from/to/from_columns/to_columns → criteria array</li>
 *   <li>Salesforce → Ossie: criteria array → from/to/from_columns/to_columns</li>
 * </ul>
 *
 */
public class RelationshipMappingHandler implements PipelineStep {

    private static final Logger logger = LoggerFactory.getLogger(RelationshipMappingHandler.class);

    private final ConversionDirection direction;
    private final CustomExtensionHandler customExtensionHandler;

    public RelationshipMappingHandler(ConversionDirection direction, CustomExtensionHandler customExtensionHandler) {
        this.direction = direction;
        this.customExtensionHandler = customExtensionHandler;
    }

    @Override
    public void execute(Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {
        logger.debug("Mapping relationships in {} direction", direction);
        if (direction == ConversionDirection.OSI_TO_SALESFORCE) {
            mapOsiToSalesforce(sourceData, outputData, mappings);
        } else {
            mapSalesforceToOsi(sourceData, outputData, mappings);
        }
    }

    /**
     * Maps Ossie relationships to Salesforce semanticRelationships.
     */
    private void mapOsiToSalesforce(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        List<Object> osiRelationships = getList(sourceData, RELATIONSHIPS);
        if (osiRelationships == null) {
            return;
        }

        // Validate and filter relationships - remove those with non-existent fields
        List<Object> validRelationships = validateAndFilterRelationships(osiRelationships, outputData);
        if (validRelationships.isEmpty()) {
            return;
        }

        // Update sourceData with filtered relationships
        sourceData.put(RELATIONSHIPS, validRelationships);

        Map<String, String> relationshipMappings = MappingUtils.filterMappingsByPrefix(mappings, RELATIONSHIPS);

        Map<String, Object> mappedData = GenericMappingEngine.applyMappings(sourceData, relationshipMappings);
        relationshipMappings.keySet().forEach(mappings::remove);

        outputData.putAll(mappedData);

        // Apply manual mappings (criteria)
        List<Object> sfRelationships = getList(outputData, SEMANTIC_RELATIONSHIPS);
        if (sfRelationships != null) {
            reconstructCriteria(osiRelationships, sfRelationships);
        }

        customExtensionHandler.restoreCustomExtensionsAtLevel(outputData, sourceData, Level.RELATIONSHIPS);

        if (sfRelationships != null) {
            applyDefaults(sfRelationships);
        }
    }

    /**
     * Maps Salesforce semanticRelationships to Ossie relationships.
     */
    private void mapSalesforceToOsi(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        List<Object> sfRelationships = getList(sourceData, SEMANTIC_RELATIONSHIPS);
        if (sfRelationships == null) {
            return;
        }

        // Filter out unsupported relationships (Formula/SemanticField) and store at model level
        List<Object> unsupportedRelationships = new ArrayList<>();
        List<Object> supportedRelationships = new ArrayList<>();

        for (Object relObj : sfRelationships) {
            Map<String, Object> sfRel = asMap(relObj);
            if (hasUnsupportedFieldTypes(sfRel)) {
                unsupportedRelationships.add(sfRel);
            } else {
                supportedRelationships.add(sfRel);
            }
        }

        // Store unsupported relationships in custom_extensions at SEMANTIC_MODEL level
        if (!unsupportedRelationships.isEmpty()) {
            storeUnsupportedRelationshipsAtModelLevel(outputData, unsupportedRelationships);
        }

        // Only process supported relationships
        if (supportedRelationships.isEmpty()) {
            return;
        }

        // Update sourceData to only contain supported relationships
        sourceData.put(SEMANTIC_RELATIONSHIPS, supportedRelationships);

        Map<String, String> relationshipMappings =
                MappingUtils.filterMappingsByPrefix(mappings, SEMANTIC_RELATIONSHIPS);

        Set<String> allHandledProps = relationshipMappings.isEmpty()? new HashSet<>() : MappingUtils.extractHandledProperties(relationshipMappings);

        allHandledProps.add(LEFT_SEMANTIC_DEFINITION_API_NAME);
        allHandledProps.add(RIGHT_SEMANTIC_DEFINITION_API_NAME);
        allHandledProps.add(CRITERIA);

        Map<String, Object> mappedData = GenericMappingEngine.applyMappings(sourceData, relationshipMappings);
        relationshipMappings.keySet().forEach(mappings::remove);

        outputData.putAll(mappedData);

        List<Object> osiRelationships = getList(outputData, RELATIONSHIPS);
        if (osiRelationships != null) {
            deconstructCriteria(supportedRelationships, osiRelationships);
        }

        // Store unmapped SF properties in custom_extensions
        customExtensionHandler.storeUnmappedProperties(outputData, sourceData, allHandledProps, Level.RELATIONSHIPS);

        // Cleanup: remove processed structural key
        sourceData.remove(SEMANTIC_RELATIONSHIPS);
    }

    /**
     * Reconstructs criteria for Ossie→SF conversion.
     */
    private void reconstructCriteria(List<Object> osiRelationships, List<Object> sfRelationships) {
        for (int i = 0; i < osiRelationships.size() && i < sfRelationships.size(); i++) {
            Map<String, Object> osiRel = asMap(osiRelationships.get(i));
            Map<String, Object> sfRel = asMap(sfRelationships.get(i));

            String fromEntity = getString(osiRel, FROM);
            String toEntity = getString(osiRel, TO);

            if (fromEntity != null) {
                sfRel.put(LEFT_SEMANTIC_DEFINITION_API_NAME, fromEntity);
            }
            if (toEntity != null) {
                sfRel.put(RIGHT_SEMANTIC_DEFINITION_API_NAME, toEntity);
            }

            Object fromColumnsObj = osiRel.get(FROM_COLUMNS);
            Object toColumnsObj = osiRel.get(TO_COLUMNS);

            if (fromColumnsObj == null || toColumnsObj == null) {
                return;
            }

            List<Object> fromColumns = asList(fromColumnsObj);
            List<Object> toColumns = asList(toColumnsObj);

            if (fromColumns.isEmpty() || toColumns.isEmpty()) {
                return;
            }

            if (fromColumns.size() != toColumns.size()) {
                return;
            }

            List<Map<String, Object>> criteriaArray = new ArrayList<>();
            for (int j = 0; j < fromColumns.size(); j++) {
                String fromCol = (String) fromColumns.get(j);
                String toCol = (String) toColumns.get(j);

                Map<String, Object> criterion = new LinkedHashMap<>();
                criterion.put(LEFT_SEMANTIC_FIELD_API_NAME, fromCol);
                criterion.put(RIGHT_SEMANTIC_FIELD_API_NAME, toCol);
                criteriaArray.add(criterion);
            }

            if (!criteriaArray.isEmpty()) {
                sfRel.put(CRITERIA, criteriaArray);
            }
        }
    }

    /**
     * Deconstructs criteria for SF→Ossie conversion.
     * Note: This method only processes supported relationships (TableField types).
     * Unsupported relationships are filtered out earlier and stored in custom_extensions.
     */
    private void deconstructCriteria(List<Object> sfRelationships, List<Object> osiRelationships) {
        for (int i = 0; i < sfRelationships.size() && i < osiRelationships.size(); i++) {
            Map<String, Object> sfRel = asMap(sfRelationships.get(i));
            Map<String, Object> osiRel = asMap(osiRelationships.get(i));

            // Extract leftSemanticDefinitionApiName → from
            String leftDef = getString(sfRel, LEFT_SEMANTIC_DEFINITION_API_NAME);
            if (leftDef != null) {
                osiRel.put(FROM, leftDef);
            }

            // Extract rightSemanticDefinitionApiName → to
            String rightDef = getString(sfRel, RIGHT_SEMANTIC_DEFINITION_API_NAME);
            if (rightDef != null) {
                osiRel.put(TO, rightDef);
            }

            Object criteriaObj = sfRel.get(CRITERIA);
            if (criteriaObj != null) {
                List<Object> criteria = asList(criteriaObj);

                List<String> fromColumns = new ArrayList<>();
                List<String> toColumns = new ArrayList<>();

                for (Object criterionObj : criteria) {
                    Map<String, Object> criterion = asMap(criterionObj);

                    String leftField = getString(criterion, LEFT_SEMANTIC_FIELD_API_NAME);
                    String rightField = getString(criterion, RIGHT_SEMANTIC_FIELD_API_NAME);

                    if (leftField != null && rightField != null) {
                        fromColumns.add(leftField);
                        toColumns.add(rightField);
                    }
                }

                if (!fromColumns.isEmpty()) {
                    osiRel.put(FROM_COLUMNS, fromColumns);
                }
                if (!toColumns.isEmpty()) {
                    osiRel.put(TO_COLUMNS, toColumns);
                }
            }
        }
    }

    /**
     * Applies default values for required Salesforce relationship fields.
     * Only sets defaults if the property is not already present.
     */
    private void applyDefaults(List<Object> sfRelationships) {
        for (Object relObj : sfRelationships) {
            Map<String, Object> sfRel = asMap(relObj);

            sfRel.putIfAbsent(CARDINALITY, DEFAULT_CARDINALITY);
            sfRel.putIfAbsent(IS_ENABLED, true);
            sfRel.putIfAbsent(JOIN_TYPE, DEFAULT_JOIN_TYPE);
        }
    }

    /**
     * Validates and filters relationships, removing those that reference non-existent fields. (Calculated fields that are not supported)
     *
     * @param osiRelationships List of Ossie relationships to validate
     * @param outputData The output data containing semanticDataObjects with their fields
     * @return Filtered list of valid relationships
     */
    private List<Object> validateAndFilterRelationships(List<Object> osiRelationships, Map<String, Object> outputData) {
        List<Object> validRelationships = new ArrayList<>();
        List<Object> sfDataObjects = getList(outputData, SEMANTIC_DATA_OBJECTS);

        for (Object relObj : osiRelationships) {
            Map<String, Object> osiRel = asMap(relObj);
            String relName = getString(osiRel, NAME);
            String fromEntity = getString(osiRel, FROM);
            String toEntity = getString(osiRel, TO);

            Map<String, Object> fromDataObject = findDataObjectByName(sfDataObjects, fromEntity);
            Map<String, Object> toDataObject = findDataObjectByName(sfDataObjects, toEntity);

            if (fromDataObject == null || toDataObject == null) {
                logger.debug("Removing relationship '{}' - entity not found", relName);
                continue;
            }

            List<Object> fromColumns = getList(osiRel, FROM_COLUMNS);
            List<Object> toColumns = getList(osiRel, TO_COLUMNS);

            if (!validateColumns(fromColumns, fromDataObject, fromEntity, relName) ||
                !validateColumns(toColumns, toDataObject, toEntity, relName)) {
                continue;
            }
            validRelationships.add(osiRel);
        }
        return validRelationships;
    }

    /**
     * Validates that all columns exist in the given data object.
     *
     * @param columns List of column names to validate
     * @param dataObject The data object containing the fields
     * @param entityName The entity name (for logging)
     * @param relName The relationship name (for logging)
     * @return true if all columns exist, false otherwise
     */
    private boolean validateColumns(List<Object> columns, Map<String, Object> dataObject,
                                     String entityName, String relName) {
        if (columns == null || columns.isEmpty()) {
            return true;
        }

        for (Object colObj : columns) {
            String columnName = (String) colObj;
            if (!fieldExistsInDataObject(dataObject, columnName)) {
                logger.debug("Removing relationship '{}' - column '{}' not found in entity '{}'",
                        relName, columnName, entityName);
                return false;
            }
        }

        return true;
    }

    /**
     * Finds a data object by its apiName.
     */
    private Map<String, Object> findDataObjectByName(List<Object> dataObjects, String name) {
        for (Object obj : dataObjects) {
            Map<String, Object> dataObject = asMap(obj);
            String apiName = getString(dataObject, API_NAME);
            if (name.equals(apiName)) {
                return dataObject;
            }
        }
        return null;
    }

    /**
     * Checks if a field exists in a data object's semanticDimensions or semanticMeasurements.
     */
    private boolean fieldExistsInDataObject(Map<String, Object> dataObject, String fieldName) {
        // Check both semanticDimensions and semanticMeasurements
        for (String fieldListKey : List.of(SEMANTIC_DIMENSIONS, SEMANTIC_MEASUREMENTS)) {
            List<Object> fields = getList(dataObject, fieldListKey);
            if (fields != null) {
                for (Object fieldObj : fields) {
                    Map<String, Object> field = asMap(fieldObj);
                    String apiName = getString(field, API_NAME);
                    if (fieldName.equals(apiName)) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    /**
     * Checks if a relationship has unsupported field types (Formula or SemanticField).
     *
     * @param relationship The relationship to check
     * @return true if the relationship contains Formula or SemanticField types
     */
    private boolean hasUnsupportedFieldTypes(Map<String, Object> relationship) {
        Object criteriaObj = relationship.get(CRITERIA);
        if (criteriaObj == null) {
            return false;
        }

        List<Object> criteria = asList(criteriaObj);
        for (Object criterionObj : criteria) {
            Map<String, Object> criterion = asMap(criterionObj);
            String leftFieldType = getString(criterion, LEFT_FIELD_TYPE);
            String rightFieldType = getString(criterion, RIGHT_FIELD_TYPE);

            if (FIELD_TYPE_FORMULA.equals(leftFieldType) || FIELD_TYPE_FORMULA.equals(rightFieldType) ||
                FIELD_TYPE_SEMANTIC_FIELD.equals(leftFieldType) || FIELD_TYPE_SEMANTIC_FIELD.equals(rightFieldType)) {
                logger.debug("Relationship '{}' contains unsupported field types (Formula/SemanticField)",
                        getString(relationship, API_NAME));
                return true;
            }
        }
        return false;
    }

    /**
     * Stores unsupported relationships in custom_extensions at the SEMANTIC_MODEL level.
     *
     * @param outputData The output data structure
     * @param unsupportedRelationships List of relationships that cannot be converted
     */
    private void storeUnsupportedRelationshipsAtModelLevel(Map<String, Object> outputData,
                                                            List<Object> unsupportedRelationships) {
        Map<String, Object> customData = new LinkedHashMap<>();
        customData.put(SEMANTIC_RELATIONSHIPS, unsupportedRelationships);

        customExtensionHandler.addCustomExtension(outputData, customData);
    }
}
