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
import java.util.List;
import java.util.Set;

/** Immutable compiler nodes. Neither source parsing nor type checking emits target text. */
final class ExpressionAst {
    private ExpressionAst() {}
    sealed interface Node permits Literal, Field, Unary, Binary, Call, Conditional {}
    record Literal(Object value) implements Node {}
    record Field(ExpressionCompiler.Reference reference) implements Node {}
    record Unary(String operator, Node operand) implements Node {}
    record Binary(String operator, Node left, Node right) implements Node {}
    record Call(String name, List<Node> arguments, boolean distinct) implements Node {
        Call { arguments = List.copyOf(arguments); }
    }
    /** Alternating predicate/result pairs, followed by a separate ELSE expression. */
    record Conditional(List<Node> branches, Node otherwise) implements Node {
        Conditional { branches = List.copyOf(branches); }
    }
    enum Type {
        INTEGER("Integer"), DECIMAL("Decimal"), FLOAT("Float"), STRING("String"),
        BOOLEAN("Boolean"), DATE("Date"), DATETIME("DateTime"), DATETIME_TZ("DateTimeTz"),
        NULL(null), UNKNOWN(null);
        final String datatype;
        Type(String datatype) { this.datatype = datatype; }
        boolean numeric() { return this == INTEGER || this == DECIMAL || this == FLOAT; }
        static Type of(String datatype) {
            if (datatype == null) return UNKNOWN;
            for (Type type : values()) if (datatype.equals(type.datatype)) return type;
            return UNKNOWN;
        }
    }
    record Typed(Node node, Type type, ExpressionCompiler.Level level, Set<String> datasets,
                 List<Typed> children, ExpressionCompiler.Binding binding, BigDecimal number) {
        Typed { datasets = Set.copyOf(datasets); children = List.copyOf(children); }
    }
}
