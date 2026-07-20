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

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.model.OsiSchema;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Parses an Ossie YAML document into the schema-generated {@link OsiSchema} model.
 */
public class OsiModelParser {

    private final ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());

    /**
     * Parse an Ossie YAML file from the given path.
     */
    public OsiSchema parse(Path yamlPath) throws IOException {
        try (InputStream inputStream = Files.newInputStream(yamlPath)) {
            return yamlMapper.readValue(inputStream, OsiSchema.class);
        }
    }

    /**
     * Parse an Ossie YAML file from an input stream.
     */
    public OsiSchema parse(InputStream inputStream) {
        try {
            return yamlMapper.readValue(inputStream, OsiSchema.class);
        } catch (IOException e) {
            throw new IllegalArgumentException("Invalid Ossie YAML", e);
        }
    }
}
