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
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import net.sf.jsqlparser.expression.*;
import net.sf.jsqlparser.expression.operators.relational.IsNullExpression;
import net.sf.jsqlparser.expression.operators.relational.ParenthesedExpressionList;
import net.sf.jsqlparser.parser.CCJSqlParserUtil;
import net.sf.jsqlparser.schema.Column;
import net.sf.jsqlparser.statement.select.AllColumns;

/** Adapts a completely consumed JSqlParser expression into the explicitly supported compiler AST. */
final class SqlMetricExpressionParser {
    private final String dialect;
    private int depth;
    private SqlMetricExpressionParser(String dialect) { this.dialect = dialect; }

    static Node parse(String text, String dialect) {
        TuaMetricExpressionParser.tokenize(text, dialect);
        try {
            Expression expression = CCJSqlParserUtil.parseCondExpression(text, false,
                    parser -> parser.withSquareBracketQuotation(dialect.equals("ANSI_SQL")));
            if (expression == null) throw new IllegalArgumentException("could not parse a complete SQL expression");
            return new SqlMetricExpressionParser(dialect).adapt(expression);
        } catch (net.sf.jsqlparser.JSQLParserException e) {
            throw new IllegalArgumentException(dialect + " expression has unsupported or unexpected token: "
                    + e.getMessage(), e);
        } catch (StackOverflowError e) {
            throw new IllegalArgumentException("expression nesting exceeds parser limits", e);
        }
    }

    private Node adapt(Expression expression) {
        if (++depth > 128) throw new IllegalArgumentException("expression nesting exceeds 128 levels");
        try { return adaptNode(expression); }
        finally { depth--; }
    }

    private Node adaptNode(Expression expression) {
        if (expression instanceof ParenthesedExpressionList<?> list && list.size() == 1) {
            return adapt(list.get(0));
        }
        if (expression instanceof LongValue || expression instanceof DoubleValue) {
            return new Literal(TuaMetricExpressionParser.number(expression.toString()));
        }
        if (expression instanceof NullValue) return new Literal(null);
        if (expression instanceof BooleanValue value) return new Literal(value.getValue());
        if (expression instanceof StringValue value) {
            if (value.getPrefix() != null) throw unsupported(expression);
            return new Literal(value.getValue().replace("''", "'"));
        }
        if (expression instanceof Column column) return column(column);
        if (expression instanceof SignedExpression signed) {
            if (signed.getSign() != '+' && signed.getSign() != '-') throw unsupported(expression);
            return new Unary(String.valueOf(signed.getSign()), adapt(signed.getExpression()));
        }
        if (expression instanceof NotExpression not) {
            if (not.isExclamationMark()) throw unsupported(expression);
            return new Unary("NOT", adapt(not.getExpression()));
        }
        if (expression instanceof IsNullExpression test) {
            if (test.isUseIsNull() || test.isUseNotNull()) throw unsupported(expression);
            Node result = new Unary("ISNULL", adapt(test.getLeftExpression()));
            return test.isNot() ? new Unary("NOT", result) : result;
        }
        if (expression instanceof BinaryExpression binary) {
            if (binary instanceof net.sf.jsqlparser.expression.operators.relational.SupportsOldOracleJoinSyntax oracle
                    && (oracle.getOldOracleJoinSyntax() != 0 || oracle.getOraclePriorPosition() != 0)) {
                throw unsupported(expression);
            }
            String operator = binary.getStringExpression().toUpperCase(Locale.ROOT);
            if (!Set.of("+", "-", "*", "/", "AND", "OR", "=", "!=", "<>", "<", ">", "<=", ">=").contains(operator)) {
                throw unsupported(expression);
            }
            return new Binary(operator.equals("<>") ? "!=" : operator,
                    adapt(binary.getLeftExpression()), adapt(binary.getRightExpression()));
        }
        if (expression instanceof Function function) return function(function);
        if (expression instanceof CaseExpression conditional) {
            List<Node> branches = new ArrayList<>();
            // The original bounded contract supports searched CASE. Simple CASE can be
            // added with explicit type checking and evaluation-count guarantees later.
            if (conditional.getSwitchExpression() != null) throw unsupported(expression);
            for (WhenClause branch : conditional.getWhenClauses()) {
                branches.add(adapt(branch.getWhenExpression()));
                branches.add(adapt(branch.getThenExpression()));
            }
            return new Conditional(branches, conditional.getElseExpression() == null
                    ? new Literal(null) : adapt(conditional.getElseExpression()));
        }
        if (expression instanceof AllColumns) {
            throw new IllegalArgumentException("COUNT(*) is unsupported; name a declared field to count");
        }
        throw unsupported(expression);
    }

    private Node function(Function function) {
        String name = function.getName();
        if (name == null || !name.matches("[A-Za-z_][A-Za-z_0-9]*")) {
            throw new IllegalArgumentException("quoted or qualified function names are unsupported");
        }
        if (function.isUnique() || function.isEscaped() || function.getNamedParameters() != null
                || function.getAttribute() != null || function.getKeep() != null
                || function.getNullHandling() != null || function.isIgnoreNullsOutside()
                || function.isIgnoreNulls() || function.getLimit() != null
                || function.getHavingClause() != null || function.getExtraKeyword() != null
                || function.getOnOverflowTruncate() != null
                || function.getOrderByElements() != null && !function.getOrderByElements().isEmpty()) {
            throw new IllegalArgumentException("unsupported function modifiers for " + name);
        }
        if (function.isAllColumns()) {
            throw new IllegalArgumentException("explicit ALL function modifier is outside the supported SQL subset");
        }
        List<Node> arguments = new ArrayList<>();
        if (function.getParameters() != null) {
            for (Expression argument : function.getParameters()) arguments.add(adapt(argument));
        }
        return new Call(name.toUpperCase(Locale.ROOT), arguments, function.isDistinct());
    }

    private Node column(Column column) {
        if (column.getArrayConstructor() != null) throw unsupported(column);
        // JSqlParser 5.3's Table accessors split a quoted name containing a dot
        // ("Order.Items") into schema/table parts. Retain the original token
        // boundaries instead of binding that expression to a different object.
        List<String> raw = new ArrayList<>();
        var source = column.getASTNode();
        if (source == null) throw new IllegalArgumentException("field reference has no source identifier tokens");
        boolean identifier = true;
        for (var token = source.jjtGetFirstToken(); token != null; token = token.next) {
            if (identifier) raw.add(token.image);
            else if (!token.image.equals(".")) throw unsupported(column);
            identifier = !identifier;
            if (token == source.jjtGetLastToken()) break;
        }
        if (raw.isEmpty() || identifier) throw unsupported(column);
        boolean bracket = raw.get(0).startsWith("[");
        List<MetricFieldResolver.Identifier> parts = new ArrayList<>();
        for (String part : raw) {
            if (bracket != part.startsWith("[")) {
                throw new IllegalArgumentException("do not mix bracketed and SQL field identifiers");
            }
            boolean quoted = part.startsWith("\"") || part.startsWith("[");
            if (quoted) {
                String end = bracket ? "]" : "\"";
                part = part.substring(1, part.length() - 1).replace(end + end, end);
            }
            parts.add(new MetricFieldResolver.Identifier(part, quoted));
        }
        return new Field(parts, bracket);
    }

    private IllegalArgumentException unsupported(Expression expression) {
        return new IllegalArgumentException(dialect + " unsupported SQL expression "
                + expression.getClass().getSimpleName()
                + "; only documented expression capabilities can be converted");
    }
}
