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

import static org.apache.ossie.util.DataStructureUtils.getString;
import java.util.Map;
import org.apache.ossie.exception.ConversionException;

/** Applies measurement-specific constraints around the shared expression compiler. */
final class MetricExpressionTranslator {
    record Result(String expression, String dataType) {}
    private MetricExpressionTranslator() {}

    static Result translate(Map<String, Object> metric, Map<String, Object> sourceModel,
                            Map<String, Object> targetModel) {
        return translate(metric, new MetricFieldResolver(sourceModel, targetModel));
    }

    static Result translate(Map<String, Object> metric, MetricFieldResolver resolver) {
        ExpressionCompiler.Compiled result = compile(metric, resolver::resolveBinding);
        try {
            resolver.validateDatasets(result.datasets());
        } catch (IllegalArgumentException e) {
            throw new ConversionException("Metric '" + getString(metric, "name") + "': " + e.getMessage(), e);
        }
        return new Result(result.expression(), "Number");
    }

    static ExpressionCompiler.Compiled compile(Map<String, Object> metric,
            ExpressionCompiler.ReferenceResolver references) {
        String name = getString(metric, "name");
        try {
            ExpressionCompiler.Selected selected = ExpressionCompiler.select(metric);
            ExpressionCompiler.Compiled result = ExpressionCompiler.compile(
                    ExpressionCompiler.parse(selected.text(), selected.dialect()), references);
            ExpressionAst.Type actual = result.datatype() == null ? ExpressionAst.Type.NULL : ExpressionAst.Type.of(result.datatype());
            ExpressionAst.Type declared = ExpressionAst.Type.of(getString(metric, "datatype"));
            if (metric.containsKey("datatype") && declared == ExpressionAst.Type.UNKNOWN) {
                throw new IllegalArgumentException("unsupported metric datatype " + getString(metric, "datatype"));
            }
            if (!actual.numeric() && actual != ExpressionAst.Type.NULL) {
                throw new IllegalArgumentException("calculated measurements must be numeric, found " + actual);
            }
            if (declared != ExpressionAst.Type.UNKNOWN && (!declared.numeric()
                    || declared == ExpressionAst.Type.INTEGER && actual != ExpressionAst.Type.INTEGER && actual != ExpressionAst.Type.NULL)) {
                throw new IllegalArgumentException("datatype " + getString(metric, "datatype") + " is incompatible with expression result " + actual);
            }
            if (actual == ExpressionAst.Type.NULL && !declared.numeric()) {
                throw new IllegalArgumentException("all-null result needs an explicit numeric datatype");
            }
            if (result.level() == ExpressionCompiler.Level.ROW) {
                throw new IllegalArgumentException("unaggregated field in metric; use an explicit aggregate");
            }
            return actual == ExpressionAst.Type.NULL
                    ? new ExpressionCompiler.Compiled(result.expression(), declared.datatype, result.level(), result.datasets())
                    : result;
        } catch (IllegalArgumentException e) {
            throw new ConversionException("Metric '" + name + "': " + e.getMessage(), e);
        }
    }
}
