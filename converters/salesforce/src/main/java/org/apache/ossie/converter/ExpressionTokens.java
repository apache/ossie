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

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Bounded lexical preflight shared by the SQL and native Tua frontends. */
final class ExpressionTokens {
    enum Kind { WORD, IDENTIFIER, STRING, NUMBER, SYMBOL, END }
    record Token(Kind kind, String text, int offset, boolean bracket) {}
    private ExpressionTokens() {}
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
