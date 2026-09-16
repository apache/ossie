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

import java.util.Map;
import java.util.Set;

/** Closed, version-controlled target capabilities; parser acceptance never implies function support. */
final class ExpressionFunctionRegistry {
    enum Rule { AGGREGATE, COALESCE, NULLIF, ISNULL, NUMERIC, YEAR, LENGTH, POSITION, SUBSTRING }
    record Spec(String target, int min, int max, Rule rule, Set<String> dialects) {}
    private static final Set<String> ALL = Set.of("SNOWFLAKE", "ANSI_SQL", "TABLEAU");
    private static final Set<String> SQL = Set.of("SNOWFLAKE", "ANSI_SQL");
    private static final Set<String> TUA = Set.of("TABLEAU");
    private static Spec spec(String target, int min, int max, Rule rule, Set<String> dialects) {
        return new Spec(target, min, max, rule, dialects);
    }
    private static final Map<String, Spec> FUNCTIONS = Map.ofEntries(
            Map.entry("SUM", spec("SUM", 1, 1, Rule.AGGREGATE, ALL)),
            Map.entry("AVG", spec("AVG", 1, 1, Rule.AGGREGATE, ALL)),
            Map.entry("MIN", spec("MIN", 1, 1, Rule.AGGREGATE, ALL)),
            Map.entry("MAX", spec("MAX", 1, 1, Rule.AGGREGATE, ALL)),
            Map.entry("COUNT", spec("COUNT", 1, 1, Rule.AGGREGATE, ALL)),
            Map.entry("COUNTD", spec("COUNTD", 1, 1, Rule.AGGREGATE, TUA)),
            Map.entry("COALESCE", spec("IFNULL", 2, Integer.MAX_VALUE, Rule.COALESCE, SQL)),
            Map.entry("IFNULL", spec("IFNULL", 2, 2, Rule.COALESCE, TUA)),
            Map.entry("NULLIF", spec("IF", 2, 2, Rule.NULLIF, SQL)),
            Map.entry("ISNULL", spec("ISNULL", 1, 1, Rule.ISNULL, TUA)),
            Map.entry("ABS", spec("ABS", 1, 1, Rule.NUMERIC, ALL)),
            Map.entry("CEIL", spec("CEILING", 1, 1, Rule.NUMERIC, SQL)),
            Map.entry("CEILING", spec("CEILING", 1, 1, Rule.NUMERIC, TUA)),
            Map.entry("FLOOR", spec("FLOOR", 1, 1, Rule.NUMERIC, ALL)),
            Map.entry("ROUND", spec("ROUND", 1, 2, Rule.NUMERIC, ALL)),
            Map.entry("YEAR", spec("YEAR", 1, 1, Rule.YEAR, ALL)),
            Map.entry("LENGTH", spec("LEN", 1, 1, Rule.LENGTH, SQL)),
            Map.entry("LEN", spec("LEN", 1, 1, Rule.LENGTH, TUA)),
            Map.entry("POSITION", spec("FIND", 2, 2, Rule.POSITION, SQL)),
            Map.entry("FIND", spec("FIND", 2, 2, Rule.POSITION, TUA)),
            Map.entry("SUBSTRING", spec("MID", 2, 3, Rule.SUBSTRING, SQL)),
            Map.entry("MID", spec("MID", 2, 3, Rule.SUBSTRING, TUA)));

    private ExpressionFunctionRegistry() {}
    static Spec require(String name, String dialect, int count, boolean distinct) {
        Spec spec = FUNCTIONS.get(name);
        if (spec == null) throw new IllegalArgumentException("unsupported function " + name);
        if (!spec.dialects().contains(dialect)) {
            throw new IllegalArgumentException(name + " is outside the supported " + dialect + " subset");
        }
        if (distinct && (!name.equals("COUNT") || dialect.equals("TABLEAU"))) {
            throw new IllegalArgumentException("DISTINCT is supported only by SQL COUNT(DISTINCT field)");
        }
        if (count < spec.min() || count > spec.max()) {
            throw new IllegalArgumentException(name + " expects "
                    + (spec.min() == spec.max() ? spec.min() : spec.min() + " to " + spec.max()) + " arguments");
        }
        return spec;
    }
    static Spec get(String name) { return FUNCTIONS.get(name); }
}
