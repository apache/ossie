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
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.apache.ossie.exception.ConversionException;

/** Per-model metric dependency plan. References are checked before aggregate formulas are inlined. */
final class MetricCompilationPlan {
    private static final int MAX_DEPENDENCY_DEPTH = 128;
    private final MetricFieldResolver fields;
    private final Map<String, Map<String, Object>> declarations = new LinkedHashMap<>();
    private final Map<String, List<String>> sqlNames = new LinkedHashMap<>();
    private final Map<String, ExpressionCompiler.Compiled> compiled = new LinkedHashMap<>();
    private final Set<String> visiting = new LinkedHashSet<>();

    MetricCompilationPlan(Map<String, Object> source, MetricFieldResolver fields) {
        this.fields = fields;
        List<Object> metrics = getList(source, "metrics");
        if (metrics == null) return;
        for (Object value : metrics) {
            Map<String, Object> metric = asMap(value);
            String name = getString(metric, "name");
            if (name == null || name.isBlank()) throw new ConversionException("Metric name must not be empty");
            if (declarations.putIfAbsent(name, metric) != null) {
                throw new ConversionException("Metric '" + name + "': duplicate metric name");
            }
            sqlNames.computeIfAbsent(MetricFieldResolver.normalizeDeclaration(name), key -> new ArrayList<>()).add(name);
        }
    }

    ExpressionCompiler.Compiled compile(String name) {
        ExpressionCompiler.Compiled cached = compiled.get(name);
        if (cached != null) return cached;
        if (visiting.contains(name)) {
            throw new ConversionException("Metric dependency cycle: " + String.join(" -> ", visiting) + " -> " + name);
        }
        if (visiting.size() >= MAX_DEPENDENCY_DEPTH) {
            throw new ConversionException("Metric '" + name + "': dependency depth exceeds " + MAX_DEPENDENCY_DEPTH);
        }
        Map<String, Object> metric = declarations.get(name);
        if (metric == null) throw new ConversionException("Unknown metric '" + name + "'");
        visiting.add(name);
        try {
            ExpressionCompiler.Compiled result = MetricExpressionTranslator.compile(metric, this::resolve);
            try { fields.validateDatasets(result.datasets()); }
            catch (IllegalArgumentException e) { throw new ConversionException("Metric '" + name + "': " + e.getMessage(), e); }
            compiled.put(name, result);
            return result;
        } finally {
            visiting.remove(name);
        }
    }

    private ExpressionCompiler.Binding resolve(ExpressionCompiler.Reference reference) {
        if (reference.parts().size() == 1) {
            MetricFieldResolver.Identifier identifier = reference.parts().get(0);
            List<String> matches = reference.tableau()
                    ? (declarations.containsKey(identifier.text()) ? List.of(identifier.text()) : List.of())
                    : sqlNames.getOrDefault(MetricFieldResolver.normalize(identifier), List.of());
            if (!matches.isEmpty()) {
                if (matches.size() != 1 || fields.hasUnqualifiedField(identifier, reference.tableau())) {
                    throw new IllegalArgumentException("ambiguous field or metric reference '" + identifier.text()
                            + "'; qualify the field or use a unique metric name");
                }
                ExpressionCompiler.Compiled metric = compile(matches.get(0));
                return new ExpressionCompiler.Binding("(" + metric.expression() + ")", metric.datatype(),
                        metric.datasets(), metric.level());
            }
        }
        return fields.resolveBinding(reference);
    }
}
