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
import java.math.BigDecimal;
import java.util.List;

/** Emits only checked AST nodes into the target grammar, with bounded expansion. */
final class TuaExpressionEmitter {
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
            ExpressionFunctionRegistry.Spec spec = ExpressionFunctionRegistry.get(call.name());
            if (spec.rule() == ExpressionFunctionRegistry.Rule.COALESCE) {
                for (int i = 0; i < children.size() - 1; i++) { text("IFNULL("); append(children.get(i)); text(", "); }
                append(children.get(children.size() - 1));
                for (int i = 0; i < children.size() - 1; i++) text(")");
            } else if (spec.rule() == ExpressionFunctionRegistry.Rule.NULLIF) {
                text("(IF ("); append(children.get(0)); text(" = "); append(children.get(1));
                text(") THEN NULL ELSE "); append(children.get(0)); text(" END)");
            } else {
                text((call.distinct() ? "COUNTD" : spec.target()) + "(");
                if (call.name().equals("POSITION")) {
                    append(children.get(1)); text(", "); append(children.get(0));
                } else {
                    for (int i = 0; i < children.size(); i++) { if (i > 0) text(", "); append(children.get(i)); }
                }
                text(")");
            }
        }
    }
}
