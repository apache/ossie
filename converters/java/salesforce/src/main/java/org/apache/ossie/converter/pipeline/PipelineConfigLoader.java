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

package org.apache.ossie.converter.pipeline;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.exception.ConversionException;

import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Loads pipeline configuration from YAML.
 * Uses Jackson to deserialize osi-salesforce-converter-config.yaml into PipelineConfig model.
 *
 */
public class PipelineConfigLoader {
    private static final String CONFIG_RESOURCE = "/osi-salesforce-converter-config.yaml";
    private final ObjectMapper yamlMapper;

    public PipelineConfigLoader() {
        YAMLFactory yamlFactory = new YAMLFactory();
        this.yamlMapper = new ObjectMapper(yamlFactory);
    }

    public static PipelineConfig loadFromResource() {
        PipelineConfigLoader loader = new PipelineConfigLoader();
        try (InputStream stream = PipelineConfigLoader.class.getResourceAsStream(CONFIG_RESOURCE)) {
            if (stream == null) {
                throw new ConversionException("Pipeline config not found: " + CONFIG_RESOURCE);
            }

            // Read full YAML into map
            Map<String, Object> rawConfig = loader.yamlMapper.readValue(stream, new TypeReference<>() {});

            // Parse into structured config
            PipelineConfig config = new PipelineConfig();
            config.setPipelines((Map<String, List<String>>) rawConfig.get("pipelines"));

            // Parse direction configs (osiToSalesforce, salesforceToOsi sections)
            Map<String, DirectionConfig> directionConfigs = new HashMap<>();
            for (String direction : config.getPipelines().keySet()) {
                if (rawConfig.containsKey(direction)) {
                    DirectionConfig dirConfig = loader.yamlMapper.convertValue(
                        rawConfig.get(direction),
                        DirectionConfig.class
                    );
                    directionConfigs.put(direction, dirConfig);
                }
            }
            config.setDirectionConfigs(directionConfigs);

            return config;
        } catch (IOException e) {
            throw new ConversionException("Failed to load pipeline config", e);
        }
    }
}
