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

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Hand-computed examples evaluated independently from the production parser/AST.
 *
 * <p>This is a local regression oracle for the emitted Tua subset, not a Tableau Next execution
 * test. In particular, the evaluator models three-valued Boolean logic, null-ignoring aggregates,
 * and half-away-from-zero rounding; it does not establish native engine behavior or precision.
 */
class MetricExpressionSemanticsTest {

    private static final List<Map<String, Object>> ORDERS = List.of(
            row(10.0, 2.0, true),
            row(10.0, 0.0, false),
            row(-4.0, 4.0, null),
            row(null, null, null));

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void aggregatesIgnoreNullsButPreserveDuplicates(String dialect) {
        assertValue(dialect, "SUM(orders.amount)", ORDERS, 16.0);
        assertValue(dialect, "AVG(orders.amount)", ORDERS, 16.0 / 3);
        assertValue(dialect, "MIN(orders.amount)", ORDERS, -4.0);
        assertValue(dialect, "MAX(orders.amount)", ORDERS, 10.0);
        assertValue(dialect, "COUNT(orders.amount)", ORDERS, 3.0);
        assertValue(dialect, "COUNT(DISTINCT orders.amount)", ORDERS, 2.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void emptyAndAllNullInputsHaveDifferentCountAndSumResults(String dialect) {
        for (List<Map<String, Object>> rows : List.of(
                List.<Map<String, Object>>of(), List.of(row(null, null, null)))) {
            for (String aggregate : List.of("SUM", "AVG", "MIN", "MAX")) {
                assertValue(dialect, aggregate + "(orders.amount)", rows, null);
            }
            assertValue(dialect, "COUNT(orders.amount)", rows, 0.0);
            assertValue(dialect, "COUNT(DISTINCT orders.amount)", rows, 0.0);
            assertValue(dialect, "COALESCE(SUM(orders.amount), 0)", rows, 0.0);
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void composedConditionalArithmeticPreservesNullsAndGrouping(String dialect) {
        // Per-row numerator contributions are 20, 10, -4, 0; COUNT ignores the null amount.
        assertValue(dialect,
                "SUM(CASE WHEN orders.flag AND orders.amount > 0 "
                        + "THEN orders.amount * 2 ELSE COALESCE(orders.amount, 0) END) "
                        + "/ NULLIF(COUNT(orders.amount), 0)",
                ORDERS, 26.0 / 3);
        assertValue(dialect,
                "(SUM(orders.amount) + 2) * (MAX(orders.cost) - MIN(orders.cost))",
                ORDERS, 72.0);
        assertValue(dialect,
                "SUM(CASE WHEN orders.amount < 0 THEN -orders.amount END)",
                ORDERS, 4.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void guardedRatiosReturnNullForZeroAndEmptyDenominators(String dialect) {
        String ratio = "SUM(orders.amount) / NULLIF(SUM(orders.cost), 0)";
        assertValue(dialect, ratio, ORDERS, 16.0 / 6);
        assertValue(dialect, ratio, List.of(row(8.0, 1.0, true), row(4.0, -1.0, false)), null);
        assertValue(dialect, ratio, List.of(row(8.0, null, true)), null);
        assertValue(dialect, ratio, List.of(), null);
        assertValue(dialect, "COALESCE(" + ratio + ", 0)", List.of(), 0.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void nestedCoalesceSelectsTheFirstNonNullValueIncludingZero(String dialect) {
        String expression = "COALESCE(SUM(orders.amount), NULLIF(SUM(orders.cost), 0), 7)";
        assertValue(dialect, expression, ORDERS, 16.0);
        assertValue(dialect, expression, List.of(row(0.0, 9.0, true)), 0.0);
        assertValue(dialect, expression, List.of(row(null, 9.0, true)), 9.0);
        assertValue(dialect, expression, List.of(row(null, 0.0, true)), 7.0);
        assertValue(dialect, expression, List.of(), 7.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void nullablePredicatesUseThreeValuedLogic(String dialect) {
        assertValue(dialect,
                "SUM(CASE WHEN NOT (orders.flag = TRUE) THEN orders.amount ELSE 0 END)",
                ORDERS, 10.0);
        assertValue(dialect,
                "SUM(CASE WHEN orders.amount IS NOT NULL AND "
                        + "(orders.flag = FALSE OR orders.flag IS NULL) "
                        + "THEN orders.amount ELSE 0 END)",
                ORDERS, 6.0);
        assertValue(dialect,
                "SUM(CASE WHEN NOT (orders.flag OR orders.amount < 0) THEN 1 ELSE 0 END)",
                ORDERS, 1.0);
        assertValue(dialect,
                "SUM(CASE WHEN orders.flag IS NULL THEN "
                        + "CASE WHEN orders.amount IS NULL THEN 3 ELSE 2 END ELSE 0 END)",
                ORDERS, 5.0);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void numericFunctionsComposeAroundNegativeAggregates(String dialect) {
        List<Map<String, Object>> negative = List.of(row(-2.55, 0.0, true), row(-2.55, 0.0, false));
        assertValue(dialect, "ROUND(AVG(orders.amount), 1)", negative, -2.6);
        assertValue(dialect, "ABS(ROUND(AVG(orders.amount), 1))", negative, 2.6);
        assertValue(dialect, "CEIL(AVG(orders.amount)) + FLOOR(AVG(orders.amount))", negative, -5.0);
        assertValue(dialect, "ABS(ROUND(AVG(orders.amount), 1))", List.of(), null);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SNOWFLAKE", "ANSI_SQL"})
    void textPredicatesPreserveDuplicatesNullsAndEscapedApostrophes(String dialect) {
        List<Map<String, Object>> orders = List.of(
                row(10.0, 0.0, true, "paid"),
                row(7.0, 0.0, true, "paid"),
                row(4.0, 0.0, false, "pending"),
                row(9.0, 0.0, null, null),
                row(2.0, 0.0, true, "O'Brien"));
        assertValue(dialect,
                "SUM(CASE WHEN orders.status = 'paid' THEN orders.amount ELSE 0 END)",
                orders, 17.0);
        assertValue(dialect, "COUNT(DISTINCT orders.status)", orders, 3.0);
        assertValue(dialect, "COUNT(orders.status)", orders, 4.0);
        assertValue(dialect,
                "SUM(CASE WHEN COALESCE(NULLIF(orders.status, 'pending'), 'missing') = 'missing' "
                        + "THEN orders.amount ELSE 0 END)",
                orders, 13.0);
        assertValue(dialect,
                "SUM(CASE WHEN COALESCE(orders.status, 'paid') = 'paid' THEN orders.amount ELSE 0 END)",
                orders, 26.0);
        assertValue(dialect,
                "SUM(CASE WHEN orders.status = 'O''Brien' THEN orders.amount ELSE 0 END)",
                orders, 2.0);
        assertValue(dialect, "COUNT(DISTINCT orders.status)", List.of(), 0.0);
        assertValue(dialect, "COUNT(DISTINCT orders.status)", List.of(row(null, null, null, null)), 0.0);
    }

    private static void assertValue(
            String dialect, String sql, List<Map<String, Object>> rows, Double expected) {
        Map<String, Object> metric = Map.of(
                "name", "fixture_metric",
                "datatype", "Decimal",
                "expression", Map.of("dialects", List.of(Map.of("dialect", dialect, "expression", sql))));
        Map<String, Object> source = Map.of("datasets", List.of(Map.of(
                "name", "orders",
                "fields", List.of(
                        Map.of("name", "amount", "datatype", "Decimal"),
                        Map.of("name", "cost", "datatype", "Decimal"),
                        Map.of("name", "flag", "datatype", "Boolean"),
                        Map.of("name", "status", "datatype", "String")))));
        Map<String, Object> target = Map.of("semanticDataObjects", List.of(Map.of(
                "apiName", "orders",
                "semanticMeasurements", List.of(
                        Map.of("apiName", "amount", "dataObjectFieldName", "amount__c", "dataType", "Number"),
                        Map.of("apiName", "cost", "dataObjectFieldName", "cost__c", "dataType", "Number")),
                "semanticDimensions", List.of(
                        Map.of("apiName", "flag", "dataObjectFieldName", "flag__c", "dataType", "Boolean"),
                        Map.of("apiName", "status", "dataObjectFieldName", "status__c", "dataType", "Text")))));
        MetricExpressionTranslator.Result translated = MetricExpressionTranslator.translate(metric, source, target);
        assertEquals("Number", translated.dataType(), sql);
        Object actual = new TuaSubsetEvaluator(translated.expression()).evaluate(rows);
        String message = dialect + ": " + sql + " -> " + translated.expression();
        if (expected == null) {
            assertNull(actual, message);
        } else {
            assertInstanceOf(Number.class, actual, message);
            assertEquals(expected, ((Number) actual).doubleValue(), 1e-12, message);
        }
    }

    private static Map<String, Object> row(Double amount, Double cost, Boolean flag) {
        return row(amount, cost, flag, null);
    }

    private static Map<String, Object> row(Double amount, Double cost, Boolean flag, String status) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("amount", amount);
        result.put("cost", cost);
        result.put("flag", flag);
        result.put("status", status);
        return result;
    }

    /** Reads only generated Tua; it neither accepts SQL CASE/NULLIF nor uses production AST nodes. */
    private static final class TuaSubsetEvaluator {
        private static final Pattern TOKEN = Pattern.compile(
                "\\s*(\\[[^\\]]+\\]|[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
                        + "|'(?:[^']|'')*'|[A-Za-z_][A-Za-z_0-9]*|<>|!=|<=|>=|[().,+*/=<>-])");
        private final List<String> tokens = new ArrayList<>();
        private int position;

        TuaSubsetEvaluator(String expression) {
            Matcher matcher = TOKEN.matcher(expression);
            int offset = 0;
            while (offset < expression.length()) {
                if (expression.substring(offset).isBlank()) {
                    break;
                }
                if (!matcher.find(offset) || matcher.start() != offset) {
                    throw new AssertionError("Unexpected Tua token: " + expression.substring(offset));
                }
                tokens.add(matcher.group(1));
                offset = matcher.end();
            }
        }

        Object evaluate(List<Map<String, Object>> rows) {
            Calculation calculation = expression(0);
            assertEquals(tokens.size(), position, "Unconsumed generated Tua tokens");
            return calculation.value(rows, Map.of());
        }

        private Calculation expression(int minimum) {
            Calculation left = prefix();
            while (position < tokens.size() && precedence(tokens.get(position)) >= minimum) {
                String operator = next().toUpperCase(java.util.Locale.ROOT);
                Calculation right = expression(precedence(operator) + 1);
                Calculation previous = left;
                left = (rows, row) -> binary(operator, previous.value(rows, row), right.value(rows, row));
            }
            return left;
        }

        private Calculation prefix() {
            String token = next();
            if (token.equals("(")) {
                Calculation result = expression(0);
                expect(")");
                return result;
            }
            if (token.equalsIgnoreCase("IF")) {
                Calculation condition = expression(0);
                expect("THEN");
                Calculation yes = expression(0);
                expect("ELSE");
                Calculation no = expression(0);
                expect("END");
                return (rows, row) -> (Boolean.TRUE.equals(condition.value(rows, row)) ? yes : no).value(rows, row);
            }
            if (token.equalsIgnoreCase("NOT") || token.equals("-")) {
                Calculation child = expression(token.equals("-") ? 7 : 3);
                return (rows, row) -> {
                    Object value = child.value(rows, row);
                    return value == null ? null : token.equals("-") ? -number(value) : !(Boolean) value;
                };
            }
            if (token.equalsIgnoreCase("NULL")) {
                return (rows, row) -> null;
            }
            if (token.equalsIgnoreCase("TRUE") || token.equalsIgnoreCase("FALSE")) {
                return (rows, row) -> Boolean.valueOf(token);
            }
            if (token.startsWith("'")) {
                String literal = token.substring(1, token.length() - 1).replace("''", "'");
                return (rows, row) -> literal;
            }
            if (token.startsWith("[")) {
                assertEquals("[orders]", token);
                expect(".");
                String field = next();
                assertTrue(field.startsWith("[") && field.endsWith("]"));
                String name = field.substring(1, field.length() - 1);
                return (rows, row) -> {
                    assertTrue(row.containsKey(name), "Unknown generated field: " + name);
                    return row.get(name);
                };
            }
            if (Character.isDigit(token.charAt(0))) {
                return (rows, row) -> Double.valueOf(token);
            }
            expect("(");
            List<Calculation> arguments = new ArrayList<>();
            do {
                arguments.add(expression(0));
            } while (take(","));
            expect(")");
            return function(token.toUpperCase(java.util.Locale.ROOT), arguments);
        }

        private static Calculation function(String name, List<Calculation> arguments) {
            if (List.of("SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD").contains(name)) {
                assertEquals(1, arguments.size());
                return (rows, row) -> {
                    List<Object> values = rows.stream()
                            .map(input -> arguments.getFirst().value(rows, input))
                            .filter(java.util.Objects::nonNull).toList();
                    if (name.equals("COUNT")) {
                        return (double) values.size();
                    }
                    if (name.equals("COUNTD")) {
                        return (double) values.stream().distinct().count();
                    }
                    if (values.isEmpty()) {
                        return null;
                    }
                    return switch (name) {
                        case "SUM" -> values.stream().mapToDouble(TuaSubsetEvaluator::number).sum();
                        case "AVG" -> values.stream().mapToDouble(TuaSubsetEvaluator::number).average().orElseThrow();
                        case "MIN" -> values.stream().mapToDouble(TuaSubsetEvaluator::number).min().orElseThrow();
                        case "MAX" -> values.stream().mapToDouble(TuaSubsetEvaluator::number).max().orElseThrow();
                        default -> throw new AssertionError(name);
                    };
                };
            }
            assertTrue(List.of("IFNULL", "ISNULL", "ABS", "CEILING", "FLOOR", "ROUND").contains(name),
                    "Unsupported generated function: " + name);
            return (rows, row) -> {
                Object value = arguments.getFirst().value(rows, row);
                if (name.equals("IFNULL")) {
                    assertEquals(2, arguments.size());
                    return value != null ? value : arguments.get(1).value(rows, row);
                }
                if (name.equals("ISNULL")) {
                    assertEquals(1, arguments.size());
                    return value == null;
                }
                if (value == null) {
                    return null;
                }
                return switch (name) {
                    case "ABS" -> Math.abs(number(value));
                    case "CEILING" -> Math.ceil(number(value));
                    case "FLOOR" -> Math.floor(number(value));
                    case "ROUND" -> BigDecimal.valueOf(number(value)).setScale(
                            arguments.size() == 1 ? 0 : (int) number(arguments.get(1).value(rows, row)),
                            RoundingMode.HALF_UP).doubleValue();
                    default -> throw new AssertionError("Unsupported generated function: " + name);
                };
            };
        }

        private static Object binary(String operator, Object left, Object right) {
            if (operator.equals("AND")) {
                if (Boolean.FALSE.equals(left) || Boolean.FALSE.equals(right)) {
                    return false;
                }
                return left == null || right == null ? null : Boolean.TRUE;
            }
            if (operator.equals("OR")) {
                if (Boolean.TRUE.equals(left) || Boolean.TRUE.equals(right)) {
                    return true;
                }
                return left == null || right == null ? null : Boolean.FALSE;
            }
            if (left == null || right == null) {
                return null;
            }
            return switch (operator) {
                case "+" -> number(left) + number(right);
                case "-" -> number(left) - number(right);
                case "*" -> number(left) * number(right);
                case "/" -> {
                    assertNotEquals(0.0, number(right), "Generated expression evaluated an unguarded zero divisor");
                    yield number(left) / number(right);
                }
                case "=" -> left.equals(right);
                case "!=", "<>" -> !left.equals(right);
                case "<" -> number(left) < number(right);
                case "<=" -> number(left) <= number(right);
                case ">" -> number(left) > number(right);
                case ">=" -> number(left) >= number(right);
                default -> throw new AssertionError(operator);
            };
        }

        private static double number(Object value) {
            return ((Number) value).doubleValue();
        }

        private static int precedence(String token) {
            return switch (token.toUpperCase(java.util.Locale.ROOT)) {
                case "OR" -> 1;
                case "AND" -> 2;
                case "=", "!=", "<>", "<", ">", "<=", ">=" -> 4;
                case "+", "-" -> 5;
                case "*", "/" -> 6;
                default -> -1;
            };
        }

        private boolean take(String token) {
            if (position < tokens.size() && tokens.get(position).equalsIgnoreCase(token)) {
                position++;
                return true;
            }
            return false;
        }

        private String next() {
            assertTrue(position < tokens.size(), "Unexpected end of generated Tua expression");
            return tokens.get(position++);
        }

        private void expect(String token) {
            assertEquals(token, next().toUpperCase(java.util.Locale.ROOT));
        }
    }

    @FunctionalInterface
    private interface Calculation {
        Object value(List<Map<String, Object>> rows, Map<String, Object> row);
    }
}
