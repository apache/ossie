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

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.fasterxml.jackson.dataformat.yaml.YAMLGenerator;
import org.apache.ossie.model.OsiModel;

/**
 * Generates Ossie YAML from the schema-generated {@link OsiModel} model.
 */
public class OsiYamlGenerator {

    private final ObjectMapper yamlMapper;

    public OsiYamlGenerator() {
        YAMLFactory yamlFactory = new YAMLFactory()
                .disable(YAMLGenerator.Feature.WRITE_DOC_START_MARKER)
                .enable(YAMLGenerator.Feature.MINIMIZE_QUOTES)
                .enable(YAMLGenerator.Feature.LITERAL_BLOCK_STYLE);
        this.yamlMapper = new ObjectMapper(yamlFactory);
    }

    /**
     * Generate an Ossie YAML string from a model.
     */
    public String generate(OsiModel model) {
        try {
            return yamlMapper.writeValueAsString(model);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Unable to generate Ossie YAML", e);
        }
    }
}
