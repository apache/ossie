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

import static org.apache.ossie.converter.MetricExpression.*;
import static org.apache.ossie.util.DataStructureUtils.*;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.apache.ossie.exception.ConversionException;

/** Compiles the documented metric subset: parse, bind/check, then emit bounded Tua text. */
final class MetricExpressionTranslator {
    private static final List<String> DIALECTS = List.of("TABLEAU", "SNOWFLAKE", "ANSI_SQL");
    private static final Set<String> AGGREGATES = Set.of("SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD");
    record Result(String expression, String dataType) {}
    private MetricExpressionTranslator() {}

    static Result translate(Map<String, Object> metric, Map<String, Object> sourceModel,
                            Map<String, Object> targetModel) {
        return translate(metric, new MetricFieldResolver(sourceModel, targetModel));
    }

    static Result translate(Map<String, Object> metric, MetricFieldResolver resolver) {
        String name = getString(metric, "name");
        try {
            Map<String, Object> expression = getMap(metric, "expression");
            List<Object> dialects = expression == null ? null : getList(expression, "dialects");
            if (dialects == null) {
                throw new IllegalArgumentException("missing expression.dialects; provide TABLEAU, SNOWFLAKE or ANSI_SQL");
            }
            Map<String, String> candidates = new java.util.LinkedHashMap<>();
            for (Object entry : dialects) {
                Map<String, Object> value = asMap(entry);
                String dialect = getString(value, "dialect");
                if (DIALECTS.contains(dialect)) {
                    if (candidates.containsKey(dialect)) {
                        throw new IllegalArgumentException("ambiguous expression: multiple " + dialect + " entries");
                    }
                    candidates.put(dialect, getString(value, "expression"));
                }
            }
            String dialect = DIALECTS.stream().filter(candidates::containsKey).findFirst()
                    .orElseThrow(() -> new IllegalArgumentException(
                            "no supported dialect; provide TABLEAU, SNOWFLAKE or ANSI_SQL"));
            String text = candidates.get(dialect);
            if (text == null || text.isBlank()) {
                throw new IllegalArgumentException(dialect + " expression is empty");
            }
            Node parsed = dialect.equals("TABLEAU")
                    ? new TuaMetricExpressionParser(TuaMetricExpressionParser.tokenize(text, dialect)).parse() : SqlMetricExpressionParser.parse(text, dialect);
            Typed result = new Analyzer(dialect, resolver).analyze(parsed);
            resolver.validateDatasets(result.datasets());
            Type declared = Type.of(getString(metric, "datatype"));
            if (metric.containsKey("datatype") && declared == Type.UNKNOWN) {
                throw new IllegalArgumentException("unsupported metric datatype " + getString(metric, "datatype"));
            }
            if (!result.type().numeric() && result.type() != Type.NULL) {
                throw new IllegalArgumentException("calculated measurements must be numeric, found " + result.type());
            }
            if (declared != Type.UNKNOWN && (!declared.numeric()
                    || (declared == Type.INTEGER && result.type() != Type.INTEGER && result.type() != Type.NULL))) {
                throw new IllegalArgumentException("datatype " + getString(metric, "datatype")
                        + " is incompatible with expression result " + result.type());
            }
            if (result.type() == Type.NULL && !declared.numeric()) {
                throw new IllegalArgumentException("all-null result needs an explicit numeric datatype");
            }
            if (result.level() == Level.ROW) {
                throw new IllegalArgumentException("unaggregated field in metric; use an explicit aggregate");
            }
            return new Result(new Emitter().emit(result), "Number");
        } catch (IllegalArgumentException e) {
            throw new ConversionException("Metric '" + name + "': " + e.getMessage(), e);
        }
    }

    /** Checks the complete tree before any target text is emitted. */
    private static final class Analyzer {
        private final String dialect;
        private final MetricFieldResolver resolver;
        private int depth;
        Analyzer(String dialect, MetricFieldResolver resolver) {
            this.dialect = dialect; this.resolver = resolver;
        }
        Typed analyze(Node node) {
            if (++depth > 128) throw new IllegalArgumentException("expression nesting exceeds 128 levels");
            try { return analyzeNode(node); } finally { depth--; }
        }
        private Typed analyzeNode(Node node) {
            if (node instanceof Literal literal) {
                Object value = literal.value();
                Type type = value == null ? Type.NULL : value instanceof Boolean ? Type.BOOLEAN
                        : value instanceof String ? Type.STRING
                        : ((BigDecimal) value).stripTrailingZeros().scale() <= 0 ? Type.INTEGER : Type.DECIMAL;
                return new Typed(node, type, Level.CONSTANT, Set.of(), List.of(), null,
                        value instanceof BigDecimal number ? number : null);
            }
            if (node instanceof Field field) {
                MetricFieldResolver.ResolvedField binding = resolver.resolve(field.parts(), field.tableau());
                if (binding == null || binding.expression() == null || binding.expression().isBlank()) {
                    throw new IllegalArgumentException("field resolver returned no binding");
                }
                Type type = Type.of(binding.datatype());
                if (type == Type.UNKNOWN) throw new IllegalArgumentException("field reference needs known field datatypes");
                return new Typed(node, type, Level.ROW, Set.of(binding.dataset()), List.of(), binding, null);
            }
            if (node instanceof Unary unary) {
                Typed child = analyze(unary.operand());
                String operator = unary.operator();
                Type result = child.type();
                BigDecimal number = child.number();
                if (operator.equals("ISNULL")) { result = Type.BOOLEAN; number = null; }
                else if (operator.equals("NOT")) { require(child, Type.BOOLEAN, "NOT"); result = Type.BOOLEAN; number = null; }
                else { numeric(child, "unary " + operator); if (operator.equals("-") && number != null) number = number.negate(); }
                return new Typed(node, result, child.level(), child.datasets(), List.of(child), null, number);
            }
            if (node instanceof Binary binary) {
                Typed left = analyze(binary.left()); Typed right = analyze(binary.right());
                String op = binary.operator();
                Type result;
                if (op.equals("AND") || op.equals("OR")) {
                    require(left, Type.BOOLEAN, op); require(right, Type.BOOLEAN, op); result = Type.BOOLEAN;
                } else if (Set.of("=", "!=", "<", "<=", ">", ">=").contains(op)) {
                    compatible(left.type(), right.type(), "comparison");
                    if (!Set.of("=", "!=").contains(op) && (left.type() == Type.BOOLEAN || right.type() == Type.BOOLEAN)) {
                        throw new IllegalArgumentException("ordered comparison requires numeric, text or temporal operands");
                    }
                    result = Type.BOOLEAN;
                } else {
                    numeric(left, op); numeric(right, op);
                    result = compatible(left.type(), right.type(), op);
                    if (op.equals("/")) {
                        if (right.number() != null && right.number().signum() == 0) {
                            throw new IllegalArgumentException("division by literal zero; use NULLIF(denominator, 0) for a nullable denominator");
                        }
                        result = Type.DECIMAL;
                    }
                }
                return compose(node, result, List.of(left, right));
            }
            if (node instanceof Conditional conditional) {
                List<Typed> children = new ArrayList<>();
                Type result = Type.NULL;
                for (int i = 0; i < conditional.branches().size(); i += 2) {
                    Typed predicate = analyze(conditional.branches().get(i));
                    require(predicate, Type.BOOLEAN, "conditional predicate");
                    Typed branch = analyze(conditional.branches().get(i + 1));
                    result = compatible(result, branch.type(), "conditional branches");
                    children.add(predicate); children.add(branch);
                }
                Typed otherwise = analyze(conditional.otherwise());
                result = compatible(result, otherwise.type(), "conditional branches");
                children.add(otherwise);
                return compose(node, result, children);
            }
            Call call = (Call) node;
            String name = call.name();
            if (call.distinct() && (!name.equals("COUNT") || dialect.equals("TABLEAU"))) {
                throw new IllegalArgumentException("DISTINCT is supported only by SQL COUNT(DISTINCT field)");
            }
            if (Set.of("COALESCE", "NULLIF", "CEIL").contains(name) && dialect.equals("TABLEAU")
                    || Set.of("IFNULL", "ISNULL", "CEILING", "COUNTD").contains(name) && !dialect.equals("TABLEAU")) {
                throw new IllegalArgumentException(name + " is outside the supported " + dialect + " subset");
            }
            int maximum = switch (name) {
                case "COALESCE" -> Integer.MAX_VALUE;
                case "IFNULL", "NULLIF", "ROUND" -> 2;
                case "SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD", "ISNULL", "ABS", "CEIL", "CEILING", "FLOOR" -> 1;
                default -> throw new IllegalArgumentException("unsupported function " + name);
            };
            int minimum = Set.of("COALESCE", "IFNULL", "NULLIF").contains(name) ? 2 : 1;
            if (call.arguments().size() < minimum || call.arguments().size() > maximum) {
                throw new IllegalArgumentException(name + " expects "
                        + (minimum == maximum ? minimum : minimum + " to " + maximum) + " arguments");
            }
            List<Typed> arguments = call.arguments().stream().map(this::analyze).toList();
            if (AGGREGATES.contains(name)) return aggregate(call, arguments);
            return switch (name) {
                case "COALESCE", "IFNULL" -> {
                    Type result = Type.NULL;
                    for (Typed argument : arguments) result = compatible(result, argument.type(), name + " arguments");
                    yield compose(node, result, arguments);
                }
                case "NULLIF" -> {
                    compatible(arguments.get(0).type(), arguments.get(1).type(), "NULLIF arguments");
                    yield compose(node, arguments.get(0).type(), arguments);
                }
                case "ISNULL" -> compose(node, Type.BOOLEAN, arguments);
                default -> numericFunction(call, arguments);
            };
        }

        private Typed aggregate(Call call, List<Typed> arguments) {
            String name = call.name(); Typed argument = arguments.get(0);
            if (argument.level() == Level.AGGREGATE) throw new IllegalArgumentException("nested aggregate " + name + " is unsupported");
            if (argument.datasets().isEmpty()) throw new IllegalArgumentException(name + " needs a declared field to establish its dataset");
            if (argument.datasets().size() > 1) throw new IllegalArgumentException("one aggregate cannot combine fields from multiple datasets");
            boolean count = name.equals("COUNT") || name.equals("COUNTD");
            if (count && !(argument.node() instanceof Field)) {
                throw new IllegalArgumentException(name + " requires a declared field; counting expressions is unsupported");
            }
            if (name.equals("MIN") || name.equals("MAX")) {
                if (argument.type() == Type.BOOLEAN || argument.type() == Type.UNKNOWN) {
                    throw new IllegalArgumentException(name + " requires numeric, text or temporal operands");
                }
            } else if (!count) numeric(argument, name);
            Type result = count ? Type.INTEGER : name.equals("AVG") ? Type.DECIMAL : argument.type();
            return new Typed(call, result, Level.AGGREGATE, argument.datasets(), arguments, null, null);
        }
        private Typed numericFunction(Call call, List<Typed> arguments) {
            Typed value = arguments.get(0); numeric(value, call.name());
            BigDecimal places = BigDecimal.ZERO;
            if (arguments.size() == 2) {
                places = arguments.get(1).number();
                if (places == null || places.stripTrailingZeros().scale() > 0
                        || places.compareTo(BigDecimal.valueOf(Integer.MIN_VALUE)) < 0
                        || places.compareTo(BigDecimal.valueOf(Integer.MAX_VALUE)) > 0) {
                    throw new IllegalArgumentException("ROUND precision must be a 32-bit integer literal");
                }
            }
            Type result = value.type();
            if (result != Type.NULL && (Set.of("CEIL", "CEILING", "FLOOR").contains(call.name())
                    || call.name().equals("ROUND") && places.signum() <= 0)) result = Type.INTEGER;
            return compose(call, result, arguments);
        }
        private Typed compose(Node node, Type type, List<Typed> arguments) {
            Level level = Level.CONSTANT; Set<String> datasets = new HashSet<>();
            for (Typed argument : arguments) {
                if (level != Level.CONSTANT && argument.level() != Level.CONSTANT && level != argument.level()) {
                    throw new IllegalArgumentException("cannot mix aggregate and unaggregated field expressions");
                }
                if (argument.level() != Level.CONSTANT) level = argument.level();
                datasets.addAll(argument.datasets());
            }
            return new Typed(node, type, level, datasets, arguments, null, null);
        }
        private static void numeric(Typed value, String context) {
            if (!value.type().numeric() && value.type() != Type.NULL) {
                throw new IllegalArgumentException(context + " requires numeric operands, found " + value.type() + "; declare a compatible field datatype");
            }
        }
        private static void require(Typed value, Type expected, String context) {
            if (value.type() != expected && value.type() != Type.NULL) throw new IllegalArgumentException(context + " requires " + expected + ", found " + value.type());
        }
        private static Type compatible(Type left, Type right, String context) {
            if (left == Type.UNKNOWN || right == Type.UNKNOWN) throw new IllegalArgumentException(context + " needs known field datatypes");
            if (left == Type.NULL) return right;
            if (right == Type.NULL || left == right) return left;
            if (left.numeric() && right.numeric()) return left == Type.FLOAT || right == Type.FLOAT ? Type.FLOAT : Type.DECIMAL;
            throw new IllegalArgumentException(context + " has incompatible types " + left + " and " + right);
        }
    }

    /** Streams checked nodes so NULLIF expansion cannot allocate an unbounded string. */
    private static final class Emitter {
        private static final int MAX_OUTPUT = 131072;
        private final StringBuilder output = new StringBuilder();
        String emit(Typed expression) { append(expression); return output.toString(); }
        private void text(String text) {
            if ((long) output.length() + text.length() > MAX_OUTPUT) {
                throw new IllegalArgumentException("translated expression exceeds 131072 characters");
            }
            output.append(text);
        }
        private void append(Typed value) {
            Node node = value.node(); List<Typed> children = value.children();
            if (node instanceof Literal literal) {
                Object content = literal.value();
                text(content == null ? "NULL" : content instanceof String string ? "'" + string.replace("'", "''") + "'"
                        : content instanceof Boolean bool ? bool ? "TRUE" : "FALSE" : ((BigDecimal) content).toPlainString());
            } else if (node instanceof Field) {
                text(value.binding().expression());
            } else if (node instanceof Unary unary) {
                switch (unary.operator()) {
                    case "+" -> append(children.get(0));
                    case "-" -> { text("(-"); append(children.get(0)); text(")"); }
                    case "NOT" -> { text("(NOT "); append(children.get(0)); text(")"); }
                    case "ISNULL" -> { text("ISNULL("); append(children.get(0)); text(")"); }
                    default -> throw new IllegalStateException("unvalidated unary operator");
                }
            } else if (node instanceof Binary binary) {
                text("("); append(children.get(0)); text(" " + binary.operator() + " "); append(children.get(1)); text(")");
            } else if (node instanceof Conditional) {
                text("(IF ");
                for (int i = 0; i < children.size() - 1; i += 2) {
                    if (i > 0) text(" ELSEIF ");
                    append(children.get(i)); text(" THEN "); append(children.get(i + 1));
                }
                text(" ELSE "); append(children.get(children.size() - 1)); text(" END)");
            } else {
                Call call = (Call) node;
                if (call.name().equals("COALESCE") || call.name().equals("IFNULL")) {
                    for (int i = 0; i < children.size() - 1; i++) { text("IFNULL("); append(children.get(i)); text(", "); }
                    append(children.get(children.size() - 1));
                    for (int i = 0; i < children.size() - 1; i++) text(")");
                } else if (call.name().equals("NULLIF")) {
                    text("(IF ("); append(children.get(0)); text(" = "); append(children.get(1));
                    text(") THEN NULL ELSE "); append(children.get(0)); text(" END)");
                } else {
                    text((call.distinct() ? "COUNTD" : (call.name().equals("CEIL") ? "CEILING" : call.name())) + "(");
                    for (int i = 0; i < children.size(); i++) { if (i > 0) text(", "); append(children.get(i)); }
                    text(")");
                }
            }
        }
    }
}
