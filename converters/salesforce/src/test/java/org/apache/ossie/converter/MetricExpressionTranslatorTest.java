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

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.apache.ossie.exception.ConversionException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;

class MetricExpressionTranslatorTest {
    private static final Map<String, String> TYPES = Map.of(
            "amount", "Decimal", "profit", "Decimal", "revenue", "Decimal", "discount", "Decimal",
            "quantity", "Integer", "status", "String", "active", "Boolean", "ordered", "Date");

    private static Map<String, Object> source() {
        return Map.of("datasets", List.of(Map.of("name", "orders", "fields", TYPES.entrySet().stream()
                .map(entry -> Map.of("name", entry.getKey(), "datatype", entry.getValue())).toList())));
    }

    private static Map<String, Object> target() {
        return Map.of("semanticDataObjects", List.of(Map.of("apiName", "orders", "semanticMeasurements",
                TYPES.entrySet().stream().map(entry -> Map.of("apiName", entry.getKey(), "dataType",
                        SalesforceDataTypeMapper.toSalesforce(entry.getValue()))).toList())));
    }

    private static Map<String, Object> metric(String dialect, String expression) {
        return new LinkedHashMap<>(Map.of("name", "net_value", "expression", Map.of("dialects",
                List.of(Map.of("dialect", dialect, "expression", expression)))));
    }

    private static String translate(String dialect, String expression) {
        var result = MetricExpressionTranslator.translate(metric(dialect, expression), source(), target());
        assertEquals("Number", result.dataType());
        return result.expression();
    }

    static Stream<Arguments> supported() {
        return Stream.of(
                Arguments.of("SUM(orders.amount)", "SUM([orders].[amount])"),
                Arguments.of("AVG(amount)", "AVG([orders].[amount])"),
                Arguments.of("MIN(ORDERS.amount)", "MIN([orders].[amount])"),
                Arguments.of("MAX(orders.amount)", "MAX([orders].[amount])"),
                Arguments.of("COUNT(orders.status)", "COUNT([orders].[status])"),
                Arguments.of("COUNT(DISTINCT orders.status)", "COUNTD([orders].[status])"),
                Arguments.of("SUM(orders.amount * (1 - orders.discount))",
                        "SUM(([orders].[amount] * (1 - [orders].[discount])))"),
                Arguments.of("SUM(orders.profit) / NULLIF(SUM(orders.revenue), 0)",
                        "(SUM([orders].[profit]) / (IF (SUM([orders].[revenue]) = 0) THEN NULL ELSE SUM([orders].[revenue]) END))"),
                Arguments.of("SUM(CASE WHEN orders.status = 'paid' THEN orders.amount ELSE 0 END)",
                        "SUM((IF ([orders].[status] = 'paid') THEN [orders].[amount] ELSE 0 END))"),
                Arguments.of("COALESCE(SUM(orders.amount), AVG(orders.revenue), 0)",
                        "IFNULL(SUM([orders].[amount]), IFNULL(AVG([orders].[revenue]), 0))"),
                Arguments.of("SUM(CASE WHEN orders.amount IS NOT NULL THEN orders.amount END)",
                        "SUM((IF (NOT ISNULL([orders].[amount])) THEN [orders].[amount] ELSE NULL END))"),
                Arguments.of("ROUND(AVG(ABS(orders.amount)), 2)", "ROUND(AVG(ABS([orders].[amount])), 2)"),
                Arguments.of("SUM(CEIL(orders.amount) - FLOOR(orders.amount))",
                        "SUM((CEILING([orders].[amount]) - FLOOR([orders].[amount])))"),
                Arguments.of("1 + 2 * 3 - 4 / 2", "((1 + (2 * 3)) - (4 / 2))"),
                Arguments.of(".25 + 1e2", "(0.25 + 100)"),
                Arguments.of("SUM(orders.quantity) + -2", "(SUM([orders].[quantity]) + (-2))"),
                Arguments.of("CASE WHEN MAX(orders.status) = 'z' THEN 1 ELSE 0 END",
                        "(IF (MAX([orders].[status]) = 'z') THEN 1 ELSE 0 END)")
        );
    }

    @ParameterizedTest
    @MethodSource("supported")
    void compilesComposedSqlInBothDialects(String expression, String expected) {
        assertEquals(expected, translate("SNOWFLAKE", expression));
        assertEquals(expected, translate("ANSI_SQL", expression));
        // Every generated expression can be read through the bounded TABLEAU path.
        assertEquals(expected, translate("TABLEAU", expected));
    }

    @Test
    void notUsesSqlPrecedenceAndPreservesThreeValuedPredicates() {
        assertEquals("SUM((IF ((NOT ([orders].[amount] = 0)) OR ([orders].[active] AND ISNULL([orders].[discount])))"
                        + " THEN 1 ELSE 0 END))",
                translate("SNOWFLAKE", "SUM(CASE WHEN NOT orders.amount = 0 OR orders.active AND orders.discount IS NULL THEN 1 ELSE 0 END)"));
    }

    @Test
    void nestedCasesAndElseifRetainBranchOrder() {
        String sql = "SUM(CASE WHEN orders.active THEN CASE WHEN orders.amount > 0 THEN 2 ELSE 3 END WHEN orders.status = 'paid' THEN 4 END)";
        String expected = "SUM((IF [orders].[active] THEN (IF ([orders].[amount] > 0) THEN 2 ELSE 3 END)"
                + " ELSEIF ([orders].[status] = 'paid') THEN 4 ELSE NULL END))";
        assertEquals(expected, translate("SNOWFLAKE", sql));
        assertEquals(expected, translate("TABLEAU", expected));
    }

    @Test
    void identifiersAreBoundToSemanticNamesAndStringContentsAreNotReferences() {
        assertEquals("SUM([orders].[amount])", translate("SNOWFLAKE", "SUM(\"ORDERS\".\"AMOUNT\")"));
        assertEquals("SUM([orders].[amount])", translate("ANSI_SQL", "SUM([orders].[amount])"));
        assertEquals("SUM((IF ([orders].[status] = 'missing.field O''Brien') THEN 1 ELSE 0 END))",
                translate("SNOWFLAKE", "SUM(CASE WHEN orders.status = 'missing.field O''Brien' THEN 1 ELSE 0 END)"));
        assertEquals("SUM((IF ([orders].[status] = 'paid') THEN 1 ELSE 0 END))",
                translate("TABLEAU", "SUM(IF [orders].[status] = \"paid\" THEN 1 ELSE 0 END)"));
    }

    static Stream<Arguments> unsupported() {
        return Stream.of(
                Arguments.of("SUM(orders.missing)", "Unknown field"),
                Arguments.of("SUM(missing.amount)", "Unknown dataset"),
                Arguments.of("SUM(db.orders.amount)", "physical source paths"),
                Arguments.of("SUM(orders.amount__c)", "Unknown field"),
                Arguments.of("SUM(orders.amount) + orders.amount", "mix aggregate"),
                Arguments.of("CASE WHEN orders.active THEN SUM(orders.amount) ELSE 0 END", "mix aggregate"),
                Arguments.of("SUM(AVG(orders.amount))", "nested aggregate"),
                Arguments.of("SUM(orders.status)", "numeric"),
                Arguments.of("AVG(orders.active)", "numeric"),
                Arguments.of("SUM(CASE WHEN orders.amount THEN 1 ELSE 0 END)", "BOOLEAN"),
                Arguments.of("SUM(CASE WHEN orders.active THEN orders.amount ELSE 'zero' END)", "incompatible types"),
                Arguments.of("COALESCE(SUM(orders.amount), '0')", "incompatible types"),
                Arguments.of("NULLIF(SUM(orders.amount), '0')", "incompatible types"),
                Arguments.of("SUM(orders.amount) AND TRUE", "BOOLEAN"),
                Arguments.of("SUM(CASE WHEN orders.active > TRUE THEN 1 ELSE 0 END)", "ordered comparison"),
                Arguments.of("orders.amount + 1", "unaggregated"),
                Arguments.of("MAX(orders.status)", "must be numeric"),
                Arguments.of("TRUE", "must be numeric"),
                Arguments.of("COUNT(*)", "COUNT(*)"),
                Arguments.of("COUNT(1)", "declared field"),
                Arguments.of("COUNT(orders.quantity + 1)", "counting expressions"),
                Arguments.of("SUM(1)", "establish its dataset"),
                Arguments.of("SUM(DISTINCT orders.amount)", "DISTINCT"),
                Arguments.of("COUNT(DISTINCT orders.amount, orders.quantity)", "expects 1"),
                Arguments.of("ROUND(SUM(orders.amount), 2, 'HALF_TO_EVEN')", "expects 1 to 2"),
                Arguments.of("ROUND(SUM(orders.amount), .5)", "integer literal"),
                Arguments.of("ROUND(SUM(orders.amount), orders.quantity)", "integer literal"),
                Arguments.of("CEIL(SUM(orders.amount), 2)", "expects 1"),
                Arguments.of("FLOOR(SUM(orders.amount), 2)", "expects 1"),
                Arguments.of("ABS()", "expects 1"),
                Arguments.of("COALESCE(SUM(orders.amount))", "expects 2"),
                Arguments.of("NULLIF(SUM(orders.amount))", "expects 2"),
                Arguments.of("MEDIAN(orders.amount)", "unsupported function"),
                Arguments.of("CAST(orders.amount AS DECIMAL)", "expected )"),
                Arguments.of("SUM(YEAR(orders.ordered))", "unsupported function"),
                Arguments.of("SUM(LENGTH(orders.status))", "unsupported function"),
                Arguments.of("SUM(orders.amount) OVER ()", "unexpected token"),
                Arguments.of("SUM(orders.amount) FILTER (WHERE orders.active)", "unexpected token"),
                Arguments.of("{ FIXED : SUM(orders.amount) }", "unsupported character"),
                Arguments.of("SELECT SUM(orders.amount)", "Unknown field"),
                Arguments.of("SUM(orders.amount);", "unsupported character"),
                Arguments.of("SUM(orders.amount) -- comment", "comments"),
                Arguments.of("SUM(orders.amount) /* comment */", "comments"),
                Arguments.of("SUM(orders.amount", "expected )"),
                Arguments.of("SUM(orders.amount) trailing", "unexpected token"),
                Arguments.of("1.2.3", "invalid numeric"),
                Arguments.of("1e", "invalid numeric"),
                Arguments.of("1e1000000000", "too large"),
                Arguments.of("SUM(CASE WHEN orders.status = 'unterminated THEN 1 END)", "unterminated"),
                Arguments.of("SUM(orders.amount) % 2", "unsupported character"),
                Arguments.of("SUM(orders.amount) ^ 2", "unsupported character"),
                Arguments.of("SUM(orders.amount) || 'x'", "unsupported character"),
                Arguments.of("SUM(orders.amount) / 0", "division by literal zero"),
                Arguments.of("net_value + 1", "Unknown field")
        );
    }

    @ParameterizedTest
    @MethodSource("unsupported")
    void rejectsWithMetricNameAndActionableReason(String expression, String reason) {
        ConversionException exception = assertThrows(ConversionException.class,
                () -> translate("SNOWFLAKE", expression));
        assertTrue(exception.getMessage().contains("Metric 'net_value'"), exception.getMessage());
        assertTrue(exception.getMessage().contains(reason), exception.getMessage());
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUM(orders.amount)", "SUM([orders].[missing])", "COUNT(DISTINCT [orders].[amount])",
            "COALESCE(SUM([orders].[amount]), 0)", "CASE WHEN TRUE THEN 1 ELSE 0 END", "not a formula",
            "SUM([orders].[amount]) OVER ()", "SUM([orders].[amount] + [orders].[status])"})
    void tableauCannotBypassParsingOrReferenceValidation(String expression) {
        assertThrows(ConversionException.class, () -> translate("TABLEAU", expression));
    }

    @Test
    void selectedDialectFailureNeverFallsBack() {
        Map<String, Object> metric = metric("TABLEAU", "SUM([orders].[missing])");
        metric.put("expression", Map.of("dialects", List.of(
                Map.of("dialect", "ANSI_SQL", "expression", "SUM(orders.amount)"),
                Map.of("dialect", "TABLEAU", "expression", "SUM([orders].[missing])"))));
        assertThrows(ConversionException.class, () -> MetricExpressionTranslator.translate(metric, source(), target()));
    }

    @Test
    void rejectsDuplicateDialectAndMissingOrEmptyExpressions() {
        Map<String, Object> metric = metric("SNOWFLAKE", "SUM(orders.amount)");
        Map<String, Object> entry = Map.of("dialect", "SNOWFLAKE", "expression", "SUM(orders.amount)");
        metric.put("expression", Map.of("dialects", List.of(entry, entry)));
        assertThrows(ConversionException.class, () -> MetricExpressionTranslator.translate(metric, source(), target()));
        for (Object expression : List.of(Map.of(), Map.of("dialects", List.of()),
                Map.of("dialects", List.of(Map.of("dialect", "BIGQUERY", "expression", "1"))))) {
            metric.put("expression", expression);
            assertThrows(ConversionException.class, () -> MetricExpressionTranslator.translate(metric, source(), target()));
        }
        assertThrows(ConversionException.class, () -> translate("SNOWFLAKE", " "));
    }

    @Test
    void outputDatatypeMustAgreeWithTheFormula() {
        Map<String, Object> metric = metric("SNOWFLAKE", "AVG(orders.quantity)");
        for (String datatype : List.of("Integer", "String", "Boolean", "Date", "Opaque", "Time")) {
            metric.put("datatype", datatype);
            assertThrows(ConversionException.class, () -> MetricExpressionTranslator.translate(metric, source(), target()));
        }
        metric.put("datatype", "Decimal");
        assertEquals("Number", MetricExpressionTranslator.translate(metric, source(), target()).dataType());
        Map<String, Object> countMetric = metric("SNOWFLAKE", "COUNT(orders.quantity)");
        countMetric.put("datatype", "Integer");
        assertEquals("Number", MetricExpressionTranslator.translate(countMetric, source(), target()).dataType());
        Map<String, Object> nullMetric = metric("SNOWFLAKE", "NULL");
        nullMetric.put("datatype", "Decimal");
        assertEquals("NULL", MetricExpressionTranslator.translate(nullMetric, source(), target()).expression());
    }

    @Test
    void limitsNestingAndExpansionWithoutStackOverflow() {
        assertThrows(ConversionException.class, () -> translate("SNOWFLAKE", "(".repeat(200) + "1" + ")".repeat(200)));
        assertThrows(ConversionException.class, () -> translate("SNOWFLAKE", "-".repeat(200) + "1"));
        String formula = "SUM(orders.amount)";
        for (int i = 0; i < 20; i++) formula = "NULLIF(" + formula + ", 0)";
        String expanded = formula;
        assertThrows(ConversionException.class, () -> translate("SNOWFLAKE", expanded));
    }

    @Test
    void temporalAggregatesCanBeUsedInNumericPredicates() {
        assertEquals("(IF (MIN([orders].[ordered]) = MAX([orders].[ordered])) THEN 1 ELSE 0 END)",
                translate("SNOWFLAKE", "CASE WHEN MIN(orders.ordered) = MAX(orders.ordered) THEN 1 ELSE 0 END"));
    }

    @Test
    void separateAggregatesRequireConnectedDatasets() {
        Map<String, Object> twoSources = Map.of("datasets", List.of(
                Map.of("name", "orders", "fields", List.of(Map.of("name", "amount", "datatype", "Decimal"))),
                Map.of("name", "returns", "fields", List.of(Map.of("name", "amount", "datatype", "Decimal")))));
        Map<String, Object> twoTargets = new LinkedHashMap<>(Map.of("semanticDataObjects", List.of(
                Map.of("apiName", "orders", "semanticMeasurements", List.of(Map.of("apiName", "amount", "dataType", "Number"))),
                Map.of("apiName", "returns", "semanticMeasurements", List.of(Map.of("apiName", "amount", "dataType", "Number"))))));
        Map<String, Object> metric = metric("SNOWFLAKE", "SUM(orders.amount) - SUM(returns.amount)");
        assertThrows(ConversionException.class, () -> MetricExpressionTranslator.translate(metric, twoSources, twoTargets));
        twoTargets.put("semanticRelationships", List.of(Map.of("leftSemanticDefinitionApiName", "orders",
                "rightSemanticDefinitionApiName", "returns")));
        assertEquals("(SUM([orders].[amount]) - SUM([returns].[amount]))",
                MetricExpressionTranslator.translate(metric, twoSources, twoTargets).expression());
        assertThrows(ConversionException.class, () -> MetricExpressionTranslator.translate(
                metric("SNOWFLAKE", "SUM(orders.amount - returns.amount)"), twoSources, twoTargets));
    }
}
