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
import static org.apache.ossie.converter.ExpressionTokens.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Native Tua frontend for the bounded supported grammar; produces the same AST as SQL. */
final class TuaExpressionParser {
    private final List<Token> tokens;
    private int position;
    private int depth;
    TuaExpressionParser(List<Token> tokens) { this.tokens = tokens; }

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
        if (token.kind() == Kind.NUMBER) return new Literal(ExpressionTokens.number(token.text()));
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
        // One bracketed name is a semantic metric reference; the model resolver
        // distinguishes it from an unknown or ambiguous field. Physical fields
        // still require dataset qualification in the field resolver.
        if (parts.size() > 2) throw error("TABLEAU references must use [metric] or [dataset].[field] notation");
        return new Field(new ExpressionCompiler.Reference(parts, true));
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
}
