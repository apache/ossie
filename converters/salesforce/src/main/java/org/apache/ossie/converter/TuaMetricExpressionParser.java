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
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Native Tua frontend for the bounded supported grammar; produces the same AST as SQL. */
final class TuaMetricExpressionParser {
    private final List<Token> tokens;
    private int position;
    private int depth;
    TuaMetricExpressionParser(List<Token> tokens) { this.tokens = tokens; }

    Node parse() {
        Node node = expression();
        if (peek().kind() != Kind.END) throw error("unsupported or unexpected token '" + peek().text() + "'");
        return node;
    }
    private Node expression() {
        if (++depth > 128) throw error("expression nesting exceeds 128 levels");
        try { return or(); } finally { depth--; }
    }
    private Node or() {
        Node value = and();
        while (take("OR")) value = new Binary("OR", value, and());
        return value;
    }
    private Node and() {
        Node value = not();
        while (take("AND")) value = new Binary("AND", value, not());
        return value;
    }
    private Node not() {
        int count = 0;
        while (take("NOT")) if (++count > 128) throw error("too many unary operators");
        Node value = comparison();
        while (count-- > 0) value = new Unary("NOT", value);
        return value;
    }
    private Node comparison() {
        Node value = additive();
        if (at("IS")) throw error("use ISNULL in TABLEAU expressions");
        if (Set.of("=", "!=", "<>", "<", "<=", ">", ">=").contains(peek().text())) {
            String op = next().text();
            return new Binary(op.equals("<>") ? "!=" : op, value, additive());
        }
        return value;
    }
    private Node additive() {
        Node value = multiplicative();
        while (at("+") || at("-")) value = new Binary(next().text(), value, multiplicative());
        return value;
    }
    private Node multiplicative() {
        Node value = unary();
        while (at("*") || at("/")) value = new Binary(next().text(), value, unary());
        return value;
    }
    private Node unary() {
        List<String> signs = new ArrayList<>();
        while (at("+") || at("-")) {
            if (signs.size() >= 128) throw error("too many unary operators");
            signs.add(next().text());
        }
        Node value = primary();
        for (int i = signs.size() - 1; i >= 0; i--) value = new Unary(signs.get(i), value);
        return value;
    }
    private Node primary() {
        if (take("(")) { Node value = expression(); expect(")"); return value; }
        if (at("CASE")) throw error("searched CASE is SQL; use IF in TABLEAU");
        if (take("IF")) return conditional();
        if (take("NULL")) return new Literal(null);
        if (at("TRUE") || at("FALSE")) return new Literal(Boolean.valueOf(next().text()));
        Token token = next();
        if (token.kind() == Kind.NUMBER) return new Literal(number(token.text()));
        if (token.kind() == Kind.STRING) return new Literal(token.text());
        if (token.kind() != Kind.WORD && token.kind() != Kind.IDENTIFIER) {
            throw error("expected a value, found '" + token.text() + "'");
        }
        if (take("(")) {
            if (token.kind() != Kind.WORD) throw error("quoted function names are unsupported");
            return function(token.text().toUpperCase(Locale.ROOT));
        }
        List<MetricFieldResolver.Identifier> parts = new ArrayList<>();
        addIdentifier(parts, token);
        while (take(".")) addIdentifier(parts, next());
        if (parts.size() != 2) throw error("TABLEAU fields must use [dataset].[field] notation");
        return new Field(parts, true);
    }
    private void addIdentifier(List<MetricFieldResolver.Identifier> parts, Token token) {
        if (token.kind() != Kind.IDENTIFIER || !token.bracket()) {
            throw error("TABLEAU fields must use [dataset].[field] notation");
        }
        parts.add(new MetricFieldResolver.Identifier(token.text(), true));
    }
    private Node conditional() {
        List<Node> branches = new ArrayList<>();
        do {
            branches.add(expression()); expect("THEN"); branches.add(expression());
        } while (take("ELSEIF"));
        Node otherwise = take("ELSE") ? expression() : new Literal(null);
        expect("END");
        return new Conditional(branches, otherwise);
    }
    private Node function(String name) {
        boolean distinct = take("DISTINCT");
        if (at("*")) throw error("COUNT(*) is unsupported; name a declared field to count");
        List<Node> arguments = new ArrayList<>();
        if (!at(")")) do { arguments.add(expression()); } while (take(","));
        expect(")");
        return new Call(name, arguments, distinct);
    }
    private Token peek() { return tokens.get(position); }
    private Token next() { Token token = peek(); if (token.kind() != Kind.END) position++; return token; }
    private boolean at(String text) {
        return (peek().kind() == Kind.WORD || peek().kind() == Kind.SYMBOL) && peek().text().equalsIgnoreCase(text);
    }
    private boolean take(String text) { if (!at(text)) return false; next(); return true; }
    private void expect(String text) { if (!take(text)) throw error("expected " + text + ", found '" + peek().text() + "'"); }
    private IllegalArgumentException error(String message) {
        return new IllegalArgumentException("TABLEAU at character " + (peek().offset() + 1) + ": " + message);
    }

    // Preflight bounds SQL parsing too; native parsing also consumes these tokens.
    enum Kind { WORD, IDENTIFIER, STRING, NUMBER, SYMBOL, END }
    record Token(Kind kind, String text, int offset, boolean bracket) {}
    static List<Token> tokenize(String text, String dialect) {
        if (text.length() > 32768) throw new IllegalArgumentException("expression exceeds 32768 characters");
        List<Token> tokens = new ArrayList<>();
        int nesting = 0;
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
            Token added = tokens.get(tokens.size() - 1);
            if (added.kind() == Kind.NUMBER) number(added.text());
            if (added.kind() == Kind.SYMBOL && added.text().equals("(") && ++nesting > 128) {
                throw lexical(dialect, start, "expression nesting exceeds 128 levels");
            }
            if (added.kind() == Kind.SYMBOL && added.text().equals(")")) nesting--;
            if (tokens.size() > 8192) throw lexical(dialect, start, "too many expression tokens");
        }
        if (nesting > 0) throw lexical(dialect, text.length(), "expected )");
        tokens.add(new Token(Kind.END, "end of expression", text.length(), false));
        return tokens;
    }

    private static IllegalArgumentException lexical(String dialect, int offset, String message) {
        return new IllegalArgumentException(dialect + " at character " + (offset + 1) + ": " + message);
    }
    static BigDecimal number(String text) {
        BigDecimal value;
        try { value = new BigDecimal(text); }
        catch (NumberFormatException e) { throw new IllegalArgumentException("invalid numeric literal '" + text + "'"); }
        if (Math.abs((long) value.scale()) > 1000 || value.precision() > 1000) {
            throw new IllegalArgumentException("numeric literal is too large");
        }
        return value;
    }
}
