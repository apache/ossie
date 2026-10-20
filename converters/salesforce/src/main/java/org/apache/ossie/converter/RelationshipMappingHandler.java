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
import org.apache.ossie.exception.ValidationException;
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
        if (direction == ConversionDirection.OSSIE_TO_SALESFORCE) {
            mapOssieToSalesforce(sourceData, outputData, mappings);
        } else {
            mapSalesforceToOssie(sourceData, outputData, mappings);
        }
    }

    /**
     * Maps Ossie relationships to Salesforce semanticRelationships.
     */
    private void mapOssieToSalesforce(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        List<Object> ossieRelationships = getList(sourceData, RELATIONSHIPS);
        if (ossieRelationships == null) {
            return;
        }

        // Validate and filter relationships - remove those with non-existent fields
        List<Object> validRelationships = validateAndFilterRelationships(ossieRelationships, outputData);

        // Claim the relationship mappings before the filtered list can turn out to be
        // empty. Any left behind are applied generically after every handler has run,
        // which would copy the relationships this handler just rejected into the payload
        // in their Ossie shape, with none of the properties Salesforce names them by.
        Map<String, String> relationshipMappings = MappingUtils.filterMappingsByPrefix(mappings, RELATIONSHIPS);
        relationshipMappings.keySet().forEach(mappings::remove);

        if (validRelationships.isEmpty()) {
            return;
        }

        // Update sourceData with filtered relationships
        sourceData.put(RELATIONSHIPS, validRelationships);

        Map<String, Object> mappedData = GenericMappingEngine.applyMappings(sourceData, relationshipMappings);

        outputData.putAll(mappedData);

        // Apply manual mappings (criteria). The Salesforce relationships were mapped from
        // the filtered list, so criteria have to be rebuilt from that same list: pairing
        // them with the unfiltered one shifts every relationship that follows a dropped one.
        List<Object> sfRelationships = getList(outputData, SEMANTIC_RELATIONSHIPS);
        if (sfRelationships != null) {
            reconstructCriteria(validRelationships, sfRelationships);
        }

        customExtensionHandler.restoreCustomExtensionsAtLevel(outputData, sourceData, Level.RELATIONSHIPS);

        if (sfRelationships != null) {
            applyDefaults(sfRelationships);
        }
    }

    /**
     * Maps Salesforce semanticRelationships to Ossie relationships.
     */
    private void mapSalesforceToOssie(
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
            unescapeCriteriaFormulas(sfRel);
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

        List<Object> ossieRelationships = getList(outputData, RELATIONSHIPS);
        if (ossieRelationships != null) {
            deconstructCriteria(supportedRelationships, ossieRelationships);
        }

        // Store unmapped SF properties in custom_extensions
        customExtensionHandler.storeUnmappedProperties(outputData, sourceData, allHandledProps, Level.RELATIONSHIPS);

        // Cleanup: remove processed structural key
        sourceData.remove(SEMANTIC_RELATIONSHIPS);
    }

    /**
     * Decodes the HTML escaping Salesforce applies to a criterion whose join side is a
     * formula rather than a column. Applied before the relationship is sorted into
     * supported or unsupported, so the text is decoded whether it ends up in
     * {@code from_columns}/{@code to_columns} or is preserved in {@code custom_extensions}.
     * A plain field apiName cannot contain an entity, so decoding leaves it untouched.
     */
    private void unescapeCriteriaFormulas(Map<String, Object> sfRel) {
        Object criteriaObj = sfRel.get(CRITERIA);
        if (criteriaObj == null) {
            return;
        }

        for (Object criterionObj : asList(criteriaObj)) {
            Map<String, Object> criterion = asMap(criterionObj);
            for (String key : List.of(LEFT_SEMANTIC_FIELD_API_NAME, RIGHT_SEMANTIC_FIELD_API_NAME)) {
                String value = getString(criterion, key);
                if (value != null) {
                    criterion.put(key, SalesforceFormulaEscaping.unescape(value));
                }
            }
        }
    }

    /**
     * Reconstructs criteria for Ossie→SF conversion.
     */
    private void reconstructCriteria(List<Object> ossieRelationships, List<Object> sfRelationships) {
        for (int i = 0; i < ossieRelationships.size() && i < sfRelationships.size(); i++) {
            Map<String, Object> ossieRel = asMap(ossieRelationships.get(i));
            Map<String, Object> sfRel = asMap(sfRelationships.get(i));

            String fromEntity = getString(ossieRel, FROM);
            String toEntity = getString(ossieRel, TO);

            if (fromEntity != null) {
                sfRel.put(LEFT_SEMANTIC_DEFINITION_API_NAME, fromEntity);
            }
            if (toEntity != null) {
                sfRel.put(RIGHT_SEMANTIC_DEFINITION_API_NAME, toEntity);
            }

            // validateAndFilterRelationships already rejected any relationship whose columns
            // don't pair up, so every relationship reaching this point has a usable pairing.
            List<Object> fromColumns = asList(ossieRel.get(FROM_COLUMNS));
            List<Object> toColumns = asList(ossieRel.get(TO_COLUMNS));

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
    private void deconstructCriteria(List<Object> sfRelationships, List<Object> ossieRelationships) {
        for (int i = 0; i < sfRelationships.size() && i < ossieRelationships.size(); i++) {
            Map<String, Object> sfRel = asMap(sfRelationships.get(i));
            Map<String, Object> ossieRel = asMap(ossieRelationships.get(i));

            // Extract leftSemanticDefinitionApiName → from
            String leftDef = getString(sfRel, LEFT_SEMANTIC_DEFINITION_API_NAME);
            if (leftDef != null) {
                ossieRel.put(FROM, leftDef);
            }

            // Extract rightSemanticDefinitionApiName → to
            String rightDef = getString(sfRel, RIGHT_SEMANTIC_DEFINITION_API_NAME);
            if (rightDef != null) {
                ossieRel.put(TO, rightDef);
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
                    ossieRel.put(FROM_COLUMNS, fromColumns);
                }
                if (!toColumns.isEmpty()) {
                    ossieRel.put(TO_COLUMNS, toColumns);
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
     * Validates and filters relationships, removing those that reference non-existent fields
     * or entities (Salesforce limitations the spec allows, such as calculated fields it can't
     * join on). A relationship whose join columns don't pair up is not a Salesforce limitation
     * but a spec violation - core-spec/spec.md requires from_columns and to_columns to declare
     * the same number of columns - so that case fails the whole conversion instead of being
     * dropped silently: an unpaired relationship still deploys to Salesforce as an enabled,
     * queryable relationship with empty criteria, a join that joins nothing.
     *
     * @param ossieRelationships List of Ossie relationships to validate
     * @param outputData The output data containing semanticDataObjects with their fields
     * @return Filtered list of valid relationships
     * @throws ValidationException if a relationship's join columns cannot be paired
     */
    private List<Object> validateAndFilterRelationships(List<Object> ossieRelationships, Map<String, Object> outputData) {
        List<Object> validRelationships = new ArrayList<>();
        List<Object> sfDataObjects = getList(outputData, SEMANTIC_DATA_OBJECTS);

        for (Object relObj : ossieRelationships) {
            Map<String, Object> ossieRel = asMap(relObj);
            String relName = getString(ossieRel, NAME);
            String fromEntity = getString(ossieRel, FROM);
            String toEntity = getString(ossieRel, TO);

            List<Object> fromColumns = getList(ossieRel, FROM_COLUMNS);
            List<Object> toColumns = getList(ossieRel, TO_COLUMNS);

            if (!columnsPairable(fromColumns, toColumns)) {
                throw new ValidationException(String.format(
                        "Relationship '%s' cannot be converted: from_columns and to_columns must declare "
                                + "the same number of columns, at least one each (got %d and %d)",
                        relName,
                        fromColumns == null ? 0 : fromColumns.size(),
                        toColumns == null ? 0 : toColumns.size()));
            }

            Map<String, Object> fromDataObject = findDataObjectByName(sfDataObjects, fromEntity);
            Map<String, Object> toDataObject = findDataObjectByName(sfDataObjects, toEntity);

            if (fromDataObject == null || toDataObject == null) {
                logger.debug("Removing relationship '{}' - entity not found", relName);
                continue;
            }

            if (!validateColumns(fromColumns, fromDataObject, fromEntity, relName) ||
                !validateColumns(toColumns, toDataObject, toEntity, relName)) {
                continue;
            }
            validRelationships.add(ossieRel);
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
