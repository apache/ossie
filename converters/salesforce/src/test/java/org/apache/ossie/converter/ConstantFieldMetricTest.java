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

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.ossie.exception.ConversionException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class ConstantFieldMetricTest {
    private static final ObjectMapper YAML = new ObjectMapper(new YAMLFactory());
    private static final ObjectMapper JSON = new ObjectMapper();

    @ParameterizedTest
    @ValueSource(strings = {"ANSI_SQL", "SNOWFLAKE", "TABLEAU"})
    void rejectsSummingConstantRowFieldsWhoseDatasetWouldDisappear(String dialect) throws Exception {
        Map<String, Object> document = fixture();
        Map<String, Object> model = model(document);
        for (String dataset : List.of("Orders", "Products")) {
            addField(model, dataset, field("one_per_row", dialect, "1"));
        }
        // Each individual metric needs a different row population; SUM(1) cannot express this.
        for (String dataset : List.of("Orders", "Products")) {
            String reference = dialect.equals("TABLEAU") ? "[" + dataset + "].[one_per_row]" : dataset + ".one_per_row";
            model.put("metrics", List.of(metric("row_population", dialect, "SUM(" + reference + ")")));
            ConversionException error = assertThrows(ConversionException.class, () -> convert(document));
            assertTrue(error.getMessage().contains("Metric 'row_population'"), error.getMessage());
            assertTrue(error.getMessage().contains(dataset + ".one_per_row"), error.getMessage());
            assertTrue(error.getMessage().contains("without a physical dataset anchor"), error.getMessage());
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"COUNT", "COUNTD", "MIN", "MAX", "AVG"})
    void doesNotAllowOtherAggregatesToBypassConstantFieldScopeChecks(String function) throws Exception {
        Map<String, Object> document = fixture();
        Map<String, Object> model = model(document);
        addField(model, "Orders", field("one_per_row", "TABLEAU", "1 + 0"));
        model.put("metrics", List.of(metric("count_rows", "TABLEAU", function + "([Orders].[one_per_row])")));
        ConversionException error = assertThrows(ConversionException.class, () -> convert(document));
        assertTrue(error.getMessage().contains("without a physical dataset anchor"), error.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {"ANSI_SQL", "SNOWFLAKE", "TABLEAU"})
    void stillExportsStandaloneConstantFieldsAndConstantMetrics(String dialect) throws Exception {
        Map<String, Object> document = fixture();
        Map<String, Object> model = model(document);
        addField(model, "Orders", field("constant", dialect, "1 + 1"));
        addField(model, "Products", field("constant", dialect, "2"));
        model.put("metrics", List.of(metric("fixed_value", dialect, "42")));
        Map<String, Object> output = convert(document);
        assertEquals("42", items(output, "semanticCalculatedMeasurements").get(0).get("expression"));
        assertEquals(2, items(output, "semanticCalculatedDimensions").stream()
                .filter(item -> List.of("Orders__constant", "Products__constant").contains(item.get("apiName"))).count());
    }

    @Test
    void derivedRowExpressionCanCombineAConstantFieldWithAPhysicalField() throws Exception {
        Map<String, Object> document = fixture();
        Map<String, Object> model = model(document);
        addField(model, "Orders", field("constant_one", "ANSI_SQL", "1"));
        Map<String, Object> amountPlusOne = field("amount_plus_one", "ANSI_SQL", "amount + constant_one");
        amountPlusOne.put("datatype", "Decimal");
        addField(model, "Orders", amountPlusOne);
        model.put("metrics", List.of(metric("adjusted_total", "ANSI_SQL", "SUM(Orders.amount_plus_one)")));
        String expression = items(convert(document), "semanticCalculatedMeasurements").get(0).get("expression").toString();
        assertTrue(expression.contains("[Orders].[amount]"), expression);
        assertTrue(expression.contains("+ 1"), expression);
    }

    @Test
    void constantDependenciesCannotSmuggleDatasetScopeThroughAnAlias() throws Exception {
        Map<String, Object> document = fixture();
        Map<String, Object> model = model(document);
        addField(model, "Orders", field("constant_one", "ANSI_SQL", "1"));
        addField(model, "Orders", field("constant_alias", "TABLEAU", "[Orders].[constant_one] + 0"));
        model.put("metrics", List.of(metric("total", "ANSI_SQL", "SUM(Orders.constant_alias)")));
        ConversionException error = assertThrows(ConversionException.class, () -> convert(document));
        assertTrue(error.getMessage().contains("without a physical dataset anchor"), error.getMessage());
    }

    private static Map<String, Object> field(String name, String dialect, String expression) {
        return new LinkedHashMap<>(Map.of("name", name, "datatype", "Integer", "expression",
                Map.of("dialects", List.of(Map.of("dialect", dialect, "expression", expression)))));
    }

    private static Map<String, Object> metric(String name, String dialect, String expression) {
        Map<String, Object> metric = field(name, dialect, expression);
        metric.put("datatype", "Decimal");
        return metric;
    }

    private static void addField(Map<String, Object> model, String datasetName, Map<String, Object> field) {
        Map<String, Object> dataset = items(model, "datasets").stream()
                .filter(item -> datasetName.equals(item.get("name"))).findFirst().orElseThrow();
        List<Map<String, Object>> fields = new ArrayList<>(items(dataset, "fields"));
        fields.add(field);
        dataset.put("fields", fields);
    }

    private static Map<String, Object> fixture() throws Exception {
        return YAML.readValue(Files.readString(Path.of("src/test/resources/examples/ossieToSalesforce.yaml")), new TypeReference<>() {});
    }

    private static Map<String, Object> model(Map<String, Object> document) {
        return items(document, "semantic_model").get(0);
    }

    private static Map<String, Object> convert(Map<String, Object> document) throws Exception {
        String output = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE)
                .convert(YAML.writeValueAsString(document)).get(0);
        return JSON.readValue(output, new TypeReference<>() {});
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> items(Map<String, Object> map, String key) {
        return (List<Map<String, Object>>) map.getOrDefault(key, List.of());
    }
}
