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

package org.apache.ossie.mapper;

import java.util.Map;

/**
 * Interface for mapping properties between Ossie and Salesforce formats during conversion.
 *
 * <p>Implementations of this interface define bidirectional mappings between Ossie YAML format
 * and Salesforce JSON format. This supports nested paths using dot notation
 * (e.g., "datasets.name" ↔ "semanticDataObjects.apiName").</p>
 *
 *
 */
public interface PropertyMapper {

    /**
     * Returns the mapping from Ossie property paths to Salesforce property paths.
     *
     * @return a map where keys are Ossie property paths and values are Salesforce property paths
     */
    Map<String, String> getOsiToSalesforceMappings();

    /**
     * Returns the mapping from Salesforce property paths to Ossie property paths.
     * <p>This is the reverse mapping of {@link #getOsiToSalesforceMappings()}.
     *
     * @return a map where keys are Salesforce property paths and values are Ossie property paths
     */
    Map<String, String> getSalesforceToOsiMappings();
}
