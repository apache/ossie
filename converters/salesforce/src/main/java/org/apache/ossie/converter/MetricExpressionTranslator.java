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

import static org.apache.ossie.util.DataStructureUtils.*;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.apache.ossie.exception.ConversionException;

/**
 * Compiles the supported metric-expression subset to Salesforce's Tua grammar.
 *
 * <p>This is deliberately an expression parser, not a SQL statement parser. Each
 * production returns a typed formula with its aggregation level; unsupported
 * syntax cannot fall through as untranslated text. See the README for the
 * relationship to the proposed Python ossie_sql engine and extension points.
 */
final class MetricExpressionTranslator {
    private static final List<String> DIALECTS = List.of("TABLEAU", "SNOWFLAKE", "ANSI_SQL");
    private static final Set<String> AGGREGATES = Set.of("SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD");

    record Result(String expression, String dataType) {}

    private MetricExpressionTranslator() {}

    static Result translate(Map<String, Object> metric, Map<String, Object> sourceModel,
                            Map<String, Object> targetModel) {
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
            MetricFieldResolver resolver = new MetricFieldResolver(sourceModel, targetModel);
            Parser parser = new Parser(text, dialect, resolver);
            Value result = parser.parse();
            resolver.validateDatasets(result.datasets);
            Type declared = type(getString(metric, "datatype"));
            if (metric.containsKey("datatype") && declared == Type.UNKNOWN) {
                throw new IllegalArgumentException("unsupported metric datatype " + getString(metric, "datatype"));
            }
            if (!result.type.numeric() && result.type != Type.NULL) {
                throw new IllegalArgumentException("calculated measurements must be numeric, found " + result.type);
            }
            if (declared != Type.UNKNOWN && (!declared.numeric()
                    || (declared == Type.INTEGER && result.type != Type.INTEGER && result.type != Type.NULL))) {
                throw new IllegalArgumentException("datatype " + getString(metric, "datatype")
                        + " is incompatible with expression result " + result.type);
            }
            if (result.type == Type.NULL && !declared.numeric()) {
                throw new IllegalArgumentException("all-null result needs an explicit numeric datatype");
            }
            if (result.level == Level.ROW) {
                throw new IllegalArgumentException("unaggregated field in metric; use an explicit aggregate");
            }
            return new Result(result.formula, "Number");
        } catch (IllegalArgumentException e) {
            throw new ConversionException("Metric '" + name + "': " + e.getMessage(), e);
        }
    }

    private enum Level { CONSTANT, ROW, AGGREGATE }

    private enum Type {
        INTEGER, DECIMAL, FLOAT, STRING, BOOLEAN, DATE, DATETIME, DATETIME_TZ, NULL, UNKNOWN;
        boolean numeric() { return this == INTEGER || this == DECIMAL || this == FLOAT; }
    }

    private static Type type(String datatype) {
        if (datatype == null) return Type.UNKNOWN;
        return switch (datatype) {
            case "Integer" -> Type.INTEGER;
            case "Decimal" -> Type.DECIMAL;
            case "Float" -> Type.FLOAT;
            case "String" -> Type.STRING;
            case "Boolean" -> Type.BOOLEAN;
            case "Date" -> Type.DATE;
            case "DateTime" -> Type.DATETIME;
            case "DateTimeTz" -> Type.DATETIME_TZ;
            default -> Type.UNKNOWN;
        };
    }

    /** A synthesized parser attribute, not a second general-purpose expression model. */
    private record Value(String formula, Type type, Level level, Set<String> datasets,
                         boolean field, BigDecimal number) {
        Value(String formula, Type type, Level level, Set<String> datasets) {
            this(formula, type, level, datasets, false, null);
        }
    }

    private enum Kind { WORD, IDENTIFIER, STRING, NUMBER, SYMBOL, END }
    private record Token(Kind kind, String text, int offset, boolean bracket) {}

    private static final class Parser {
        private final List<Token> tokens;
        private final String dialect;
        private final MetricFieldResolver resolver;
        private int position;
        private int depth;

        Parser(String expression, String dialect, MetricFieldResolver resolver) {
            this.dialect = dialect;
            this.resolver = resolver;
            this.tokens = tokenize(expression, dialect);
        }

        Value parse() {
            Value value = expression();
            if (peek().kind != Kind.END) throw error("unsupported or unexpected token '" + peek().text + "'");
            return value;
        }

        private Value expression() {
            if (++depth > 128) throw error("expression nesting exceeds 128 levels");
            Value value = or();
            depth--;
            return value;
        }

        private Value or() {
            Value value = and();
            while (take("OR")) value = binary("OR", value, and(), Type.BOOLEAN);
            return value;
        }

        private Value and() {
            Value value = not();
            while (take("AND")) value = binary("AND", value, not(), Type.BOOLEAN);
            return value;
        }

        // SQL NOT binds below comparisons, unlike the old draft's precedence table.
        private Value not() {
            int count = 0;
            while (take("NOT")) {
                if (++count > 128) throw error("too many unary operators");
            }
            Value value = comparison();
            for (int i = 0; i < count; i++) {
                require(value, Type.BOOLEAN, "NOT");
                value = new Value("(NOT " + value.formula + ")", Type.BOOLEAN, value.level, value.datasets);
            }
            return value;
        }

        private Value comparison() {
            Value value = additive();
            if (take("IS")) {
                if (dialect.equals("TABLEAU")) throw error("use ISNULL in TABLEAU expressions");
                boolean negated = take("NOT");
                expect("NULL");
                return new Value((negated ? "(NOT ISNULL(" : "ISNULL(") + value.formula
                        + (negated ? "))" : ")"), Type.BOOLEAN, value.level, value.datasets);
            }
            if (Set.of("=", "!=", "<>", "<", "<=", ">", ">=").contains(peek().text)) {
                String operator = next().text;
                Value right = additive();
                compatible(value.type, right.type, "comparison");
                if (!Set.of("=", "!=", "<>").contains(operator)
                        && (value.type == Type.BOOLEAN || right.type == Type.BOOLEAN)) {
                    throw error("ordered comparison requires numeric, text or temporal operands");
                }
                return compose("(" + value.formula + " " + (operator.equals("<>") ? "!=" : operator)
                        + " " + right.formula + ")", Type.BOOLEAN, List.of(value, right));
            }
            return value;
        }

        private Value additive() {
            Value value = multiplicative();
            while (at("+") || at("-")) {
                String operator = next().text;
                Value right = multiplicative();
                value = binary(operator, value, right, numericType(value, right, operator));
            }
            return value;
        }

        private Value multiplicative() {
            Value value = unary();
            while (at("*") || at("/")) {
                String operator = next().text;
                Value right = unary();
                if (operator.equals("/") && right.number != null && right.number.signum() == 0) {
                    throw error("division by literal zero; use NULLIF(denominator, 0) for a nullable denominator");
                }
                Type resultType = numericType(value, right, operator);
                value = binary(operator, value, right, operator.equals("/") ? Type.DECIMAL : resultType);
            }
            return value;
        }

        private Value unary() {
            List<String> signs = new ArrayList<>();
            while (at("+") || at("-")) {
                if (signs.size() >= 128) throw error("too many unary operators");
                signs.add(next().text);
            }
            Value value = primary();
            for (int i = signs.size() - 1; i >= 0; i--) {
                numeric(value, "unary " + signs.get(i));
                boolean negative = signs.get(i).equals("-");
                value = new Value(negative ? "(-" + value.formula + ")" : value.formula,
                        value.type, value.level, value.datasets, false,
                        value.number == null ? null : negative ? value.number.negate() : value.number);
            }
            return value;
        }

        private Value primary() {
            if (take("(")) {
                Value value = expression();
                expect(")");
                return value;
            }
            if (at("CASE")) {
                if (dialect.equals("TABLEAU")) throw error("searched CASE is SQL; use IF in TABLEAU");
                next();
                return conditional(false);
            }
            if (at("IF") && dialect.equals("TABLEAU")) {
                next();
                return conditional(true);
            }
            if (take("NULL")) return new Value("NULL", Type.NULL, Level.CONSTANT, Set.of());
            if (at("TRUE") || at("FALSE")) {
                return new Value(next().text.toUpperCase(Locale.ROOT), Type.BOOLEAN, Level.CONSTANT, Set.of());
            }
            Token token = next();
            if (token.kind == Kind.NUMBER) {
                BigDecimal number;
                try { number = new BigDecimal(token.text); }
                catch (NumberFormatException e) { throw error("invalid numeric literal '" + token.text + "'"); }
                if (Math.abs((long) number.scale()) > 1000 || number.precision() > 1000) {
                    throw error("numeric literal is too large");
                }
                Type datatype = number.stripTrailingZeros().scale() <= 0 ? Type.INTEGER : Type.DECIMAL;
                return new Value(number.toPlainString(), datatype, Level.CONSTANT, Set.of(), false, number);
            }
            if (token.kind == Kind.STRING) {
                return new Value("'" + token.text.replace("'", "''") + "'", Type.STRING, Level.CONSTANT, Set.of());
            }
            if (token.kind != Kind.WORD && token.kind != Kind.IDENTIFIER) {
                throw error("expected a value, found '" + token.text + "'");
            }
            if (take("(")) {
                if (token.kind != Kind.WORD) throw error("quoted function names are unsupported");
                return function(token.text.toUpperCase(Locale.ROOT));
            }
            List<MetricFieldResolver.Identifier> parts = new ArrayList<>();
            addIdentifier(parts, token);
            boolean bracketed = token.bracket;
            while (take(".")) {
                Token part = next();
                if (bracketed != part.bracket) throw error("do not mix bracketed and SQL field identifiers");
                addIdentifier(parts, part);
            }
            // Existing ANSI_SQL fixtures use complete Tableau field notation. Keep
            // that narrow compatibility spelling, with the same exact-name checks.
            MetricFieldResolver.ResolvedField field = resolver.resolve(parts, bracketed);
            return new Value(field.expression(), type(field.datatype()), Level.ROW,
                    Set.of(field.dataset()), true, null);
        }

        private void addIdentifier(List<MetricFieldResolver.Identifier> parts, Token token) {
            if (token.kind != Kind.WORD && token.kind != Kind.IDENTIFIER) throw error("expected field identifier");
            if (dialect.equals("TABLEAU") && !token.bracket) {
                throw error("TABLEAU fields must use [dataset].[field] notation");
            }
            parts.add(new MetricFieldResolver.Identifier(token.text, token.kind == Kind.IDENTIFIER));
        }

        private Value conditional(boolean tableau) {
            List<Value> values = new ArrayList<>();
            StringBuilder formula = new StringBuilder("(IF ");
            Type resultType = Type.NULL;
            boolean first = true;
            do {
                if (!first) formula.append(" ELSEIF ");
                if (!tableau) expect("WHEN");
                Value condition = expression();
                require(condition, Type.BOOLEAN, "conditional predicate");
                expect("THEN");
                Value branch = expression();
                resultType = compatible(resultType, branch.type, "conditional branches");
                values.add(condition);
                values.add(branch);
                formula.append(condition.formula).append(" THEN ").append(branch.formula);
                first = false;
            } while (tableau ? take("ELSEIF") : at("WHEN"));
            Value otherwise = take("ELSE") ? expression() : new Value("NULL", Type.NULL, Level.CONSTANT, Set.of());
            expect("END");
            resultType = compatible(resultType, otherwise.type, "conditional branches");
            values.add(otherwise);
            formula.append(" ELSE ").append(otherwise.formula).append(" END)");
            return compose(formula.toString(), resultType, values);
        }

        private Value function(String name) {
            if (++depth > 128) throw error("expression nesting exceeds 128 levels");
            boolean distinct = take("DISTINCT");
            if (at("*")) throw error("COUNT(*) is unsupported; name a declared field to count");
            List<Value> arguments = new ArrayList<>();
            if (!at(")")) {
                do { arguments.add(expression()); } while (take(","));
            }
            expect(")");
            depth--;
            if (distinct && (!name.equals("COUNT") || dialect.equals("TABLEAU"))) {
                throw error("DISTINCT is supported only by SQL COUNT(DISTINCT field)");
            }
            if (AGGREGATES.contains(name)) {
                if (name.equals("COUNTD") && !dialect.equals("TABLEAU")) throw error("use COUNT(DISTINCT field) in SQL");
                arity(name, arguments, 1, 1);
                Value argument = arguments.get(0);
                if (argument.level == Level.AGGREGATE) throw error("nested aggregate " + name + " is unsupported");
                if (argument.datasets.isEmpty()) throw error(name + " needs a declared field to establish its dataset");
                if (argument.datasets.size() > 1) throw error("one aggregate cannot combine fields from multiple datasets");
                boolean count = name.equals("COUNT") || name.equals("COUNTD");
                if (count && !argument.field) throw error(name + " requires a declared field; counting expressions is unsupported");
                if (name.equals("MIN") || name.equals("MAX")) {
                    if (argument.type == Type.BOOLEAN || argument.type == Type.UNKNOWN) {
                        throw error(name + " requires numeric, text or temporal operands");
                    }
                } else if (!count) numeric(argument, name);
                return new Value((distinct ? "COUNTD" : name) + "(" + argument.formula + ")",
                        count ? Type.INTEGER : name.equals("AVG") ? Type.DECIMAL : argument.type,
                        Level.AGGREGATE, argument.datasets);
            }
            if (Set.of("COALESCE", "NULLIF").contains(name) && dialect.equals("TABLEAU")) {
                throw error(name + " is SQL syntax; use IFNULL or IF in TABLEAU");
            }
            if (Set.of("IFNULL", "ISNULL", "CEILING").contains(name) && !dialect.equals("TABLEAU")) {
                throw error(name + " is outside the supported SQL subset");
            }
            return switch (name) {
                case "COALESCE", "IFNULL" -> coalesce(name, arguments);
                case "NULLIF" -> nullif(arguments);
                case "ISNULL" -> {
                    arity(name, arguments, 1, 1);
                    yield compose(call(name, arguments), Type.BOOLEAN, arguments);
                }
                case "ABS", "CEIL", "CEILING", "FLOOR", "ROUND" -> numericFunction(name, arguments);
                default -> throw error("unsupported function " + name);
            };
        }

        private Value coalesce(String name, List<Value> arguments) {
            arity(name, arguments, 2, name.equals("IFNULL") ? 2 : Integer.MAX_VALUE);
            Type resultType = Type.NULL;
            for (Value argument : arguments) resultType = compatible(resultType, argument.type, name + " arguments");
            String formula = arguments.get(arguments.size() - 1).formula;
            for (int i = arguments.size() - 2; i >= 0; i--) formula = "IFNULL(" + arguments.get(i).formula + ", " + formula + ")";
            return compose(formula, resultType, arguments);
        }

        private Value nullif(List<Value> arguments) {
            arity("NULLIF", arguments, 2, 2);
            Value left = arguments.get(0);
            Value right = arguments.get(1);
            compatible(left.type, right.type, "NULLIF arguments");
            return compose("(IF (" + left.formula + " = " + right.formula + ") THEN NULL ELSE "
                    + left.formula + " END)", left.type, arguments);
        }

        private Value numericFunction(String name, List<Value> arguments) {
            if (name.equals("CEIL") && dialect.equals("TABLEAU")) throw error("use CEILING in TABLEAU");
            arity(name, arguments, 1, name.equals("ROUND") ? 2 : 1);
            Value value = arguments.get(0);
            numeric(value, name);
            if (arguments.size() == 2) {
                BigDecimal places = arguments.get(1).number;
                if (places == null || places.stripTrailingZeros().scale() > 0
                        || places.compareTo(BigDecimal.valueOf(Integer.MIN_VALUE)) < 0
                        || places.compareTo(BigDecimal.valueOf(Integer.MAX_VALUE)) > 0) {
                    throw error("ROUND precision must be a 32-bit integer literal");
                }
            }
            String target = name.equals("CEIL") ? "CEILING" : name;
            return compose(call(target, arguments), value.type, arguments);
        }

        private String call(String name, List<Value> arguments) {
            return name + "(" + String.join(", ", arguments.stream().map(Value::formula).toList()) + ")";
        }

        private void arity(String name, List<Value> arguments, int min, int max) {
            if (arguments.size() < min || arguments.size() > max) {
                throw error(name + " expects " + (min == max ? min : min + " to " + max) + " arguments");
            }
        }

        private Value binary(String operator, Value left, Value right, Type resultType) {
            if (operator.equals("AND") || operator.equals("OR")) {
                require(left, Type.BOOLEAN, operator);
                require(right, Type.BOOLEAN, operator);
            }
            return compose("(" + left.formula + " " + operator + " " + right.formula + ")",
                    resultType, List.of(left, right));
        }

        private Type numericType(Value left, Value right, String context) {
            numeric(left, context);
            numeric(right, context);
            return compatible(left.type, right.type, context);
        }

        private void numeric(Value value, String context) {
            if (!value.type.numeric() && value.type != Type.NULL) {
                throw error(context + " requires numeric operands, found " + value.type
                        + "; declare a compatible field datatype");
            }
        }

        private void require(Value value, Type expected, String context) {
            if (value.type != expected && value.type != Type.NULL) throw error(context + " requires " + expected + ", found " + value.type);
        }

        private Type compatible(Type left, Type right, String context) {
            if (left == Type.UNKNOWN || right == Type.UNKNOWN) throw error(context + " needs known field datatypes");
            if (left == Type.NULL) return right;
            if (right == Type.NULL || left == right) return left;
            if (left.numeric() && right.numeric()) {
                if (left == Type.FLOAT || right == Type.FLOAT) return Type.FLOAT;
                return Type.DECIMAL;
            }
            throw error(context + " has incompatible types " + left + " and " + right);
        }

        private Value compose(String formula, Type type, List<Value> arguments) {
            Level level = Level.CONSTANT;
            Set<String> datasets = new HashSet<>();
            for (Value argument : arguments) {
                if (level != Level.CONSTANT && argument.level != Level.CONSTANT && level != argument.level) {
                    throw error("cannot mix aggregate and unaggregated field expressions");
                }
                if (argument.level != Level.CONSTANT) level = argument.level;
                datasets.addAll(argument.datasets);
            }
            // NULLIF duplicates its first operand. Bound expansion as well as input size.
            if (formula.length() > 131072) throw error("translated expression exceeds 131072 characters");
            return new Value(formula, type, level, Set.copyOf(datasets));
        }

        private Token peek() { return tokens.get(position); }
        private Token next() { Token token = peek(); if (token.kind != Kind.END) position++; return token; }
        private boolean at(String text) {
            return (peek().kind == Kind.WORD || peek().kind == Kind.SYMBOL) && peek().text.equalsIgnoreCase(text);
        }
        private boolean take(String text) { if (!at(text)) return false; next(); return true; }
        private void expect(String text) { if (!take(text)) throw error("expected " + text + ", found '" + peek().text + "'"); }
        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(dialect + " at character " + (peek().offset + 1) + ": " + message);
        }
    }

    private static List<Token> tokenize(String text, String dialect) {
        if (text.length() > 32768) throw new IllegalArgumentException("expression exceeds 32768 characters");
        List<Token> tokens = new ArrayList<>();
        for (int i = 0; i < text.length();) {
            char c = text.charAt(i);
            if (Character.isWhitespace(c)) { i++; continue; }
            int start = i;
            if (c == '\'' || c == '"' || c == '[') {
                if (c == '[' && dialect.equals("SNOWFLAKE")) throw lexical(dialect, i, "use double-quoted SQL identifiers");
                boolean string = c == '\'' || (c == '"' && dialect.equals("TABLEAU"));
                char end = c == '[' ? ']' : c;
                StringBuilder value = new StringBuilder();
                boolean closed = false;
                i++;
                while (i < text.length()) {
                    char part = text.charAt(i++);
                    if (part == end) {
                        if (i < text.length() && text.charAt(i) == end) { value.append(end); i++; }
                        else { closed = true; break; }
                    } else {
                        if (Character.isISOControl(part) || (string && part == '\\')) {
                            throw lexical(dialect, i - 1, "control characters and backslash string escapes are unsupported");
                        }
                        value.append(part);
                    }
                }
                if (!closed) throw lexical(dialect, start, "unterminated quoted value");
                tokens.add(new Token(string ? Kind.STRING : Kind.IDENTIFIER, value.toString(), start, c == '['));
            } else if (Character.isDigit(c) || (c == '.' && i + 1 < text.length() && Character.isDigit(text.charAt(i + 1)))) {
                i++;
                while (i < text.length() && (Character.isDigit(text.charAt(i)) || text.charAt(i) == '.')) i++;
                if (i < text.length() && (text.charAt(i) == 'e' || text.charAt(i) == 'E')) {
                    i++;
                    if (i < text.length() && (text.charAt(i) == '+' || text.charAt(i) == '-')) i++;
                    while (i < text.length() && Character.isDigit(text.charAt(i))) i++;
                }
                tokens.add(new Token(Kind.NUMBER, text.substring(start, i), start, false));
            } else if (Character.isLetter(c) || c == '_') {
                i++;
                while (i < text.length() && (Character.isLetterOrDigit(text.charAt(i)) || text.charAt(i) == '_' || text.charAt(i) == '$')) i++;
                tokens.add(new Token(Kind.WORD, text.substring(start, i), start, false));
            } else {
                if (i + 1 < text.length() && (text.startsWith("--", i) || text.startsWith("/*", i))) {
                    throw lexical(dialect, i, "comments are unsupported in metric expressions");
                }
                String symbol = String.valueOf(c);
                if (i + 1 < text.length() && Set.of("<=", ">=", "<>", "!=").contains(text.substring(i, i + 2))) {
                    symbol = text.substring(i, i + 2);
                    i++;
                }
                if (!"()+-*/.,=<>!".contains(String.valueOf(c))) throw lexical(dialect, start, "unsupported character '" + c + "'");
                tokens.add(new Token(Kind.SYMBOL, symbol, start, false));
                i++;
            }
            if (tokens.size() > 8192) throw lexical(dialect, start, "too many expression tokens");
        }
        tokens.add(new Token(Kind.END, "end of expression", text.length(), false));
        return tokens;
    }

    private static IllegalArgumentException lexical(String dialect, int offset, String message) {
        return new IllegalArgumentException(dialect + " at character " + (offset + 1) + ": " + message);
    }
}
