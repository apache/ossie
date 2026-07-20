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

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.exception.InvalidInputException;
import java.io.IOException;
import java.io.InputStream;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * Property mapper that loads mappings from a YAML configuration file.
 *
 * <p>This mapper reads the bundled mappings.yaml which defines Ossie-Salesforce
 * property mappings. The transformation is performed by the converter classes.</p>
 *
 */
public class FileBasedPropertyMapper implements PropertyMapper {

    private static final ObjectMapper YAML_MAPPER = new ObjectMapper(new YAMLFactory());

    private final Map<String, String> mappings;
    private final Map<String, String> reverseMappings;

    /**
     * Constructs a FileBasedPropertyMapper with the specified mappings.
     *
     * @param mappings the Ossie-Salesforce mappings
     */
    public FileBasedPropertyMapper(Map<String, String> mappings) {
        this.mappings = mappings != null ? new LinkedHashMap<>(mappings) : new LinkedHashMap<>();
        this.reverseMappings = generateReverseMappings();
    }

    /**
     * Generates reverse mappings (Salesforce to Ossie) from the Ossie to Salesforce mappings.
     *
     * @return the Salesforce-to-Ossie mappings
     */
    private Map<String, String> generateReverseMappings() {
        return mappings.entrySet().stream().collect(Collectors.toMap(Map.Entry::getValue, Map.Entry::getKey));
    }

    /**
     * Creates a FileBasedPropertyMapper from a classpath resource.
     *
     * @param resourcePath the path to the YAML resource (e.g., "/mappings.yaml")
     * @return a new FileBasedPropertyMapper
     * @throws InvalidInputException if the resource cannot be read or parsed
     */
    public static FileBasedPropertyMapper fromResource(String resourcePath) {
        try (InputStream is = FileBasedPropertyMapper.class.getResourceAsStream(resourcePath)) {
            if (is == null) {
                throw new InvalidInputException("Mapping configuration resource not found: " + resourcePath);
            }

            String content = new String(is.readAllBytes());
            return parseYaml(content);
        } catch (IOException e) {
            throw new InvalidInputException("Failed to read mapping configuration resource: " + resourcePath, e);
        }
    }

    private static FileBasedPropertyMapper parseYaml(String content) {
        try {
            Map<String, String> mappings = YAML_MAPPER.readValue(
                content,
                new TypeReference<LinkedHashMap<String, String>>() {}
            );
            return new FileBasedPropertyMapper(mappings);
        } catch (Exception e) {
            throw new InvalidInputException("Failed to parse YAML mapping configuration: " + e.getMessage(), e);
        }
    }

    @Override
    public Map<String, String> getOsiToSalesforceMappings() {
        return Collections.unmodifiableMap(mappings);
    }

    @Override
    public Map<String, String> getSalesforceToOsiMappings() {
        return Collections.unmodifiableMap(reverseMappings);
    }

    @Override
    public String toString() {
        return "FileBasedPropertyMapper{" + "mappingCount=" + mappings.size() + '}';
    }
}
