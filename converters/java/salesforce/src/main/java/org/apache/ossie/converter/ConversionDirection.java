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
 * Enum representing the direction of conversion.
 *
 */
public enum ConversionDirection {
    /**
     * Converting from Ossie YAML format to Salesforce JSON format.
     */
    OSI_TO_SALESFORCE,

    /**
     * Converting from Salesforce JSON format to Ossie YAML format.
     */
    SALESFORCE_TO_OSI;

    /**
     * Converts enum name to pipeline configuration key.
     * Maps OSI_TO_SALESFORCE -> "osiToSalesforce" and SALESFORCE_TO_OSI -> "salesforceToOsi"
     *
     * @return The pipeline key used in YAML configuration
     */
    public String toPipelineKey() {
        return switch (this) {
            case OSI_TO_SALESFORCE -> "osiToSalesforce";
            case SALESFORCE_TO_OSI -> "salesforceToOsi";
        };
    }
}
