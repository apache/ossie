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

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.apache.ossie.converter.pipeline.PipelineStep;
import org.apache.ossie.mapper.FileBasedPropertyMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

class SalesforceFormulaEscapingTest {

    @ParameterizedTest
    @MethodSource("escapedFormulas")
    void decodesTheEntitiesSalesforceEmits(String stored, String original) {
        assertEquals(original, SalesforceFormulaEscaping.unescape(stored));
    }

    static Stream<Arguments> escapedFormulas() {
        return Stream.of(
                Arguments.of("SUM(IF ([Orders].[Segment] = &#39;SMB&#39;) THEN 1 ELSE 0 END)",
                        "SUM(IF ([Orders].[Segment] = 'SMB') THEN 1 ELSE 0 END)"),
                Arguments.of("SUM(IF ([Orders].[Sales] &lt; 100) THEN 1 ELSE 0 END)",
                        "SUM(IF ([Orders].[Sales] < 100) THEN 1 ELSE 0 END)"),
                Arguments.of("SUM(IF ([Orders].[Sales] &gt; 100) THEN 1 ELSE 0 END)",
                        "SUM(IF ([Orders].[Sales] > 100) THEN 1 ELSE 0 END)"),
                Arguments.of("CONTAINS([Orders].[Name], &quot;Inc&quot;)",
                        "CONTAINS([Orders].[Name], \"Inc\")"),
                Arguments.of("CONTAINS([Orders].[Name], &#39;A&amp;B&#39;)",
                        "CONTAINS([Orders].[Name], 'A&B')"));
    }

    @Test
    void decodesAmpersandLastSoEscapedEntityTextSurvives() {
        assertEquals("'&lt;'", SalesforceFormulaEscaping.unescape("&#39;&amp;lt;&#39;"));
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"", "SUM([Orders].[Sales])"})
    void leavesFormulasWithoutEntitiesUntouched(String formula) {
        assertEquals(formula, SalesforceFormulaEscaping.unescape(formula));
    }

    @Test
    void decodesImportedCalculatedMeasurementExpressions() {
        Map<String, Object> source = new LinkedHashMap<>(Map.of(
                "semanticCalculatedMeasurements", mutableList(Map.of(
                        "apiName", "smb_orders",
                        "expression", "SUM(IF ([Orders].[Segment] = &#39;A&amp;B&#39;) THEN 1 ELSE 0 END)",
                        "dataType", "Number"))));
        Map<String, Object> output = new LinkedHashMap<>();

        handler(new MetricMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE, extensions()), source, output);

        assertEquals("SUM(IF ([Orders].[Segment] = 'A&B') THEN 1 ELSE 0 END)",
                tableauExpression(firstOf(output, "metrics")));
    }

    @Test
    void decodesImportedCalculatedDimensionExpressions() {
        Map<String, Object> source = new LinkedHashMap<>(Map.of(
                "semanticDataObjects", mutableList(Map.of("apiName", "Orders")),
                "semanticCalculatedDimensions", mutableList(Map.of(
                        "apiName", "segment_label",
                        "expression", "IF ([Orders].[Segment] = &#39;A&amp;B&#39;) THEN &#39;yes&#39; ELSE &#39;no&#39; END",
                        "dataType", "Text",
                        "dependencies", List.of(Map.of("dependentDefinitionApiName", "Orders"))))));
        Map<String, Object> output = new LinkedHashMap<>(Map.of(
                "datasets", mutableList(Map.of("name", "Orders"))));

        handler(new FieldMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE, extensions()), source, output);

        Map<String, Object> dataset = firstOf(output, "datasets");
        assertEquals("IF ([Orders].[Segment] = 'A&B') THEN 'yes' ELSE 'no' END",
                tableauExpression(firstOf(dataset, "fields")));
    }

    @Test
    void decodesCalculatedDimensionsPreservedAsCustomExtensions() {
        // No dependencies, so the dimension cannot become a dataset field and is kept verbatim.
        Map<String, Object> source = new LinkedHashMap<>(Map.of(
                "semanticDataObjects", mutableList(Map.of("apiName", "Orders")),
                "semanticCalculatedDimensions", mutableList(Map.of(
                        "apiName", "segment_label",
                        "expression", "IF ([Orders].[Segment] = &#39;A&amp;B&#39;) THEN &#39;yes&#39; ELSE &#39;no&#39; END",
                        "dataType", "Text"))));
        Map<String, Object> output = new LinkedHashMap<>(Map.of(
                "datasets", mutableList(Map.of("name", "Orders"))));

        handler(new FieldMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE, extensions()), source, output);

        Map<String, Object> preserved = firstOf(source, "semanticCalculatedDimensions");
        assertEquals("IF ([Orders].[Segment] = 'A&B') THEN 'yes' ELSE 'no' END",
                preserved.get("expression"));
    }

    @Test
    void decodesFormulaJoinCriteriaPreservedAsCustomExtensions() {
        // A criterion whose join side is a Formula makes the relationship unsupported, so it
        // is kept verbatim in custom_extensions rather than becoming from_columns/to_columns.
        Map<String, Object> source = new LinkedHashMap<>(Map.of(
                "semanticRelationships", mutableList(Map.of(
                        "apiName", "Orders_Customers",
                        "leftSemanticDefinitionApiName", "Orders",
                        "rightSemanticDefinitionApiName", "Customers",
                        "criteria", mutableList(Map.of(
                                "joinOperator", "Equals",
                                "leftFieldType", "TableField",
                                "leftSemanticFieldApiName", "CustomerId",
                                "rightFieldType", "Formula",
                                "rightSemanticFieldApiName",
                                "LEFT(&#39;A&amp;B&#39; + [Customers].[Id], 4)"))))));
        Map<String, Object> output = new LinkedHashMap<>();

        handler(new RelationshipMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE, extensions()),
                source, output);

        assertTrue(customExtensionData(output).contains("LEFT('A&B' + [Customers].[Id], 4)"),
                "formula kept escaped in custom_extensions: " + customExtensionData(output));
    }

    @Test
    void leavesTableFieldJoinCriteriaUntouched() {
        // Both sides are table fields, so the criterion becomes from_columns/to_columns. A
        // field apiName carries no entities, so decoding must leave it exactly as it was.
        Map<String, Object> source = new LinkedHashMap<>(Map.of(
                "semanticRelationships", mutableList(Map.of(
                        "apiName", "Orders_Customers",
                        "leftSemanticDefinitionApiName", "Orders",
                        "rightSemanticDefinitionApiName", "Customers",
                        "criteria", mutableList(Map.of(
                                "joinOperator", "Equals",
                                "leftFieldType", "TableField",
                                "leftSemanticFieldApiName", "CustomerId",
                                "rightFieldType", "TableField",
                                "rightSemanticFieldApiName", "Id"))))));
        Map<String, Object> output = new LinkedHashMap<>();

        handler(new RelationshipMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE, extensions()),
                source, output);

        Map<String, Object> relationship = firstOf(output, "relationships");
        assertEquals(List.of("CustomerId"), relationship.get("from_columns"));
        assertEquals(List.of("Id"), relationship.get("to_columns"));
    }

    private static CustomExtensionHandler extensions() {
        return new CustomExtensionHandler(new ObjectMapper());
    }

    private static void handler(PipelineStep step, Map<String, Object> source, Map<String, Object> output) {
        step.execute(source, output, new LinkedHashMap<>(
                FileBasedPropertyMapper.fromResource("/mappings.yaml").getSalesforceToOssieMappings()));
    }

    private static List<Object> mutableList(Map<String, Object> item) {
        List<Object> list = new ArrayList<>();
        list.add(new LinkedHashMap<>(item));
        return list;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> firstOf(Map<String, Object> container, String key) {
        List<Object> items = (List<Object>) container.get(key);
        assertNotNull(items, key + " missing from " + container.keySet());
        assertEquals(1, items.size());
        return (Map<String, Object>) items.get(0);
    }

    @SuppressWarnings("unchecked")
    private static String tableauExpression(Map<String, Object> item) {
        Map<String, Object> expression = (Map<String, Object>) item.get("expression");
        assertNotNull(expression, "expression missing from " + item.keySet());
        List<Object> dialects = (List<Object>) expression.get("dialects");
        return (String) ((Map<String, Object>) dialects.get(0)).get("expression");
    }

    @SuppressWarnings("unchecked")
    private static String customExtensionData(Map<String, Object> container) {
        List<Object> extensions = (List<Object>) container.get("custom_extensions");
        assertNotNull(extensions, "custom_extensions missing from " + container.keySet());
        return (String) ((Map<String, Object>) extensions.get(0)).get("data");
    }
}
