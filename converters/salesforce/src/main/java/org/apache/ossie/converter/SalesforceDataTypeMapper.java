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

import java.util.Map;
import java.util.Objects;

/** Maps Salesforce semantic data types to and from Ossie's portable logical types. */
final class SalesforceDataTypeMapper {

    private static final Map<String, String> SALESFORCE_TO_OSSIE = Map.ofEntries(
            Map.entry("Text", "String"),
            Map.entry("Email", "String"),
            Map.entry("PhoneNumber", "String"),
            Map.entry("Url", "String"),
            Map.entry("Number", "Decimal"),
            Map.entry("Currency", "Decimal"),
            Map.entry("Percentage", "Decimal"),
            Map.entry("Boolean", "Boolean"),
            Map.entry("Date", "Date"),
            Map.entry("DateTime", "DateTimeTz"),
            Map.entry("Geo", "Opaque"));

    private static final Map<String, String> OSSIE_TO_SALESFORCE = Map.ofEntries(
            Map.entry("String", "Text"),
            Map.entry("Integer", "Number"),
            Map.entry("Decimal", "Number"),
            Map.entry("Float", "Number"),
            Map.entry("Boolean", "Boolean"),
            Map.entry("Date", "Date"),
            Map.entry("DateTime", "DateTime"),
            Map.entry("DateTimeTz", "DateTime"));

    private SalesforceDataTypeMapper() {}

    /**
     * Maps a Salesforce type to an Ossie logical type. Unknown non-empty Salesforce
     * types are known vendor types outside the portable vocabulary and become Opaque.
     */
    static String toOssie(String salesforceDataType) {
        if (salesforceDataType == null || salesforceDataType.isBlank()) {
            return null;
        }
        return SALESFORCE_TO_OSSIE.getOrDefault(salesforceDataType, "Opaque");
    }

    /** Maps an Ossie logical type to Salesforce, or returns null when no safe mapping exists. */
    static String toSalesforce(String osiDatatype) {
        if (osiDatatype == null || osiDatatype.isBlank()) {
            return null;
        }
        return OSSIE_TO_SALESFORCE.get(osiDatatype);
    }

    static boolean isTemporalSalesforceType(String salesforceDataType) {
        return "Date".equals(salesforceDataType) || "DateTime".equals(salesforceDataType);
    }

    /**
     * Returns whether an exact Salesforce extension type and a portable Ossie type
     * describe compatible values. This treats lossy portable collapses such as
     * Email/String and Currency/Decimal as compatible.
     */
    static boolean areCompatible(String osiDatatype, String salesforceDataType) {
        if (osiDatatype == null || salesforceDataType == null) {
            return true;
        }
        return Objects.equals(toSalesforce(osiDatatype), salesforceDataType)
                || Objects.equals(toOssie(salesforceDataType), osiDatatype);
    }
}
