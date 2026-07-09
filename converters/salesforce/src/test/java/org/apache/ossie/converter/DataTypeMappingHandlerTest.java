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

import static org.apache.ossie.converter.ConverterConstants.*;
import static org.apache.ossie.util.DataStructureUtils.*;
import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class DataTypeMappingHandlerTest {

    private ObjectMapper jsonMapper;
    private CustomExtensionHandler customExtensionHandler;

    @BeforeEach
    void setUp() {
        jsonMapper = new ObjectMapper();
        customExtensionHandler = new CustomExtensionHandler(jsonMapper);
    }

    @Test
    void importsFieldAndCalculatedDimensionDatatypesWithoutSchemaValidation() throws Exception {
        Map<String, Object> sourceData = new LinkedHashMap<>();
        sourceData.put(SEMANTIC_DATA_OBJECTS, List.of(Map.of(
                API_NAME, "Orders",
                SEMANTIC_DIMENSIONS, List.of(
                        salesforceField("email", "email", "Email"),
                        salesforceField("created_at", "created_at", "DateTime"),
                        salesforceField("location", "location", "Geo"),
                        salesforceField("untyped", "untyped", null)),
                SEMANTIC_MEASUREMENTS, List.of(
                        salesforceField("amount", "amount", "Currency")))));
        sourceData.put(SEMANTIC_CALCULATED_DIMENSIONS, List.of(Map.of(
                API_NAME, "order_date",
                EXPRESSION, "DATE([Orders].[created_at])",
                DATA_TYPE, "Date",
                DEPENDENCIES, List.of(Map.of(DEPENDENT_DEFINITION_API_NAME, "Orders")))));

        Map<String, Object> outputData = new LinkedHashMap<>();
        outputData.put(DATASETS, new ArrayList<>(List.of(new LinkedHashMap<>(Map.of(NAME, "Orders")))));

        new FieldMappingHandler(ConversionDirection.SALESFORCE_TO_OSI, customExtensionHandler)
                .execute(sourceData, outputData, new LinkedHashMap<>());

        Map<String, Object> dataset = asMap(getList(outputData, DATASETS).get(0));
        List<Object> fields = getList(dataset, FIELDS);

        assertField(fields, "email", "String", false);
        assertField(fields, "created_at", "DateTimeTz", true);
        assertField(fields, "location", "Opaque", false);
        assertField(fields, "amount", "Decimal", null);
        assertField(fields, "order_date", "Date", true);
        assertFalse(findItemById(fields, NAME, "untyped").containsKey(OSI_DATATYPE));

        Map<String, Object> emailExtension = getSalesforceExtension(findItemById(fields, NAME, "email"));
        assertEquals("Email", emailExtension.get(DATA_TYPE));
        Map<String, Object> amountExtension = getSalesforceExtension(findItemById(fields, NAME, "amount"));
        assertEquals("Currency", amountExtension.get(DATA_TYPE));
    }

    @Test
    void exportsPortableDatatypesWithoutChangingFieldRouting() {
        List<Object> fields = new ArrayList<>();
        fields.add(osiField("customer_id", "customer_id", "Integer", true, null));
        fields.add(osiField("amount", "amount", "Decimal", false, null));
        fields.add(osiField("event_time", "event_time", "DateTimeTz", false, null));
        fields.add(osiField("clock_time", "clock_time", "Time", true, null));
        fields.add(osiField("email", "email", "String", true, "Email"));
        fields.add(osiField("location", "location", "Opaque", true, "Geo"));
        fields.add(osiField("payload", "payload", "Opaque", false, null));
        fields.add(osiField("conflict", "conflict", "String", true, "Number"));
        fields.add(osiField("order_year", "YEAR([Orders].[order_date])", "Integer", true, null));

        Map<String, Object> sourceData = new LinkedHashMap<>();
        sourceData.put(DATASETS, List.of(Map.of(NAME, "Orders", FIELDS, fields)));

        Map<String, Object> salesforceDataObject = new LinkedHashMap<>();
        salesforceDataObject.put(API_NAME, "Orders");
        Map<String, Object> outputData = new LinkedHashMap<>();
        outputData.put(SEMANTIC_DATA_OBJECTS, new ArrayList<>(List.of(salesforceDataObject)));

        new FieldMappingHandler(ConversionDirection.OSI_TO_SALESFORCE, customExtensionHandler)
                .execute(sourceData, outputData, new LinkedHashMap<>());

        List<Object> dimensions = getList(salesforceDataObject, SEMANTIC_DIMENSIONS);
        List<Object> measurements = getList(salesforceDataObject, SEMANTIC_MEASUREMENTS);
        List<Object> calculatedDimensions = getList(outputData, SEMANTIC_CALCULATED_DIMENSIONS);

        assertEquals("Number", findItemById(dimensions, API_NAME, "customer_id").get(DATA_TYPE));
        assertEquals("Number", findItemById(measurements, API_NAME, "amount").get(DATA_TYPE));
        assertEquals("DateTime", findItemById(measurements, API_NAME, "event_time").get(DATA_TYPE));
        assertFalse(findItemById(dimensions, API_NAME, "clock_time").containsKey(DATA_TYPE));
        assertEquals("Email", findItemById(dimensions, API_NAME, "email").get(DATA_TYPE));
        assertEquals("Geo", findItemById(dimensions, API_NAME, "location").get(DATA_TYPE));
        assertFalse(findItemById(measurements, API_NAME, "payload").containsKey(DATA_TYPE));
        assertEquals("Number", findItemById(dimensions, API_NAME, "conflict").get(DATA_TYPE));
        assertEquals("Number", findItemById(calculatedDimensions, API_NAME, "order_year").get(DATA_TYPE));

        assertNotNull(findItemById(measurements, API_NAME, "event_time"),
                "A temporal datatype without a dimension block must remain a measurement");
        assertNotNull(findItemById(dimensions, API_NAME, "clock_time"),
                "A dimension with an unsupported datatype must remain a dimension");
    }

    @Test
    void importsCalculatedMeasurementDatatypeWithoutAddingMetricExport() throws Exception {
        Map<String, Object> sourceData = new LinkedHashMap<>();
        sourceData.put(SEMANTIC_CALCULATED_MEASUREMENTS, List.of(Map.of(
                API_NAME, "revenue",
                DESCRIPTION, "Total revenue",
                EXPRESSION, "SUM([Orders].[amount])",
                DATA_TYPE, "Currency")));
        Map<String, Object> outputData = new LinkedHashMap<>();
        Map<String, String> mappings = new LinkedHashMap<>();
        mappings.put(SEMANTIC_CALCULATED_MEASUREMENTS, METRICS);
        mappings.put(SEMANTIC_CALCULATED_MEASUREMENTS + "." + API_NAME, METRICS + "." + NAME);
        mappings.put(SEMANTIC_CALCULATED_MEASUREMENTS + "." + DESCRIPTION, METRICS + "." + DESCRIPTION);

        new MetricMappingHandler(ConversionDirection.SALESFORCE_TO_OSI, customExtensionHandler)
                .execute(sourceData, outputData, mappings);

        Map<String, Object> metric = asMap(getList(outputData, METRICS).get(0));
        assertEquals("Decimal", metric.get(OSI_DATATYPE));
        assertEquals("Currency", getSalesforceExtension(metric).get(DATA_TYPE));

        Map<String, Object> osiSource = new LinkedHashMap<>();
        osiSource.put(METRICS, List.of(metric));
        Map<String, Object> salesforceOutput = new LinkedHashMap<>();
        new MetricMappingHandler(ConversionDirection.OSI_TO_SALESFORCE, customExtensionHandler)
                .execute(osiSource, salesforceOutput, new LinkedHashMap<>());
        assertFalse(salesforceOutput.containsKey(SEMANTIC_CALCULATED_MEASUREMENTS));
    }

    private Map<String, Object> salesforceField(String name, String expression, String dataType) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put(API_NAME, name);
        field.put(DATA_OBJECT_FIELD_NAME, expression);
        if (dataType != null) {
            field.put(DATA_TYPE, dataType);
        }
        return field;
    }

    private Map<String, Object> osiField(
            String name, String expression, String datatype, boolean dimension, String exactDataType) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put(NAME, name);
        field.put(OSI_DATATYPE, datatype);
        if (dimension) {
            field.put(DIMENSION, new LinkedHashMap<>());
        }
        field.put(EXPRESSION, Map.of(DIALECTS, List.of(Map.of(
                DIALECT, DIALECT_TABLEAU,
                EXPRESSION, expression))));
        if (exactDataType != null) {
            customExtensionHandler.addCustomExtension(field, Map.of(DATA_TYPE, exactDataType));
        }
        return field;
    }

    private void assertField(List<Object> fields, String name, String datatype, Boolean isTime) {
        Map<String, Object> field = findItemById(fields, NAME, name);
        assertNotNull(field);
        assertEquals(datatype, field.get(OSI_DATATYPE));
        if (isTime == null) {
            assertFalse(field.containsKey(DIMENSION));
        } else {
            assertEquals(isTime, getMap(field, DIMENSION).get(IS_TIME));
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> getSalesforceExtension(Map<String, Object> item) throws Exception {
        List<Object> extensions = getList(item, CUSTOM_EXTENSIONS);
        Map<String, Object> extension = streamMaps(extensions)
                .filter(value -> VENDOR_NAME_VALUE.equals(value.get(VENDOR_NAME)))
                .findFirst()
                .orElseThrow();
        return jsonMapper.readValue((String) extension.get(DATA), Map.class);
    }
}
