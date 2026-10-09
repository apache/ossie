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

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.TextNode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class IcebergTypeMapperTest {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @ParameterizedTest
    @MethodSource("icebergToOssieMappings")
    void testIcebergToOssieMappings(JsonNode icebergType, String datatype) {
        assertEquals(datatype, IcebergTypeMapper.toOssieDatatype(icebergType));
    }

    static Stream<Arguments> icebergToOssieMappings() throws Exception {
        return Stream.of(
                Arguments.of(TextNode.valueOf("boolean"), "Boolean"),
                Arguments.of(TextNode.valueOf("int"), "Integer"),
                Arguments.of(TextNode.valueOf("long"), "Integer"),
                Arguments.of(TextNode.valueOf("float"), "Float"),
                Arguments.of(TextNode.valueOf("double"), "Float"),
                Arguments.of(TextNode.valueOf("decimal(18,2)"), "Decimal"),
                Arguments.of(TextNode.valueOf("date"), "Date"),
                Arguments.of(TextNode.valueOf("time"), "Time"),
                Arguments.of(TextNode.valueOf("timestamp"), "DateTime"),
                Arguments.of(TextNode.valueOf("timestamp_ns"), "DateTime"),
                Arguments.of(TextNode.valueOf("timestamptz"), "DateTimeTz"),
                Arguments.of(TextNode.valueOf("timestamptz_ns"), "DateTimeTz"),
                Arguments.of(TextNode.valueOf("string"), "String"),
                Arguments.of(TextNode.valueOf("uuid"), "Opaque"),
                Arguments.of(TextNode.valueOf("fixed[16]"), "Opaque"),
                Arguments.of(TextNode.valueOf("binary"), "Opaque"),
                Arguments.of(TextNode.valueOf("variant"), "Opaque"),
                Arguments.of(TextNode.valueOf("geometry(srid:4326)"), "Opaque"),
                Arguments.of(TextNode.valueOf("geography(srid:4326, spherical)"), "Opaque"),
                Arguments.of(OBJECT_MAPPER.readTree(
                        "{\"type\":\"list\",\"element-id\":2,\"element\":\"string\","
                                + "\"element-required\":false}"), "Opaque"),
                Arguments.of(OBJECT_MAPPER.readTree(
                        "{\"type\":\"map\",\"key-id\":2,\"key\":\"string\","
                                + "\"value-id\":3,\"value\":\"long\",\"value-required\":false}"),
                        "Opaque"),
                Arguments.of(OBJECT_MAPPER.readTree("{\"type\":\"struct\",\"fields\":[]}"), "Opaque"));
    }

    @Test
    void testIcebergUnknownOmitsDatatype() {
        assertNull(IcebergTypeMapper.toOssieDatatype(TextNode.valueOf("unknown")));
        assertNull(IcebergTypeMapper.toOssieDatatype(null));
    }

    @ParameterizedTest
    @MethodSource("ossieToIcebergMappings")
    void testOssieToIcebergMappings(String datatype, String icebergType) {
        assertEquals(icebergType, IcebergTypeMapper.toDefaultIcebergType(datatype).asText());
    }

    static Stream<Arguments> ossieToIcebergMappings() {
        return Stream.of(
                Arguments.of("String", "string"),
                Arguments.of("Integer", "long"),
                Arguments.of("Decimal", "decimal(18, 2)"),
                Arguments.of("Float", "double"),
                Arguments.of("Boolean", "boolean"),
                Arguments.of("Date", "date"),
                Arguments.of("Time", "time"),
                Arguments.of("DateTime", "timestamp"),
                Arguments.of("DateTimeTz", "timestamptz"));
    }

    @Test
    void testOpaqueAndUnknownHaveNoDefaultIcebergType() {
        assertNull(IcebergTypeMapper.toDefaultIcebergType("Opaque"));
        assertNull(IcebergTypeMapper.toDefaultIcebergType("Geography"));
        assertNull(IcebergTypeMapper.toDefaultIcebergType(null));
    }

    @Test
    void testNestedTypeIdsAreReassignedWithoutMutatingSource() throws Exception {
        JsonNode source = OBJECT_MAPPER.readTree(
                "{\"type\":\"struct\",\"fields\":["
                        + "{\"id\":8,\"name\":\"tags\",\"required\":false,"
                        + "\"type\":{\"type\":\"list\",\"element-id\":9,"
                        + "\"element\":\"string\",\"element-required\":false}},"
                        + "{\"id\":10,\"name\":\"properties\",\"required\":false,"
                        + "\"type\":{\"type\":\"map\",\"key-id\":11,\"key\":\"string\","
                        + "\"value-id\":12,\"value\":\"long\",\"value-required\":false}}]}"
        );

        JsonNode exported = IcebergTypeMapper.prepareExactTypeForExport(source, new AtomicInteger(4));

        assertEquals(4, exported.path("fields").get(0).path("id").asInt());
        assertEquals(5, exported.path("fields").get(0).path("type").path("element-id").asInt());
        assertEquals(6, exported.path("fields").get(1).path("id").asInt());
        assertEquals(7, exported.path("fields").get(1).path("type").path("key-id").asInt());
        assertEquals(8, exported.path("fields").get(1).path("type").path("value-id").asInt());
        assertEquals(8, source.path("fields").get(0).path("id").asInt());
        assertEquals(9, source.path("fields").get(0).path("type").path("element-id").asInt());
    }

    @Test
    void testLegacyObjectDecimalIsCanonicalizedForExport() throws Exception {
        JsonNode source = OBJECT_MAPPER.readTree(
                "{\"type\":\"decimal\",\"precision\":20,\"scale\":4}");

        JsonNode exported = IcebergTypeMapper.prepareExactTypeForExport(source, new AtomicInteger(1));

        assertEquals("decimal(20, 4)", exported.asText());
    }
}
