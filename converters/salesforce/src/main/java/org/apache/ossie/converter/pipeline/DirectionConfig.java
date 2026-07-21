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

import static org.apache.ossie.converter.ConverterConstants.*;

/**
 * Configuration for a specific conversion direction.
 *
 */
public class DirectionConfig {
    private String inputFormat;
    private String outputFormat;
    private String schemaPath;
    private String extractModelNameFrom;

    public String getInputFormat() {
        return inputFormat;
    }

    public void setInputFormat(String inputFormat) {
        this.inputFormat = inputFormat;
    }

    public String getOutputFormat() {
        return outputFormat;
    }

    public void setOutputFormat(String outputFormat) {
        this.outputFormat = outputFormat;
    }

    public String getSchemaPath() {
        return schemaPath;
    }

    public void setSchemaPath(String schemaPath) {
        this.schemaPath = schemaPath;
    }

    public String getExtractModelNameFrom() {
        return extractModelNameFrom;
    }

    public void setExtractModelNameFrom(String extractModelNameFrom) {
        this.extractModelNameFrom = extractModelNameFrom;
    }

    /**
     * Get file extension based on output format.
     * @return JSON_EXTENSION for json format, YAML_EXTENSION for yaml format
     */
    public String getFileExtension() {
        return JSON.equals(outputFormat)
            ? JSON_EXTENSION
            : YAML_EXTENSION;
    }
}
