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

import static org.apache.ossie.converter.ExpressionAst.*;
import static org.apache.ossie.converter.ExpressionCompiler.Level;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Resolves names and checks types, aggregation level and function domains before target rendering. */
final class ExpressionAnalyzer {
    private final String dialect;
    private final ExpressionCompiler.ReferenceResolver resolver;
    private int depth;
    ExpressionAnalyzer(String dialect, ExpressionCompiler.ReferenceResolver resolver) {
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
            ExpressionCompiler.Binding binding = resolver.resolve(field.reference());
            if (binding == null || binding.expression() == null || binding.expression().isBlank()) {
                throw new IllegalArgumentException("field resolver returned no binding");
            }
            Type type = Type.of(binding.datatype());
            if (type == Type.UNKNOWN) throw new IllegalArgumentException("field reference needs known field datatypes");
            return new Typed(node, type, binding.level(), binding.datasets(), List.of(), binding, null);
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
        ExpressionFunctionRegistry.Spec spec = ExpressionFunctionRegistry.require(
                call.name(), dialect, call.arguments().size(), call.distinct());
        List<Typed> arguments = call.arguments().stream().map(this::analyze).toList();
        return switch (spec.rule()) {
            case AGGREGATE -> aggregate(call, arguments);
            case COALESCE -> {
                Type result = Type.NULL;
                for (Typed argument : arguments) result = compatible(result, argument.type(), call.name() + " arguments");
                yield compose(node, result, arguments);
            }
            case NULLIF -> {
                compatible(arguments.get(0).type(), arguments.get(1).type(), "NULLIF arguments");
                yield compose(node, arguments.get(0).type(), arguments);
            }
            case ISNULL -> compose(node, Type.BOOLEAN, arguments);
            case NUMERIC -> numericFunction(call, arguments);
            case YEAR -> {
                Type input = arguments.get(0).type();
                if (!Set.of(Type.DATE, Type.DATETIME, Type.NULL).contains(input)) {
                    throw new IllegalArgumentException("YEAR requires Date or DateTime; timezone-dependent extraction is unsupported");
                }
                yield compose(node, Type.INTEGER, arguments);
            }
            case LENGTH -> {
                require(arguments.get(0), Type.STRING, call.name());
                yield compose(node, Type.INTEGER, arguments);
            }
            case POSITION -> {
                for (Typed argument : arguments) require(argument, Type.STRING, call.name());
                yield compose(node, Type.INTEGER, arguments);
            }
            case SUBSTRING -> substring(call, arguments);
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
    private Typed substring(Call call, List<Typed> arguments) {
        require(arguments.get(0), Type.STRING, call.name());
        for (int i = 1; i < arguments.size(); i++) require(arguments.get(i), Type.INTEGER, call.name());
        // Snowflake allows non-positive indices with semantics different from Tua MID.
        // Only proven common domains are lowered, including POSITION(...) + 1 in email-domain fields.
        if (call.name().equals("SUBSTRING")) {
            BigDecimal start = lowerBound(arguments.get(1));
            if (start == null || start.signum() <= 0) throw new IllegalArgumentException("SUBSTRING start must be provably positive for Tua MID");
            if (arguments.size() == 3) {
                BigDecimal length = lowerBound(arguments.get(2));
                if (length == null || length.signum() < 0) throw new IllegalArgumentException("SUBSTRING length must be provably non-negative for Tua MID");
            }
        }
        return compose(call, Type.STRING, arguments);
    }
    private BigDecimal lowerBound(Typed value) {
        if (value.number() != null) return value.number();
        if (value.node() instanceof Call call && Set.of("LENGTH", "LEN", "POSITION", "FIND").contains(call.name())) return BigDecimal.ZERO;
        if (value.node() instanceof Binary binary && binary.operator().equals("+")) {
            BigDecimal left = lowerBound(value.children().get(0)), right = lowerBound(value.children().get(1));
            return left == null || right == null ? null : left.add(right);
        }
        return null;
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
