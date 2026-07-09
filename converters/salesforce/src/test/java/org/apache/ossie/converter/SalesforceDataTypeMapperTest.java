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

import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

class SalesforceDataTypeMapperTest {

    @ParameterizedTest
    @MethodSource("salesforceToOssieTypes")
    void mapsSalesforceTypesToOssie(String salesforceType, String osiDatatype) {
        assertEquals(osiDatatype, SalesforceDataTypeMapper.toOssie(salesforceType));
    }

    static Stream<Arguments> salesforceToOssieTypes() {
        return Stream.of(
                Arguments.of("Text", "String"),
                Arguments.of("Email", "String"),
                Arguments.of("PhoneNumber", "String"),
                Arguments.of("Url", "String"),
                Arguments.of("Number", "Decimal"),
                Arguments.of("Currency", "Decimal"),
                Arguments.of("Percentage", "Decimal"),
                Arguments.of("Boolean", "Boolean"),
                Arguments.of("Date", "Date"),
                Arguments.of("DateTime", "DateTimeTz"),
                Arguments.of("Geo", "Opaque"),
                Arguments.of("Duration", "Opaque"),
                Arguments.of("FutureVendorType", "Opaque"));
    }

    @ParameterizedTest
    @MethodSource("ossieToSalesforceTypes")
    void mapsOssieTypesToSalesforce(String osiDatatype, String salesforceType) {
        assertEquals(salesforceType, SalesforceDataTypeMapper.toSalesforce(osiDatatype));
    }

    static Stream<Arguments> ossieToSalesforceTypes() {
        return Stream.of(
                Arguments.of("String", "Text"),
                Arguments.of("Integer", "Number"),
                Arguments.of("Decimal", "Number"),
                Arguments.of("Float", "Number"),
                Arguments.of("Boolean", "Boolean"),
                Arguments.of("Date", "Date"),
                Arguments.of("DateTime", "DateTime"),
                Arguments.of("DateTimeTz", "DateTime"));
    }

    @Test
    void omitsMissingAndUnrepresentableTypes() {
        assertNull(SalesforceDataTypeMapper.toOssie(null));
        assertNull(SalesforceDataTypeMapper.toOssie(" "));
        assertNull(SalesforceDataTypeMapper.toSalesforce(null));
        assertNull(SalesforceDataTypeMapper.toSalesforce("Time"));
        assertNull(SalesforceDataTypeMapper.toSalesforce("Opaque"));
        assertNull(SalesforceDataTypeMapper.toSalesforce("FutureOssieType"));
    }

    @Test
    void recognizesTemporalSalesforceTypes() {
        assertTrue(SalesforceDataTypeMapper.isTemporalSalesforceType("Date"));
        assertTrue(SalesforceDataTypeMapper.isTemporalSalesforceType("DateTime"));
        assertFalse(SalesforceDataTypeMapper.isTemporalSalesforceType("Text"));
        assertFalse(SalesforceDataTypeMapper.isTemporalSalesforceType(null));
    }

    @Test
    void treatsPortableCollapsesAsCompatibleWithExactSalesforceTypes() {
        assertTrue(SalesforceDataTypeMapper.areCompatible("String", "Email"));
        assertTrue(SalesforceDataTypeMapper.areCompatible("Decimal", "Currency"));
        assertTrue(SalesforceDataTypeMapper.areCompatible("Integer", "Number"));
        assertTrue(SalesforceDataTypeMapper.areCompatible("DateTimeTz", "DateTime"));
        assertTrue(SalesforceDataTypeMapper.areCompatible("Opaque", "Geo"));
        assertFalse(SalesforceDataTypeMapper.areCompatible("String", "Number"));
    }
}
