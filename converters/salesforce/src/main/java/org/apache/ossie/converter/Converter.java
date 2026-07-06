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

import java.nio.file.Path;
import java.util.List;

/**
 * Interface for converting between data formats (YAML and JSON).
 *
 * <p>This is the external API for conversion. Implementations handle the conversion
 * of data from one format to another, with support for property mapping.</p>
 *
 */
public interface Converter {

    /**
     * Converts the input file and writes results to the specified output directory.
     *
     * <p>Each semantic model is written to a separate file named after its apiName.
     * Example: "Sales_Model.json", "Marketing_Model.json"
     *
     * @param inputPath the path to the input file
     * @param outputDir the directory where output files will be written
     */
    void convert(Path inputPath, Path outputDir);

    /**
     * Converts string content from the source format to the target format.
     *
     * <p>For Ossie to Salesforce: returns one Salesforce model per Ossie semantic_model entry.
     * <p>For Salesforce to Ossie: returns one Ossie document with one semantic_model entry.
     *
     * @param content the content to convert
     * @return list of converted content strings (one per semantic model)
     */
    List<String> convert(String content);
}
