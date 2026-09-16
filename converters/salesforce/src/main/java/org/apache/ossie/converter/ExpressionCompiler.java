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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/** Shared expression compiler for derived fields and metrics. Target emission follows semantic checks. */
final class ExpressionCompiler {
    private static final List<String> DIALECTS = List.of("TABLEAU", "SNOWFLAKE", "ANSI_SQL");
    private ExpressionCompiler() {}

    enum Level { CONSTANT, ROW, AGGREGATE }
    record Selected(String text, String dialect) {}
    record Parsed(ExpressionAst.Node root, String dialect) {}
    record Reference(List<MetricFieldResolver.Identifier> parts, boolean tableau) {
        Reference { parts = List.copyOf(parts); }
    }
    record Binding(String expression, String datatype, Set<String> datasets, Level level) {
        Binding { datasets = Set.copyOf(datasets); }
        Binding(String expression, String datatype, String dataset, Level level) {
            this(expression, datatype, dataset == null ? Set.of() : Set.of(dataset), level);
        }
        Binding(String expression, String datatype, String dataset) {
            this(expression, datatype, dataset, Level.ROW);
        }
        String dataset() { return datasets.size() == 1 ? datasets.iterator().next() : null; }
    }
    record Compiled(String expression, String datatype, Level level, Set<String> datasets) {
        Compiled { datasets = Set.copyOf(datasets); }
    }
    @FunctionalInterface interface ReferenceResolver { Binding resolve(Reference reference); }

    static Selected select(Map<String, Object> owner) {
        Map<String, Object> expression = getMap(owner, "expression");
        List<Object> dialects = expression == null ? null : getList(expression, "dialects");
        if (dialects == null) {
            throw new IllegalArgumentException("missing expression.dialects; provide TABLEAU, SNOWFLAKE or ANSI_SQL");
        }
        Map<String, String> candidates = new LinkedHashMap<>();
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
        if (text == null || text.isBlank()) throw new IllegalArgumentException(dialect + " expression is empty");
        return new Selected(text, dialect);
    }

    static Parsed parse(String text, String dialect) {
        if (!DIALECTS.contains(dialect)) throw new IllegalArgumentException("unsupported expression dialect " + dialect);
        if (text == null || text.isBlank()) throw new IllegalArgumentException(dialect + " expression is empty");
        List<ExpressionTokens.Token> tokens = ExpressionTokens.tokenize(text, dialect);
        ExpressionAst.Node root = dialect.equals("TABLEAU")
                ? new TuaExpressionParser(tokens).parse() : SqlExpressionParser.parse(text, dialect);
        return new Parsed(root, dialect);
    }

    static Optional<Reference> directReference(Parsed parsed) {
        return parsed.root() instanceof ExpressionAst.Field field ? Optional.of(field.reference()) : Optional.empty();
    }

    static Compiled compile(Parsed parsed, ReferenceResolver resolver) {
        ExpressionAst.Typed checked = new ExpressionAnalyzer(parsed.dialect(), resolver).analyze(parsed.root());
        String expression = new TuaExpressionEmitter().emit(checked);
        return new Compiled(expression, checked.type().datatype, checked.level(), checked.datasets());
    }
}
