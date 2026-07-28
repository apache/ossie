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

/**
 * Constants used across converters for property names and structure keys.
 *
 */
public final class ConverterConstants {

    private ConverterConstants() {}

    /**
     * Enum representing the level at which custom extensions can be stored or restored.
     */
    public enum Level {
        SEMANTIC_MODEL,
        DATASETS,
        RELATIONSHIPS,
        METRICS
    }

    // Ossie root structure
    public static final String VERSION = "version";
    public static final String OSI_VERSION = "0.2.0.dev0";
    public static final String SEMANTIC_MODEL = "semantic_model";

    // Ossie semantic model structure
    public static final String CUSTOM_EXTENSIONS = "custom_extensions";
    public static final String DATASETS = "datasets";
    public static final String FIELDS = "fields";
    public static final String METRICS = "metrics";

    // Salesforce semantic model structure
    public static final String SEMANTIC_DATA_OBJECTS = "semanticDataObjects";
    public static final String SEMANTIC_RELATIONSHIPS = "semanticRelationships";
    public static final String SEMANTIC_CALCULATED_MEASUREMENTS = "semanticCalculatedMeasurements";
    public static final String SEMANTIC_DIMENSIONS = "semanticDimensions";
    public static final String SEMANTIC_MEASUREMENTS = "semanticMeasurements";
    public static final String SEMANTIC_CALCULATED_DIMENSIONS = "semanticCalculatedDimensions";

    public static final String DEPENDENCIES = "dependencies";
    public static final String DEPENDENT_DEFINITION_API_NAME = "dependentDefinitionApiName";

    // Common property names
    public static final String NAME = "name";
    public static final String API_NAME = "apiName";
    public static final String LABEL = "label";
    public static final String DESCRIPTION = "description";
    public static final String DATA_TYPE = "dataType";
    public static final String OSI_DATATYPE = "datatype";
    public static final String AI_CONTEXT = "ai_context";
    public static final String BUSINESS_PREFERENCES = "businessPreferences";

    // AI Context object properties
    public static final String AI_INSTRUCTIONS = "instructions";
    public static final String AI_SYNONYMS = "synonyms";
    public static final String AI_EXAMPLES = "examples";

    // Field properties
    public static final String DIMENSION = "dimension";
    public static final String IS_TIME = "is_time";
    public static final String DATA_OBJECT_FIELD_NAME = "dataObjectFieldName";

    // Custom extensions structure
    public static final String VENDOR_NAME = "vendor_name";
    public static final String DATA = "data";
    public static final String VENDOR_NAME_VALUE = "SALESFORCE";

    // Expression properties
    public static final String EXPRESSION = "expression";
    public static final String DIALECTS = "dialects";
    public static final String DIALECT = "dialect";
    public static final String DIALECT_TABLEAU = "TABLEAU";

    // Relationship properties
    public static final String CRITERIA = "criteria";
    public static final String RELATIONSHIPS = "relationships";
    public static final String FROM = "from";
    public static final String TO = "to";
    public static final String FROM_COLUMNS = "from_columns";
    public static final String TO_COLUMNS = "to_columns";
    public static final String LEFT_SEMANTIC_FIELD_API_NAME = "leftSemanticFieldApiName";
    public static final String RIGHT_SEMANTIC_FIELD_API_NAME = "rightSemanticFieldApiName";
    public static final String LEFT_SEMANTIC_DEFINITION_API_NAME = "leftSemanticDefinitionApiName";
    public static final String RIGHT_SEMANTIC_DEFINITION_API_NAME = "rightSemanticDefinitionApiName";

    // Relationship criteria field types
    public static final String LEFT_FIELD_TYPE = "leftFieldType";
    public static final String RIGHT_FIELD_TYPE = "rightFieldType";
    public static final String FIELD_TYPE_TABLE_FIELD = "TableField";
    public static final String FIELD_TYPE_SEMANTIC_FIELD = "SemanticField";
    public static final String FIELD_TYPE_FORMULA = "Formula";

    // Default values settings
    public static final String CARDINALITY = "cardinality";
    public static final String IS_ENABLED = "isEnabled";
    public static final String JOIN_TYPE = "joinType";
    public static final String TABLE_TYPE = "tableType";
    public static final String DISPLAY_CATEGORY = "displayCategory";
    public static final String DISPLAY_CATEGORY_CONTINUOUS = "Continuous";
    public static final String STANDARD_TABLE_TYPE = "Standard";
    public static final String DEFAULT_CARDINALITY = "ManyToMany";
    public static final String DEFAULT_JOIN_TYPE = "Auto";

    // Format names
    public static final String JSON = "json";
    public static final String YAML = "yaml";

    // File extensions
    public static final String JSON_EXTENSION = ".json";
    public static final String YAML_EXTENSION = ".yaml";
}
