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
import org.apache.ossie.exception.ConversionException;
import org.junit.jupiter.api.Test;

class MetricCompilationPlanTest {
    private static Map<String, Object> metric(String name, String expression) {
        return metric(name, expression, "ANSI_SQL");
    }
    private static Map<String, Object> metric(String name, String expression, String dialect) {
        return new LinkedHashMap<>(Map.of("name", name, "expression", Map.of("dialects",
                List.of(Map.of("dialect", dialect, "expression", expression)))));
    }
    private static MetricCompilationPlan plan(List<Map<String, Object>> metrics) {
        Map<String, Object> source = Map.of("metrics", metrics, "datasets", List.of(Map.of("name", "orders", "fields",
                List.of(Map.of("name", "profit", "datatype", "Decimal"), Map.of("name", "units", "datatype", "Integer")))));
        Map<String, Object> target = Map.of("semanticDataObjects", List.of(Map.of("apiName", "orders", "semanticMeasurements",
                List.of(Map.of("apiName", "profit", "dataType", "Number"), Map.of("apiName", "units", "dataType", "Number")))));
        return new MetricCompilationPlan(source, new MetricFieldResolver(source, target));
    }
    @Test
    void dependenciesKeepAggregateLevelAndInferredDatatypeAndAreCached() {
        var plan = plan(List.of(metric("ratio", "total / count_units"), metric("total", "SUM(orders.profit)"),
                metric("count_units", "COUNT(orders.units)")));
        var result = plan.compile("ratio");
        assertEquals(ExpressionCompiler.Level.AGGREGATE, result.level());
        assertEquals("Decimal", result.datatype());
        assertEquals(java.util.Set.of("orders"), result.datasets());
        assertEquals("Integer", plan.compile("count_units").datatype());
        assertSame(result, plan.compile("ratio"));
    }
    @Test
    void bracketedTableauMetricReferencesUseTheSameDependencyChecks() {
        var plan = plan(List.of(metric("result", "[total] + 1", "TABLEAU"), metric("total", "SUM(orders.profit)")));
        assertEquals("((SUM([orders].[profit])) + 1)", plan.compile("result").expression());
    }
    @Test
    void forwardReferencesAndNormalizedSqlNamesAreResolved() {
        var plan = plan(List.of(metric("result", "TOTAL + 1"), metric("total", "SUM(orders.profit)")));
        assertTrue(plan.compile("result").expression().contains("SUM([orders].[profit])"));
    }
    @Test
    void ambiguousUnqualifiedFieldOrMetricIsRejected() {
        var plan = plan(List.of(metric("result", "profit + 1"), metric("profit", "SUM(orders.profit)")));
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("result")).getMessage().contains("ambiguous field or metric"));
        assertDoesNotThrow(() -> plan.compile("profit"));
    }
    @Test
    void ambiguousCaseFoldedMetricNamesAreRejected() {
        var plan = plan(List.of(metric("result", "total + 1"), metric("total", "1"), metric("TOTAL", "2")));
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("result")).getMessage().contains("ambiguous"));
    }
    @Test
    void reportsDependencyCycleWithPathAndCanStillCompileIndependentMetric() {
        var plan = plan(List.of(metric("a", "b + 1"), metric("b", "c + 1"), metric("c", "a + 1"), metric("ok", "1")));
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("a")).getMessage().contains("a -> b -> c -> a"));
        assertEquals("1", plan.compile("ok").expression());
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("a")).getMessage().contains("a -> b -> c -> a"));
    }
    @Test
    void rejectsAggregateOfAnAggregateMetric() {
        var plan = plan(List.of(metric("bad", "SUM(total)"), metric("total", "SUM(orders.profit)")));
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("bad")).getMessage().contains("nested aggregate"));
    }
    @Test
    void rejectsRowAndAggregateMixAcrossDependency() {
        var plan = plan(List.of(metric("bad", "total + orders.profit"), metric("total", "SUM(orders.profit)")));
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("bad")).getMessage().contains("mix aggregate"));
    }
    @Test
    void allNullDependencyRetainsItsExplicitDeclaredType() {
        var empty = metric("empty", "NULL");
        empty.put("datatype", "Integer");
        var plan = plan(List.of(metric("result", "COALESCE(empty, 1)"), empty));
        assertEquals("Integer", plan.compile("result").datatype());
    }
    @Test
    void fractionalDependencyCannotSatisfyIntegerDeclaration() {
        var integer = metric("rounded", "total");
        integer.put("datatype", "Integer");
        var plan = plan(List.of(integer, metric("total", "AVG(orders.units)")));
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("rounded")).getMessage().contains("incompatible"));
    }
    @Test
    void boundsDependencyDepthBeforeStackExhaustion() {
        List<Map<String, Object>> metrics = new ArrayList<>();
        for (int i = 0; i < 150; i++) metrics.add(metric("m" + i, i == 149 ? "1" : "m" + (i + 1) + " + 1"));
        var plan = plan(metrics);
        assertTrue(assertThrows(ConversionException.class, () -> plan.compile("m0")).getMessage().contains("dependency depth"));
    }
    @Test
    void boundsExponentialDependencyExpansion() {
        List<Map<String, Object>> metrics = new ArrayList<>();
        metrics.add(metric("m0", "SUM(orders.profit)"));
        for (int i = 1; i < 20; i++) metrics.add(metric("m" + i, "m" + (i - 1) + " + m" + (i - 1)));
        var plan = plan(metrics);
        var error = assertThrows(ConversionException.class, () -> plan.compile("m19"));
        assertTrue(error.getMessage().contains("exceeds 131072 characters"), error.getMessage());
    }
    @Test
    void sharedDependencyAcrossManyMetricsDoesNotAlterTheCompiledResult() {
        List<Map<String, Object>> metrics = new ArrayList<>();
        metrics.add(metric("base", "SUM(orders.profit)"));
        for (int i = 0; i < 500; i++) metrics.add(metric("m" + i, "base + " + i));
        var plan = plan(metrics);
        for (int i = 0; i < 500; i++) assertEquals("((SUM([orders].[profit])) + " + i + ")", plan.compile("m" + i).expression());
        assertSame(plan.compile("base"), plan.compile("base"));
    }
}
