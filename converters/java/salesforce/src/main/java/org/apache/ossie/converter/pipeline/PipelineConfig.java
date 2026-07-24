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

import java.util.List;
import java.util.Map;

/**
 * Root configuration model matching pipeline-config.yaml structure.
 *
 */
public class PipelineConfig {
    private Map<String, List<String>> pipelines;           // Direction -> handler names
    private Map<String, DirectionConfig> directionConfigs; // Direction -> config

    public Map<String, List<String>> getPipelines() {
        return pipelines;
    }

    public void setPipelines(Map<String, List<String>> pipelines) {
        this.pipelines = pipelines;
    }

    public Map<String, DirectionConfig> getDirectionConfigs() {
        return directionConfigs;
    }

    public void setDirectionConfigs(Map<String, DirectionConfig> directionConfigs) {
        this.directionConfigs = directionConfigs;
    }
}
