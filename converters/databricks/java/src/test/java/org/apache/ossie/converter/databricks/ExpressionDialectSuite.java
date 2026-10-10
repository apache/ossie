/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ossie.converter.databricks;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Set;
import org.junit.jupiter.api.Test;

public class ExpressionDialectSuite {

  private static final Set<String> DATASETS = Set.of("sales", "customer");
  private static final Set<String> VIEW_NAMES = Set.of("p_region", "status");

  private static void assertDialect(String expected, String... expressions) {
    for (String expr : expressions) {
      assertEquals(expected, ExpressionDialect.classify(expr, DATASETS, VIEW_NAMES, false), expr);
    }
  }

  private static void assertMeasureDialect(String expected, String... expressions) {
    for (String expr : expressions) {
      assertEquals(expected, ExpressionDialect.classify(expr, DATASETS, VIEW_NAMES, true), expr);
    }
  }

  @Test
  public void ossieLanguageExpressionsAreOssieSql() {
    assertDialect("OSSIE_SQL_2026",
        "amount",
        "SUM(sales.amount)",
        "COUNT(customer.c_name)",
        "COUNT(*)",
        "COUNT(DISTINCT customer_id)",
        "SUM(CASE WHEN o_status = 'done' THEN amount ELSE 0 END)",
        "DATE_TRUNC('month', order_date)",
        "EXTRACT(YEAR FROM order_date)",
        "CAST(amount AS DECIMAL(10, 2))",
        "CAST(amount AS DOUBLE)",
        "CAST(amount AS STRING)",
        "COALESCE(a, b) + 1.5e3",
        "a IS NOT DISTINCT FROM b",
        "name ILIKE '%x%'",
        "x IN ('a', 'b') AND NOT y IS NULL",
        "amount % 2 != 0",
        "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)",
        "DATEADD(day, 7, order_date)",
        "DATE '2024-01-15'",
        "TIMESTAMP_NTZ '2024-01-15 10:00:00'",
        "POSITION('a' IN s)",
        "SUBSTRING(s, 2, 3)",
        "CURRENT_DATE()",
        "a || b",
        "LOG(10, x)");
  }

  @Test
  public void standardSqlOutsideTheOssieLanguageIsAnsiSql() {
    assertDialect("ANSI_SQL",
        "CHAR_LENGTH(name)",
        "SUM(amount) FILTER (WHERE amount > 0)",
        "CORR(a, b)",
        "SUBSTRING(s FROM 2 FOR 3)",
        "TRIM(BOTH ' ' FROM s)",
        "s LIKE 'a!%' ESCAPE '!'",
        "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount NULLS LAST)");
  }

  @Test
  public void aMeasureIsPortableOnlyWhenEveryNameSitsInAnAggregate() {
    // Outside an aggregate call, a bare name in a measure refers to another measure.
    assertMeasureDialect("OSSIE_SQL_2026",
        "SUM(amount)",
        "SUM(sales.amount) / NULLIF(COUNT(DISTINCT customer_id), 0)",
        "SUM(CASE WHEN (a > 1) THEN b END) * 2",
        "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)");
    assertMeasureDialect("ANSI_SQL", "SUM(amount) FILTER (WHERE amount > 0)");
    assertMeasureDialect("DATABRICKS",
        "total_cost / row_count",
        "running_revenue * 2",
        "SUM(amount) + sales.amount",
        "DATEADD(day, 1, MAX(order_date))");
  }

  @Test
  public void databricksOnlyOrAmbiguousExpressionsStayDatabricks() {
    assertDialect("DATABRICKS",
        // Databricks reads "x" as a string where portable SQL reads an identifier, and a legacy
        // Databricks setting reads 'a''b' as two concatenated literals rather than one.
        "\"amount\"",
        "'it''s'",
        "'a' 'b'",
        "'a\\'b'",
        "`my col`",
        // Databricks-only syntax and operators.
        "x::int",
        ":param * 2",
        "x <=> y",
        "x == y",
        "arr[0]",
        "t.*",
        "a.b.c",
        "transform(arr, x -> x + 1)",
        "name RLIKE 'a.*'",
        "INTERVAL 7 DAY",
        "1L + 2",
        "a -- comment",
        "MEASURE(revenue)",
        "schema.fn(x)",
        "SUM(x) total",
        "x IN (SELECT y FROM t)",
        "CURRENT_USER()",
        "localtime",
        "o_flags >> 3",
        "x << 2",
        // Each half fits only one portable dialect: CHAR_LENGTH is standard SQL outside the Ossie
        // language, while CURRENT_DATE() and TIMESTAMP_NTZ are Ossie forms outside standard SQL.
        "CHAR_LENGTH(s) > 0 AND d = CURRENT_DATE()",
        "CHAR_LENGTH(s) > 0 AND d = TIMESTAMP_NTZ '2024-01-15 10:00:00'",
        // Names that are not a dataset column: the fact alias, a struct field, a parameter, and a
        // dimension that may shadow a column of the same name.
        "UPPER(source.o_status)",
        "address.city",
        "amount * p_region",
        "UPPER(status)",
        "SUM(sales.status)",
        "customer.address.city",
        "UPPER(customer.nation.n_name)",
        "customer.fn(x)",
        // Windows: Databricks sorts NULLs first by default, and the frame depends on query grain.
        "SUM(x) OVER (ORDER BY v)",
        "ROW_NUMBER() OVER (ORDER BY d NULLS LAST)",
        "SUM(x) OVER w",
        // Same function name, different meaning or form in Databricks.
        "DATEDIFF(day, start_date, end_date)",
        "DATEDIFF(end_date, start_date)",
        "LOG(x)",
        "COUNT(a, b)",
        "CEIL(x, 2)",
        "TRUNC(d, 'MM')",
        "POSITION('a', s)",
        "TRIM(s, 'x')",
        "TRIM(BOTH FROM s, 'x')",
        "REGEXP_EXTRACT(s, 'a')",
        // CAST targets whose Databricks type differs: VARCHAR is a plain STRING, FLOAT is 32-bit,
        // an integer cast truncates, and a bare DECIMAL is DECIMAL(10, 0).
        "CAST(z AS VARCHAR(5))",
        "CAST(x AS FLOAT)",
        "CAST(x AS INT)",
        "CAST(x AS DECIMAL)",
        "CAST(d AS TIMESTAMP)",
        "TIMESTAMP '2024-01-01'",
        // Not a single well-formed expression.
        "",
        "(a",
        "a, b");
  }
}
