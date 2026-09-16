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
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class ExpressionCompilerTest {
    private static final Map<String, String> TYPES = Map.of(
            "amount", "Decimal", "quantity", "Integer", "email", "String", "active", "Boolean",
            "ordered", "Date", "timestamp", "DateTime", "zoned", "DateTimeTz");
    private static ExpressionCompiler.Binding resolve(ExpressionCompiler.Reference reference) {
        String field = reference.parts().getLast().text();
        String datatype = TYPES.get(field);
        if (datatype == null) throw new IllegalArgumentException("Unknown field " + field);
        return new ExpressionCompiler.Binding("[orders].[" + field + "]", datatype, "orders");
    }
    private static ExpressionCompiler.Compiled compile(String source, String dialect) {
        return ExpressionCompiler.compile(ExpressionCompiler.parse(source, dialect), ExpressionCompilerTest::resolve);
    }

    @Test void parsesWithoutBindingAndRetainsQuotedIdentifiers() {
        var parsed = ExpressionCompiler.parse("\"Order.Items\".\"Net Revenue\"", "SNOWFLAKE");
        var reference = ExpressionCompiler.directReference(parsed).orElseThrow();
        assertEquals(List.of(new MetricFieldResolver.Identifier("Order.Items", true),
                new MetricFieldResolver.Identifier("Net Revenue", true)), reference.parts());
        assertFalse(reference.tableau());
        assertTrue(ExpressionCompiler.directReference(ExpressionCompiler.parse("(amount)", "ANSI_SQL")).isPresent());
        assertTrue(ExpressionCompiler.directReference(ExpressionCompiler.parse("amount + 1", "ANSI_SQL")).isEmpty());
    }

    @Test void independentlyBindsAndRendersTheSameImmutableParse() {
        var parsed = ExpressionCompiler.parse("SUM(amount)", "SNOWFLAKE");
        AtomicInteger calls = new AtomicInteger();
        var first = ExpressionCompiler.compile(parsed, reference -> {
            calls.incrementAndGet(); return new ExpressionCompiler.Binding("[one].[net]", "Decimal", "one");
        });
        var second = ExpressionCompiler.compile(parsed,
                reference -> new ExpressionCompiler.Binding("[two].[gross]", "Decimal", "two"));
        assertEquals(1, calls.get());
        assertEquals("SUM([one].[net])", first.expression());
        assertEquals(Set.of("two"), second.datasets());
        assertEquals("SUM([two].[gross])", second.expression());
    }

    @ParameterizedTest
    @ValueSource(strings = {"CAST(amount AS INT)", "amount::INT", "SUM(amount) OVER ()",
            "SUM(amount) FILTER (WHERE active)", "(SELECT amount FROM orders)",
            "amount IN (1, 2)", "SUM(amount ORDER BY quantity)", "amount(+) = quantity",
            "CASE amount WHEN 1 THEN 2 END", "SUM(amount) AS alias", "SUM(amount); SUM(quantity)",
            "SUM(amount) trailing", "SELECT amount", "unknown_function(amount)", "COALESCE()"})
    void rejectsShapesWithoutExplicitCapabilities(String source) {
        assertThrows(IllegalArgumentException.class, () -> compile(source, "SNOWFLAKE"), source);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUM(ALL amount)", "SUM(UNIQUE amount)", "SUM(amount IGNORE NULLS)",
            "SUM(amount RESPECT NULLS)", "SUM(amount) IGNORE NULLS",
            "SUM(amount) KEEP (DENSE_RANK LAST ORDER BY quantity)", "SUM(amount LIMIT 1)",
            "SUM(amount HAVING MAX quantity)", "SUM(amount ORDER BY quantity)",
            "SUM(amount).attribute", "private_schema.SUM(amount)", "\"SUM\"(amount)",
            "N'prefixed'", "email ISNULL", "email NOTNULL", "PRIOR amount = quantity", "!active"})
    void rejectsDialectModifiersInsteadOfSilentlyDiscardingThem(String source) {
        assertThrows(IllegalArgumentException.class, () -> compile(source, "SNOWFLAKE"), source);
    }

    @Test void nativeMetricReferencesAreBoundByTheModelResolver() {
        var result = ExpressionCompiler.compile(ExpressionCompiler.parse("[net] + 1", "TABLEAU"), reference -> {
            assertTrue(reference.tableau());
            assertEquals(List.of(new MetricFieldResolver.Identifier("net", true)), reference.parts());
            return new ExpressionCompiler.Binding("SUM([orders].[amount])", "Decimal", "orders", ExpressionCompiler.Level.AGGREGATE);
        });
        assertEquals("(SUM([orders].[amount]) + 1)", result.expression());
        assertEquals(ExpressionCompiler.Level.AGGREGATE, result.level());
    }

    @Test void escapedQuotedIdentifiersDoNotChangeReferenceBoundaries() {
        for (String dialect : List.of("SNOWFLAKE", "ANSI_SQL")) {
            var reference = ExpressionCompiler.directReference(ExpressionCompiler.parse("\"a\"\"b.c\".\"d\"\"e\"", dialect)).orElseThrow();
            assertEquals(List.of(new MetricFieldResolver.Identifier("a\"b.c", true),
                    new MetricFieldResolver.Identifier("d\"e", true)), reference.parts());
        }
        var bracket = ExpressionCompiler.directReference(ExpressionCompiler.parse("[a.b].[c]", "ANSI_SQL")).orElseThrow();
        assertTrue(bracket.tableau());
        assertEquals(List.of(new MetricFieldResolver.Identifier("a.b", true),
                new MetricFieldResolver.Identifier("c", true)), bracket.parts());
        // JSqlParser does not consume doubled closing brackets. Fail closed, rather
        // than resolving a truncated identifier to another field.
        assertThrows(IllegalArgumentException.class,
                () -> ExpressionCompiler.parse("[a.b].[c]]d]", "ANSI_SQL"));
    }

    @Test void preservesNumberPrecisionAndEscapedStringValues() {
        assertEquals("0.12345678901234567890123456789",
                compile("0.12345678901234567890123456789", "SNOWFLAKE").expression());
        var result = compile("CASE WHEN email = 'O''Brien -- not a comment' THEN 'a''b' ELSE 'x' END", "SNOWFLAKE");
        assertEquals("String", result.datatype());
        assertEquals(ExpressionCompiler.Level.ROW, result.level());
        assertEquals("(IF ([orders].[email] = 'O''Brien -- not a comment') THEN 'a''b' ELSE 'x' END)", result.expression());
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void compilesExistingDerivedDateAndEmailDomainExpressions(String dialect) {
        var year = compile("YEAR(ordered)", dialect);
        assertEquals("YEAR([orders].[ordered])", year.expression());
        assertEquals("Integer", year.datatype());
        var domain = compile("SUBSTRING(email, POSITION('@' IN email) + 1, LENGTH(email))", dialect);
        assertEquals("MID([orders].[email], (FIND([orders].[email], '@') + 1), LEN([orders].[email]))", domain.expression());
        assertEquals("String", domain.datatype());
        assertEquals(ExpressionCompiler.Level.ROW, domain.level());
        assertEquals(domain.expression(), compile(domain.expression(), "TABLEAU").expression());
        assertEquals("SUM(YEAR([orders].[ordered]))", compile("SUM(YEAR(ordered))", dialect).expression());
        assertEquals("SUM(LEN([orders].[email]))", compile("SUM(LENGTH(email))", dialect).expression());
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUBSTRING(email, 0)", "SUBSTRING(email, -1)", "SUBSTRING(email, quantity)",
            "SUBSTRING(email, 1, -1)", "SUBSTRING(email, 1, quantity)", "SUBSTRING(email, 1.5)",
            "YEAR(zoned)", "YEAR(email)", "LENGTH(quantity)", "POSITION(1 IN email)"})
    void rejectsScalarDomainsWithoutEquivalentTargetSemantics(String source) {
        assertThrows(IllegalArgumentException.class, () -> compile(source, "SNOWFLAKE"), source);
    }

    @ParameterizedTest
    @ValueSource(strings = {"CEIL(AVG(amount))", "FLOOR(AVG(amount))", "ROUND(AVG(amount))",
            "ROUND(AVG(amount), 0)", "ROUND(AVG(amount), -2)"})
    void integralRoundingCanSatisfyAnIntegerMetricDeclaration(String source) {
        var result = compile(source, "SNOWFLAKE");
        assertEquals("Integer", result.datatype());
        var metric = Map.<String, Object>of("name", "rounded", "datatype", "Integer", "expression",
                Map.of("dialects", List.of(Map.of("dialect", "SNOWFLAKE", "expression", source))));
        assertEquals("Integer", MetricExpressionTranslator.compile(metric, ExpressionCompilerTest::resolve).datatype());
    }

    @Test void positivePrecisionDoesNotClaimAnIntegralResult() {
        assertEquals("Decimal", compile("ROUND(AVG(amount), 2)", "SNOWFLAKE").datatype());
        assertEquals("Integer", compile("ROUND(SUM(quantity), 2)", "SNOWFLAKE").datatype());
    }

    @Test void boundDerivedRowsAndMetricsPreserveTheirLevelAndLineage() {
        var row = ExpressionCompiler.compile(ExpressionCompiler.parse("SUM(net)", "SNOWFLAKE"),
                reference -> new ExpressionCompiler.Binding("([orders].[amount] * [orders].[quantity])", "Decimal", "orders"));
        assertEquals("SUM(([orders].[amount] * [orders].[quantity]))", row.expression());
        var aggregate = ExpressionCompiler.compile(ExpressionCompiler.parse("ratio + 1", "SNOWFLAKE"),
                reference -> new ExpressionCompiler.Binding("(SUM([orders].[amount]) / SUM([costs].[amount]))",
                        "Decimal", Set.of("orders", "costs"), ExpressionCompiler.Level.AGGREGATE));
        assertEquals(Set.of("orders", "costs"), aggregate.datasets());
        assertEquals(ExpressionCompiler.Level.AGGREGATE, aggregate.level());
        assertThrows(IllegalArgumentException.class, () -> ExpressionCompiler.compile(
                ExpressionCompiler.parse("SUM(ratio)", "SNOWFLAKE"),
                reference -> new ExpressionCompiler.Binding("SUM([orders].[amount])", "Decimal", "orders", ExpressionCompiler.Level.AGGREGATE)));
    }

    @Test void declaredTypeSurvivesAnAllNullMetricForDependentMetrics() {
        var metric = Map.<String, Object>of("name", "nullable", "datatype", "Decimal", "expression",
                Map.of("dialects", List.of(Map.of("dialect", "SNOWFLAKE", "expression", "NULL"))));
        assertEquals("Decimal", MetricExpressionTranslator.compile(metric, ExpressionCompilerTest::resolve).datatype());
    }

    @Test void limitsInputDepthTokensAndEmittedExpansion() {
        assertThrows(IllegalArgumentException.class, () -> compile("(".repeat(129) + "1" + ")".repeat(129), "SNOWFLAKE"));
        assertThrows(IllegalArgumentException.class, () -> compile("1+".repeat(5000) + "1", "SNOWFLAKE"));
        assertThrows(IllegalArgumentException.class, () -> compile("1".repeat(32769), "SNOWFLAKE"));
        String growing = "SUM(amount)";
        for (int i = 0; i < 18; i++) growing = "NULLIF(" + growing + ", 0)";
        String source = growing;
        var exception = assertThrows(IllegalArgumentException.class, () -> compile(source, "SNOWFLAKE"));
        assertTrue(exception.getMessage().contains("131072"), exception.getMessage());
    }
}
