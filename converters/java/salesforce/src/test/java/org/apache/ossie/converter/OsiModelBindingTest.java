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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import org.apache.ossie.model.DialectExpression;
import org.apache.ossie.model.OsiModel;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.Map;

class OsiModelBindingTest {

    private final ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());

    @Test
    void bindsBothSidesOfTheDynamicPipelineWithoutVendorSchema() throws Exception {
        String rootYaml = String.join("\n",
                "version: 0.2.0.dev0",
                "semantic_model:",
                "  - name: sales",
                "    datasets:",
                "      - name: orders",
                "        source: analytics.orders",
                "        fields:",
                "          - name: order_id",
                "            expression:",
                "              dialects:",
                "                - dialect: ANSI_SQL",
                "                  expression: order_id");
        Map<String, Object> rootMap = yamlMapper.readValue(
                rootYaml, new TypeReference<LinkedHashMap<String, Object>>() {});

        OsiModel typedRoot = OsiModelBinding.fromRootMap(yamlMapper, rootMap);
        assertEquals(DialectExpression.Dialect.ANSI_SQL, typedRoot.getSemanticModel().get(0)
                .getDatasets().get(0).getFields().get(0)
                .getExpression().getDialects().get(0).getDialect());

        Map<String, Object> pipelineMap = OsiModelBinding.toPipelineMap(
                yamlMapper, typedRoot.getSemanticModel().get(0));
        assertInstanceOf(Iterable.class, pipelineMap.get("datasets"));

        String pipelineYaml = yamlMapper.writeValueAsString(
                typedRoot.getSemanticModel().get(0));
        OsiModel wrapped = OsiModelBinding.wrapPipelineYaml(
                yamlMapper, pipelineYaml, ConverterConstants.OSI_VERSION);
        assertEquals("sales", wrapped.getSemanticModel().get(0).getName());
        assertEquals("0.2.0.dev0", wrapped.getVersion());
    }
}
