-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--   http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing,
-- software distributed under the License is distributed on an
-- "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
-- KIND, either express or implied.  See the License for the
-- specific language governing permissions and limitations
-- under the License.

-- ============================================================================
-- Compliance queries: Relational Query Interface (Layer 2)
--
-- Test cases for core-spec/relational_semantics.md over the classic SCOTT
-- schema. Each case has three parts:
--   1. An Ossie query that evaluates measures with AGG() (commented out).
--   2. An equivalent DuckDB query over the base tables (runnable).
--   3. The expected result (commented out).
--
-- Every Ossie query evaluates its measures with AGG().
--
-- Part A covers the fundamentals on one relation. Part B covers joins: joins
-- that repeat no measure row, joins that repeat rows (fan-out and
-- many-to-many), and two queries that are run with each join type. For
-- joins, the DuckDB query applies the reference evaluation strategy of
-- Section 9 of the spec: it removes join duplicates by row-identity key (the
-- dataset's primary_key), then it aggregates.
--
-- Run:
--   duckdb < compliance/query/relational_semantics.sql
--
-- The file needs no extensions or imports. Everything that DuckDB cannot run
-- is commented out.
--
-- Expected-result format:
--   * Column headers are in uppercase.
--   * Numbers are right-aligned. Strings and dates are left-aligned.
--   * NULL is shown as an empty cell.
--   * DOUBLE values are rounded to 2 decimal places.
--
-- References:
--   * Spec: core-spec/relational_semantics.md (apache/ossie#354)
--   * Dataset-scoped metrics: apache/ossie#591
--   * Test format: apache/ossie#501. View expander: apache/ossie#502.
-- ============================================================================


-- ============================================================================
-- Data: the SCOTT schema
--
-- Values are the same as the SCOTT tables in Apache Calcite
-- (core/src/test/resources/sql/within-distinct.iq).
-- ============================================================================

DROP SCHEMA IF EXISTS scott CASCADE;
CREATE SCHEMA scott;

CREATE TABLE scott.dept (
  deptno INTEGER NOT NULL PRIMARY KEY,
  dname  VARCHAR NOT NULL,
  loc    VARCHAR NOT NULL
);

INSERT INTO scott.dept VALUES
  (10, 'ACCOUNTING', 'NEW YORK'),
  (20, 'RESEARCH',   'DALLAS'),
  (30, 'SALES',      'CHICAGO'),
  (40, 'OPERATIONS', 'BOSTON');

CREATE TABLE scott.emp (
  empno    INTEGER NOT NULL PRIMARY KEY,
  ename    VARCHAR NOT NULL,
  job      VARCHAR NOT NULL,
  mgr      INTEGER,
  hiredate DATE NOT NULL,
  sal      DECIMAL(7, 2) NOT NULL,
  comm     DECIMAL(7, 2),
  deptno   INTEGER NOT NULL
);

INSERT INTO scott.emp VALUES
  (7369, 'SMITH',  'CLERK',     7902, DATE '1980-12-17',  800.00,    NULL, 20),
  (7499, 'ALLEN',  'SALESMAN',  7698, DATE '1981-02-20', 1600.00,  300.00, 30),
  (7521, 'WARD',   'SALESMAN',  7698, DATE '1981-02-22', 1250.00,  500.00, 30),
  (7566, 'JONES',  'MANAGER',   7839, DATE '1981-02-04', 2975.00,    NULL, 20),
  (7654, 'MARTIN', 'SALESMAN',  7698, DATE '1981-09-28', 1250.00, 1400.00, 30),
  (7698, 'BLAKE',  'MANAGER',   7839, DATE '1981-01-05', 2850.00,    NULL, 30),
  (7782, 'CLARK',  'MANAGER',   7839, DATE '1981-06-09', 2450.00,    NULL, 10),
  (7788, 'SCOTT',  'ANALYST',   7566, DATE '1987-04-19', 3000.00,    NULL, 20),
  (7839, 'KING',   'PRESIDENT', NULL, DATE '1981-11-17', 5000.00,    NULL, 10),
  (7844, 'TURNER', 'SALESMAN',  7698, DATE '1981-09-08', 1500.00,    0.00, 30),
  (7876, 'ADAMS',  'CLERK',     7788, DATE '1987-05-23', 1100.00,    NULL, 20),
  (7900, 'JAMES',  'CLERK',     7698, DATE '1981-12-03',  950.00,    NULL, 30),
  (7902, 'FORD',   'ANALYST',   7566, DATE '1981-12-03', 3000.00,    NULL, 20),
  (7934, 'MILLER', 'CLERK',     7782, DATE '1982-01-23', 1300.00,    NULL, 10);

CREATE TABLE scott.salgrade (
  grade INTEGER NOT NULL PRIMARY KEY,
  losal INTEGER NOT NULL,
  hisal INTEGER NOT NULL
);

INSERT INTO scott.salgrade VALUES
  (1,  700, 1200),
  (2, 1201, 1400),
  (3, 1401, 2000),
  (4, 2001, 3000),
  (5, 3001, 9999);

-- BONUS has no rows in SCOTT. It is here for completeness; no test uses it.
CREATE TABLE scott.bonus (
  ename VARCHAR,
  job   VARCHAR,
  sal   DECIMAL(7, 2),
  comm  DECIMAL(7, 2)
);


-- ============================================================================
-- Model: Ossie semantic model over SCOTT (commented out)
--
-- The metrics are dataset-scoped (datasets[].metrics, apache/ossie#591). Each
-- dataset is exposed as a relation with the same name, and each metric is a
-- measure column of that relation (spec Section 3.4). All relations of the
-- model are in one schema with the name of the model (spec Section 11.2). The
-- Ossie queries below refer to these relations by two-part name:
-- scott_model.emp, scott_model.dept, and scott_model.salgrade.
--
-- The primary_key of each dataset is the row identity of its measures (spec
-- Section 3.2).
--
-- The emp-to-salgrade join (e.sal BETWEEN s.losal AND s.hisal) is a range
-- join. An Ossie relationship cannot express it, so the consuming query
-- writes it (spec Section 11.3).
-- ============================================================================

-- version: 0.2.0.dev0
-- name: scott_model
-- description: The SCOTT schema with dataset-scoped metrics
-- datasets:
--   - name: emp
--     source: scott.emp
--     primary_key: [empno]
--     fields:
--       - name: empno
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: empno}]}
--       - name: ename
--         datatype: String
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: ename}]}
--       - name: job
--         datatype: String
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: job}]}
--       - name: mgr
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: mgr}]}
--       - name: hiredate
--         datatype: Date
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: hiredate}]}
--       - name: sal
--         datatype: Decimal
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: sal}]}
--       - name: comm
--         datatype: Decimal
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: comm}]}
--       - name: deptno
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: deptno}]}
--     metrics:
--       - name: sum_sal
--         description: Total salary
--         datatype: Decimal
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: SUM(sal)}]}
--       - name: avg_sal
--         description: Average salary
--         datatype: Float
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: AVG(sal)}]}
--       - name: emp_count
--         description: Number of employees
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: COUNT(*)}]}
--       - name: comm_count
--         description: Number of employees that have a commission
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: COUNT(comm)}]}
--       - name: sum_comm
--         description: Total commission
--         datatype: Decimal
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: SUM(comm)}]}
--       - name: avg_comm
--         description: Average commission, over employees that have a commission
--         datatype: Float
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: AVG(comm)}]}
--
--   - name: dept
--     source: scott.dept
--     primary_key: [deptno]
--     fields:
--       - name: deptno
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: deptno}]}
--       - name: dname
--         datatype: String
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: dname}]}
--       - name: loc
--         datatype: String
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: loc}]}
--     metrics:
--       - name: dept_count
--         description: Number of departments
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: COUNT(*)}]}
--
--   - name: salgrade
--     source: scott.salgrade
--     primary_key: [grade]
--     fields:
--       - name: grade
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: grade}]}
--       - name: losal
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: losal}]}
--       - name: hisal
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: hisal}]}
--     metrics:
--       - name: grade_count
--         description: Number of salary grades
--         datatype: Integer
--         expression: {dialects: [{dialect: OSSIE_SQL_2026, expression: COUNT(*)}]}
--
-- relationships:
--   - name: emp_to_dept
--     from: emp
--     to: dept
--     from_columns: [deptno]
--     to_columns: [deptno]
--   - name: emp_to_manager
--     from: emp
--     to: emp
--     from_columns: [mgr]
--     to_columns: [empno]


-- ============================================================================
-- Part A: fundamentals (one relation)
--
-- On one relation, no join repeats rows. So AGG(m) gives the same result as
-- the metric's aggregate over the same rows. Each DuckDB query is the Ossie
-- query with each AGG(m) replaced by the expression of m, and with each model
-- relation (scott_model.emp) replaced by its source table (scott.emp).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- A1. Grand total.
-- AGG() is an aggregate. Without GROUP BY, the query returns one row.
-- Spec: 6, 7.2
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(sum_sal) AS sum_sal,
--          AGG(emp_count) AS emp_count,
--          AGG(avg_sal) AS avg_sal
--   FROM scott_model.emp;
SELECT SUM(sal) AS sum_sal,
       COUNT(*) AS emp_count,
       AVG(sal) AS avg_sal
FROM scott.emp;
-- +----------+-----------+---------+
-- | SUM_SAL  | EMP_COUNT | AVG_SAL |
-- +----------+-----------+---------+
-- | 29025.00 |        14 | 2073.21 |
-- +----------+-----------+---------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- A2. GROUP BY a field.
-- The grouping sets the level at which each measure is reported.
-- Spec: 7.4, G1 (8.2)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT deptno,
--          AGG(sum_sal) AS sum_sal,
--          AGG(emp_count) AS emp_count,
--          AGG(avg_sal) AS avg_sal
--   FROM scott_model.emp
--   GROUP BY deptno
--   ORDER BY deptno;
SELECT deptno,
       SUM(sal) AS sum_sal,
       COUNT(*) AS emp_count,
       AVG(sal) AS avg_sal
FROM scott.emp
GROUP BY deptno
ORDER BY deptno;
-- +--------+----------+-----------+---------+
-- | DEPTNO | SUM_SAL  | EMP_COUNT | AVG_SAL |
-- +--------+----------+-----------+---------+
-- |     10 |  8750.00 |         3 | 2916.67 |
-- |     20 | 10875.00 |         5 | 2175.00 |
-- |     30 |  9400.00 |         6 | 1566.67 |
-- +--------+----------+-----------+---------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A3. WHERE on a field.
-- WHERE removes rows before evaluation, so each measure sees only the rows
-- that remain.
-- Spec: 7.3, G1 (8.2)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT deptno,
--          AGG(sum_sal) AS sum_sal,
--          AGG(emp_count) AS emp_count
--   FROM scott_model.emp
--   WHERE job <> 'CLERK'
--   GROUP BY deptno
--   ORDER BY deptno;
SELECT deptno,
       SUM(sal) AS sum_sal,
       COUNT(*) AS emp_count
FROM scott.emp
WHERE job <> 'CLERK'
GROUP BY deptno
ORDER BY deptno;
-- +--------+---------+-----------+
-- | DEPTNO | SUM_SAL | EMP_COUNT |
-- +--------+---------+-----------+
-- |     10 | 7450.00 |         2 |
-- |     20 | 8975.00 |         3 |
-- |     30 | 8450.00 |         5 |
-- +--------+---------+-----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A4. HAVING, ORDER BY, and LIMIT on measures.
-- AGG() can appear in HAVING and ORDER BY, as any aggregate can. HAVING can
-- use a measure that the SELECT list does not contain. HAVING removes ANALYST
-- (6000, 2 employees) and PRESIDENT (5000, 1 employee), so CLERK (4150) is in
-- the top three.
-- Spec: 7.5
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT job, AGG(sum_sal) AS sum_sal
--   FROM scott_model.emp
--   GROUP BY job
--   HAVING AGG(emp_count) > 2
--   ORDER BY AGG(sum_sal) DESC
--   LIMIT 3;
SELECT job, SUM(sal) AS sum_sal
FROM scott.emp
GROUP BY job
HAVING COUNT(*) > 2
ORDER BY SUM(sal) DESC
LIMIT 3;
-- +----------+---------+
-- | JOB      | SUM_SAL |
-- +----------+---------+
-- | MANAGER  | 8275.00 |
-- | SALESMAN | 5600.00 |
-- | CLERK    | 4150.00 |
-- +----------+---------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A5. Scalar expressions over AGG() results.
-- Each AGG() gives a scalar value for each group. Scalar operators and
-- functions then apply to these values. AGG(sum_sal) / AGG(emp_count) is equal
-- to AGG(avg_sal). COALESCE replaces the NULL commission of departments 10
-- and 20.
-- Spec: 7.2, 8.1
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT deptno,
--          AGG(sum_sal) / AGG(emp_count) AS sal_per_emp,
--          AGG(avg_sal) AS avg_sal,
--          AGG(sum_sal) + COALESCE(AGG(sum_comm), 0) AS total_pay
--   FROM scott_model.emp
--   GROUP BY deptno
--   ORDER BY deptno;
SELECT deptno,
       SUM(sal) / COUNT(*) AS sal_per_emp,
       AVG(sal) AS avg_sal,
       SUM(sal) + COALESCE(SUM(comm), 0) AS total_pay
FROM scott.emp
GROUP BY deptno
ORDER BY deptno;
-- +--------+-------------+---------+-----------+
-- | DEPTNO | SAL_PER_EMP | AVG_SAL | TOTAL_PAY |
-- +--------+-------------+---------+-----------+
-- |     10 |     2916.67 | 2916.67 |   8750.00 |
-- |     20 |     2175.00 | 2175.00 |  10875.00 |
-- |     30 |     1566.67 | 1566.67 |  11600.00 |
-- +--------+-------------+---------+-----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A6. NULL inputs.
-- A measure follows the NULL rules of its aggregate. emp_count is COUNT(*)
-- and counts every row. comm_count is COUNT(comm) and ignores NULL. Departments
-- 10 and 20 have no commission values, so SUM and AVG give NULL. In department
-- 30, TURNER's 0.00 is a value, so AVG(comm) is 2200 / 4.
-- Spec: 8.1
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT deptno,
--          AGG(emp_count) AS emp_count,
--          AGG(comm_count) AS comm_count,
--          AGG(sum_comm) AS sum_comm,
--          AGG(avg_comm) AS avg_comm
--   FROM scott_model.emp
--   GROUP BY deptno
--   ORDER BY deptno;
SELECT deptno,
       COUNT(*) AS emp_count,
       COUNT(comm) AS comm_count,
       SUM(comm) AS sum_comm,
       AVG(comm) AS avg_comm
FROM scott.emp
GROUP BY deptno
ORDER BY deptno;
-- +--------+-----------+------------+----------+----------+
-- | DEPTNO | EMP_COUNT | COMM_COUNT | SUM_COMM | AVG_COMM |
-- +--------+-----------+------------+----------+----------+
-- |     10 |         3 |          0 |          |          |
-- |     20 |         5 |          0 |          |          |
-- |     30 |         6 |          4 |  2200.00 |   550.00 |
-- +--------+-----------+------------+----------+----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A7. Empty input set.
-- No employee is in department 40. Each measure takes the empty-input value
-- of its aggregate: 0 for COUNT, NULL for SUM and AVG.
-- Spec: 8.1
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(emp_count) AS emp_count,
--          AGG(sum_sal) AS sum_sal,
--          AGG(avg_sal) AS avg_sal
--   FROM scott_model.emp
--   WHERE deptno = 40;
SELECT COUNT(*) AS emp_count,
       SUM(sal) AS sum_sal,
       AVG(sal) AS avg_sal
FROM scott.emp
WHERE deptno = 40;
-- +-----------+---------+---------+
-- | EMP_COUNT | SUM_SAL | AVG_SAL |
-- +-----------+---------+---------+
-- |         0 |         |         |
-- +-----------+---------+---------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- A8. GROUP BY ALL on an expression.
-- A measure is not a grouping column. GROUP BY ALL groups by the other
-- expressions of the SELECT list: here, an expression on a field.
-- Spec: 7.4
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT EXTRACT(YEAR FROM hiredate) AS hire_year,
--          AGG(emp_count) AS emp_count,
--          AGG(sum_sal) AS sum_sal
--   FROM scott_model.emp
--   GROUP BY ALL
--   ORDER BY hire_year;
SELECT EXTRACT(YEAR FROM hiredate) AS hire_year,
       COUNT(*) AS emp_count,
       SUM(sal) AS sum_sal
FROM scott.emp
GROUP BY ALL
ORDER BY hire_year;
-- +-----------+-----------+----------+
-- | HIRE_YEAR | EMP_COUNT | SUM_SAL  |
-- +-----------+-----------+----------+
-- |      1980 |         1 |   800.00 |
-- |      1981 |        10 | 22825.00 |
-- |      1982 |         1 |  1300.00 |
-- |      1987 |         2 |  4100.00 |
-- +-----------+-----------+----------+
-- (4 rows)

-- ----------------------------------------------------------------------------
-- A9. ROLLUP.
-- A measure is evaluated again for each grouping set. On the total row,
-- AGG(avg_sal) is the average over all 14 employees (2073.21). It is not the
-- average of the three department averages (2219.44).
-- Spec: 7.4, G1 (8.2)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT deptno,
--          AGG(emp_count) AS emp_count,
--          AGG(avg_sal) AS avg_sal
--   FROM scott_model.emp
--   GROUP BY ROLLUP (deptno)
--   ORDER BY deptno NULLS LAST;
SELECT deptno,
       COUNT(*) AS emp_count,
       AVG(sal) AS avg_sal
FROM scott.emp
GROUP BY ROLLUP (deptno)
ORDER BY deptno NULLS LAST;
-- +--------+-----------+---------+
-- | DEPTNO | EMP_COUNT | AVG_SAL |
-- +--------+-----------+---------+
-- |     10 |         3 | 2916.67 |
-- |     20 |         5 | 2175.00 |
-- |     30 |         6 | 1566.67 |
-- |        |        14 | 2073.21 |
-- +--------+-----------+---------+
-- (4 rows)

-- ----------------------------------------------------------------------------
-- A10. Measure through a CTE that filters and renames.
-- The CTE passes the measures through without evaluating them. A rename does
-- not change a measure. The WHERE clause of the CTE restricts the rows that
-- the outer AGG() calls see.
-- Spec: 7.1, 7.7, 8.1
-- ----------------------------------------------------------------------------
-- Ossie:
--   WITH staff AS (
--     SELECT deptno, sum_sal AS payroll, emp_count AS headcount
--     FROM scott_model.emp
--     WHERE job IN ('CLERK', 'SALESMAN'))
--   SELECT deptno, AGG(payroll) AS payroll, AGG(headcount) AS headcount
--   FROM staff
--   GROUP BY deptno
--   ORDER BY deptno;
WITH staff AS (
  SELECT deptno, sal
  FROM scott.emp
  WHERE job IN ('CLERK', 'SALESMAN'))
SELECT deptno, SUM(sal) AS payroll, COUNT(*) AS headcount
FROM staff
GROUP BY deptno
ORDER BY deptno;
-- +--------+---------+-----------+
-- | DEPTNO | PAYROLL | HEADCOUNT |
-- +--------+---------+-----------+
-- |     10 | 1300.00 |         1 |
-- |     20 | 1900.00 |         2 |
-- |     30 | 6550.00 |         5 |
-- +--------+---------+-----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A11. AGG() in a scalar subquery.
-- AGG() cannot appear directly in WHERE, but a scalar subquery in WHERE can
-- use it. The query counts, by department, the employees whose salary is
-- greater than the company average (2073.21).
-- Spec: 7.3
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT deptno, AGG(emp_count) AS emp_count
--   FROM scott_model.emp
--   WHERE sal > (SELECT AGG(avg_sal) FROM scott_model.emp)
--   GROUP BY deptno
--   ORDER BY deptno;
SELECT deptno, COUNT(*) AS emp_count
FROM scott.emp
WHERE sal > (SELECT AVG(sal) FROM scott.emp)
GROUP BY deptno
ORDER BY deptno;
-- +--------+-----------+
-- | DEPTNO | EMP_COUNT |
-- +--------+-----------+
-- |     10 |         2 |
-- |     20 |         3 |
-- |     30 |         1 |
-- +--------+-----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- A12. Two-stage aggregation and a set operator.
-- The first branch averages the per-department payroll (29025 / 3). The
-- second branch is the average salary per employee. After AGG() evaluates the
-- measures, the values are ordinary scalars, so UNION ALL is valid.
-- Spec: A.3, 7.8
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT 'avg payroll per dept' AS metric, AVG(dept_sal) AS amount
--   FROM (SELECT deptno, AGG(sum_sal) AS dept_sal
--         FROM scott_model.emp
--         GROUP BY deptno) AS d
--   UNION ALL
--   SELECT 'avg salary per emp', AGG(avg_sal)
--   FROM scott_model.emp
--   ORDER BY metric;
SELECT 'avg payroll per dept' AS metric, AVG(dept_sal) AS amount
FROM (SELECT deptno, SUM(sal) AS dept_sal
      FROM scott.emp
      GROUP BY deptno) AS d
UNION ALL
SELECT 'avg salary per emp', AVG(sal)
FROM scott.emp
ORDER BY metric;
-- +----------------------+---------+
-- | METRIC               | AMOUNT  |
-- +----------------------+---------+
-- | avg payroll per dept | 9675.00 |
-- | avg salary per emp   | 2073.21 |
-- +----------------------+---------+
-- (2 rows)


-- ============================================================================
-- Part B: joins
--
-- A join can repeat a row of a measure's source (fan-out). An outer join can
-- also add NULL-padded rows that come from no source row. AGG(m) aggregates
-- each source row of m once and ignores NULL-padded rows (spec Section 8.1).
-- The comments give the result of a plain aggregate over the joined rows
-- ("naive") where it differs.
--
-- Each DuckDB query applies the reference strategy of spec Section 9:
--   1. The CTE "joined" is the Ossie query's FROM, JOIN, and WHERE. For each
--      measure, it also carries the row-identity key (<alias>_k, which is the
--      primary key of the dataset) and the arguments of the aggregate.
--   2. The CTE "spine" has one row for each group: SELECT DISTINCT over the
--      grouping columns. A grand total has one group and needs no spine.
--   3. For each measure, a correlated subquery selects the joined rows of the
--      group, removes the rows where the key is NULL, reduces the rows to one
--      for each key (GROUP BY key), and then applies the aggregate.
--   4. Scalar expressions over AGG() results apply to the per-group values.
--
-- B1-B3:   joins that repeat no row of the measure's source.
-- B4-B8:   joins that repeat rows: fan-out and many-to-many.
-- B9-B13:  one self-join query with each join type.
-- B14-B17: one dept-emp query with inner and outer joins.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- B1. Many-to-one join, group by a field of the joined relation.
-- Each employee matches one department, so the join repeats no emp row. The
-- measures are the same as the per-department totals of A2.
-- Spec: 7.6
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT d.dname,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(e.emp_count) AS emp_count
--   FROM scott_model.emp AS e
--   JOIN scott_model.dept AS d ON e.deptno = d.deptno
--   GROUP BY d.dname
--   ORDER BY d.dname;
WITH joined AS (
  SELECT d.dname,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno),
spine AS (SELECT DISTINCT dname FROM joined)
SELECT g.dname,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS emp_count
FROM spine AS g
ORDER BY g.dname;
-- +------------+----------+-----------+
-- | DNAME      | SUM_SAL  | EMP_COUNT |
-- +------------+----------+-----------+
-- | ACCOUNTING |  8750.00 |         3 |
-- | RESEARCH   | 10875.00 |         5 |
-- | SALES      |  9400.00 |         6 |
-- +------------+----------+-----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- B2. WHERE on a field of the joined relation.
-- A filter on dept removes emp rows from the input set, as a filter on emp
-- does. Only the employees in NEW YORK remain.
-- Spec: 7.6, G1 (8.2)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT e.job,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(e.emp_count) AS emp_count
--   FROM scott_model.emp AS e
--   JOIN scott_model.dept AS d ON e.deptno = d.deptno
--   WHERE d.loc = 'NEW YORK'
--   GROUP BY e.job
--   ORDER BY e.job;
WITH joined AS (
  SELECT e.job,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno
  WHERE d.loc = 'NEW YORK'),
spine AS (SELECT DISTINCT job FROM joined)
SELECT g.job,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS emp_count
FROM spine AS g
ORDER BY g.job;
-- +-----------+---------+-----------+
-- | JOB       | SUM_SAL | EMP_COUNT |
-- +-----------+---------+-----------+
-- | CLERK     | 1300.00 |         1 |
-- | MANAGER   | 2450.00 |         1 |
-- | PRESIDENT | 5000.00 |         1 |
-- +-----------+---------+-----------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- B3. Top-N filter through a join.
-- The subquery selects the two departments with the largest payroll (20 and
-- 30). The join to it acts as a filter on emp, and the outer query reports
-- payroll by job for those departments. The departments that the subquery
-- ranks are the same values that the join matches (closure, spec 8.4).
-- Spec: 7.6, 8.4, A.2
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT e.job, AGG(e.sum_sal) AS sum_sal
--   FROM scott_model.emp AS e
--   JOIN (SELECT deptno
--         FROM scott_model.emp
--         GROUP BY deptno
--         ORDER BY AGG(sum_sal) DESC
--         LIMIT 2) AS t ON e.deptno = t.deptno
--   GROUP BY e.job
--   ORDER BY e.job;
WITH joined AS (
  SELECT e.job,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.emp AS e
  JOIN (SELECT deptno
        FROM scott.emp
        GROUP BY deptno
        ORDER BY SUM(sal) DESC
        LIMIT 2) AS t ON e.deptno = t.deptno),
spine AS (SELECT DISTINCT job FROM joined)
SELECT g.job,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS sum_sal
FROM spine AS g
ORDER BY g.job;
-- +----------+---------+
-- | JOB      | SUM_SAL |
-- +----------+---------+
-- | ANALYST  | 6000.00 |
-- | CLERK    | 2850.00 |
-- | MANAGER  | 5825.00 |
-- | SALESMAN | 5600.00 |
-- +----------+---------+
-- (4 rows)

-- ----------------------------------------------------------------------------
-- B4. Fan-out: a measure of the "one" side, grouped by the "many" side.
-- The join repeats each dept row once for each employee in it. AGG(dept_count)
-- counts each department once in each group, so the result is the number of
-- departments that have the job. Naive COUNT(*) gives 2, 4, 3, 1, 4, which
-- AGG(e.emp_count) also gives, because the join repeats no emp row.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT e.job,
--          AGG(d.dept_count) AS dept_count,
--          AGG(e.emp_count) AS emp_count
--   FROM scott_model.emp AS e
--   JOIN scott_model.dept AS d ON e.deptno = d.deptno
--   GROUP BY e.job
--   ORDER BY e.job;
WITH joined AS (
  SELECT e.job,
         e.empno AS e_k,
         d.deptno AS d_k
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno),
spine AS (SELECT DISTINCT job FROM joined)
SELECT g.job,
       (SELECT COUNT(*)
        FROM (SELECT j.d_k
              FROM joined AS j
              WHERE j.d_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.d_k) AS x) AS dept_count,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS emp_count
FROM spine AS g
ORDER BY g.job;
-- +-----------+------------+-----------+
-- | JOB       | DEPT_COUNT | EMP_COUNT |
-- +-----------+------------+-----------+
-- | ANALYST   |          1 |         2 |
-- | CLERK     |          3 |         4 |
-- | MANAGER   |          3 |         3 |
-- | PRESIDENT |          1 |         1 |
-- | SALESMAN  |          1 |         4 |
-- +-----------+------------+-----------+
-- (5 rows)

-- ----------------------------------------------------------------------------
-- B5. Fan-out in a self-join: the manager's salary.
-- e is the employee and m is the manager. The join repeats each manager row
-- once for each report. AGG(m.sum_sal) counts each manager's salary once in
-- each group. The 4 salesmen all report to BLAKE, so the result is 2850; the
-- naive SUM(m.sal) is 11400. The 4 clerks report to 4 different managers, so
-- the two values are the same (11300). Naive SUM(m.sal) by job is 5950,
-- 11300, 15000, 11400.
-- Spec: 7.6, 8.1, G2 (8.3)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT e.job,
--          AGG(e.emp_count) AS emp_count,
--          AGG(m.emp_count) AS managers,
--          AGG(m.sum_sal) AS managers_sal
--   FROM scott_model.emp AS e
--   JOIN scott_model.emp AS m ON e.mgr = m.empno
--   GROUP BY e.job
--   ORDER BY e.job;
WITH joined AS (
  SELECT e.job,
         e.empno AS e_k,
         m.empno AS m_k, m.sal AS m_sal
  FROM scott.emp AS e
  JOIN scott.emp AS m ON e.mgr = m.empno),
spine AS (SELECT DISTINCT job FROM joined)
SELECT g.job,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.e_k) AS x) AS emp_count,
       (SELECT COUNT(*)
        FROM (SELECT j.m_k
              FROM joined AS j
              WHERE j.m_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.m_k) AS x) AS managers,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.m_sal) AS sal
              FROM joined AS j
              WHERE j.m_k IS NOT NULL AND j.job IS NOT DISTINCT FROM g.job
              GROUP BY j.m_k) AS x) AS managers_sal
FROM spine AS g
ORDER BY g.job;
-- +----------+-----------+----------+--------------+
-- | JOB      | EMP_COUNT | MANAGERS | MANAGERS_SAL |
-- +----------+-----------+----------+--------------+
-- | ANALYST  |         2 |        1 |      2975.00 |
-- | CLERK    |         4 |        4 |     11300.00 |
-- | MANAGER  |         3 |        1 |      5000.00 |
-- | SALESMAN |         4 |        1 |      2850.00 |
-- +----------+-----------+----------+--------------+
-- (4 rows)

-- ----------------------------------------------------------------------------
-- B6. Fan-out through a range join.
-- The join condition can be any predicate over fields, not only an equality.
-- Each employee matches one salary grade, and a grade can match many
-- employees. AGG(s.grade_count) counts each grade once in each department, so
-- it gives the number of distinct grades in the department. Naive COUNT(*)
-- gives 3, 5, 6.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT e.deptno,
--          AGG(e.emp_count) AS emp_count,
--          AGG(s.grade_count) AS grade_count
--   FROM scott_model.emp AS e
--   JOIN scott_model.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal
--   GROUP BY e.deptno
--   ORDER BY e.deptno;
WITH joined AS (
  SELECT e.deptno,
         e.empno AS e_k,
         s.grade AS s_k
  FROM scott.emp AS e
  JOIN scott.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal),
spine AS (SELECT DISTINCT deptno FROM joined)
SELECT g.deptno,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.deptno IS NOT DISTINCT FROM g.deptno
              GROUP BY j.e_k) AS x) AS emp_count,
       (SELECT COUNT(*)
        FROM (SELECT j.s_k
              FROM joined AS j
              WHERE j.s_k IS NOT NULL AND j.deptno IS NOT DISTINCT FROM g.deptno
              GROUP BY j.s_k) AS x) AS grade_count
FROM spine AS g
ORDER BY g.deptno;
-- +--------+-----------+-------------+
-- | DEPTNO | EMP_COUNT | GRADE_COUNT |
-- +--------+-----------+-------------+
-- |     10 |         3 |           3 |
-- |     20 |         5 |           2 |
-- |     30 |         6 |           4 |
-- +--------+-----------+-------------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- B7. Fan-out on two sides of a three-way join.
-- emp joins to dept and to salgrade. The group is the salary grade, and
-- AGG(d.dept_count) gives the number of departments that have an employee in
-- the grade. The join repeats each dept row once for each of its employees in
-- the grade. Naive COUNT(*) gives 3, 3, 2, 5, 1.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT s.grade,
--          AGG(e.emp_count) AS emp_count,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(d.dept_count) AS dept_count
--   FROM scott_model.emp AS e
--   JOIN scott_model.dept AS d ON e.deptno = d.deptno
--   JOIN scott_model.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal
--   GROUP BY s.grade
--   ORDER BY s.grade;
WITH joined AS (
  SELECT s.grade,
         e.empno AS e_k, e.sal AS e_sal,
         d.deptno AS d_k
  FROM scott.emp AS e
  JOIN scott.dept AS d ON e.deptno = d.deptno
  JOIN scott.salgrade AS s ON e.sal BETWEEN s.losal AND s.hisal),
spine AS (SELECT DISTINCT grade FROM joined)
SELECT g.grade,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.grade IS NOT DISTINCT FROM g.grade
              GROUP BY j.e_k) AS x) AS emp_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.grade IS NOT DISTINCT FROM g.grade
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.d_k
              FROM joined AS j
              WHERE j.d_k IS NOT NULL AND j.grade IS NOT DISTINCT FROM g.grade
              GROUP BY j.d_k) AS x) AS dept_count
FROM spine AS g
ORDER BY g.grade;
-- +-------+-----------+----------+------------+
-- | GRADE | EMP_COUNT | SUM_SAL  | DEPT_COUNT |
-- +-------+-----------+----------+------------+
-- |     1 |         3 |  2850.00 |          2 |
-- |     2 |         3 |  3800.00 |          2 |
-- |     3 |         2 |  3100.00 |          1 |
-- |     4 |         5 | 14275.00 |          3 |
-- |     5 |         1 |  5000.00 |          1 |
-- +-------+-----------+----------+------------+
-- (5 rows)

-- ----------------------------------------------------------------------------
-- B8. Many-to-many join (chasm trap).
-- Each employee is joined to every colleague in the same department, which
-- gives 3 x 3, 5 x 5, and 6 x 6 joined rows. Both sides repeat. Each measure
-- still counts each of its source rows once. Naive SUM(e.sal) gives 26250,
-- 54375, 56400, and naive COUNT(*) gives 9, 25, 36.
-- Spec: 7.6, G2 (8.3)
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT e.deptno,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(c.emp_count) AS colleagues
--   FROM scott_model.emp AS e
--   JOIN scott_model.emp AS c ON e.deptno = c.deptno
--   GROUP BY e.deptno
--   ORDER BY e.deptno;
WITH joined AS (
  SELECT e.deptno,
         e.empno AS e_k, e.sal AS e_sal,
         c.empno AS c_k
  FROM scott.emp AS e
  JOIN scott.emp AS c ON e.deptno = c.deptno),
spine AS (SELECT DISTINCT deptno FROM joined)
SELECT g.deptno,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.deptno IS NOT DISTINCT FROM g.deptno
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.c_k
              FROM joined AS j
              WHERE j.c_k IS NOT NULL AND j.deptno IS NOT DISTINCT FROM g.deptno
              GROUP BY j.c_k) AS x) AS colleagues
FROM spine AS g
ORDER BY g.deptno;
-- +--------+----------+------------+
-- | DEPTNO | SUM_SAL  | COLLEAGUES |
-- +--------+----------+------------+
-- |     10 |  8750.00 |          3 |
-- |     20 | 10875.00 |          5 |
-- |     30 |  9400.00 |          6 |
-- +--------+----------+------------+
-- (3 rows)

-- ----------------------------------------------------------------------------
-- B9-B13. One self-join query with each join type.
--
-- e is the employee and m is the manager, joined on e.mgr = m.empno. Both
-- sides have rows that do not match:
--   * KING has no manager, so KING has no match on the e side.
--   * 8 employees manage nobody, so they have no match on the m side.
-- The join type decides which source rows reach each measure. The join never
-- inflates a measure, and NULL-padded rows add nothing:
--
--   join type   joined rows   e_count  e_sal     m_count  m_sal
--   INNER       13            13       24025.00  6        19275.00
--   LEFT        14            14       29025.00  6        19275.00
--   RIGHT       21            13       24025.00  14       29025.00
--   FULL        22            14       29025.00  14       29025.00
--   CROSS       196           14       29025.00  14       29025.00
--
-- Naive SUM(m.sal) for the INNER join is 43650: KING's salary is counted 3
-- times and BLAKE's 5 times.
-- Spec: 7.6, 8.1, G2 (8.3), A.6
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- B9. INNER JOIN.
-- Only employees that have a manager, and only employees that are managers.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(e.emp_count) AS e_count,
--          AGG(e.sum_sal) AS e_sal,
--          AGG(m.emp_count) AS m_count,
--          AGG(m.sum_sal) AS m_sal
--   FROM scott_model.emp AS e
--   JOIN scott_model.emp AS m ON e.mgr = m.empno;
WITH joined AS (
  SELECT e.empno AS e_k, e.sal AS e_sal,
         m.empno AS m_k, m.sal AS m_sal
  FROM scott.emp AS e
  JOIN scott.emp AS m ON e.mgr = m.empno)
SELECT (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.m_k
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.m_sal) AS sal
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_sal;
-- +---------+----------+---------+----------+
-- | E_COUNT | E_SAL    | M_COUNT | M_SAL    |
-- +---------+----------+---------+----------+
-- |      13 | 24025.00 |       6 | 19275.00 |
-- +---------+----------+---------+----------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B10. LEFT JOIN.
-- KING is kept on the e side. KING's row is NULL-padded on the m side, so the
-- m measures do not change.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(e.emp_count) AS e_count,
--          AGG(e.sum_sal) AS e_sal,
--          AGG(m.emp_count) AS m_count,
--          AGG(m.sum_sal) AS m_sal
--   FROM scott_model.emp AS e
--   LEFT JOIN scott_model.emp AS m ON e.mgr = m.empno;
WITH joined AS (
  SELECT e.empno AS e_k, e.sal AS e_sal,
         m.empno AS m_k, m.sal AS m_sal
  FROM scott.emp AS e
  LEFT JOIN scott.emp AS m ON e.mgr = m.empno)
SELECT (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.m_k
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.m_sal) AS sal
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_sal;
-- +---------+----------+---------+----------+
-- | E_COUNT | E_SAL    | M_COUNT | M_SAL    |
-- +---------+----------+---------+----------+
-- |      14 | 29025.00 |       6 | 19275.00 |
-- +---------+----------+---------+----------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B11. RIGHT JOIN.
-- The 8 employees that manage nobody are kept on the m side. Their rows are
-- NULL-padded on the e side, so the e measures are the same as for INNER.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(e.emp_count) AS e_count,
--          AGG(e.sum_sal) AS e_sal,
--          AGG(m.emp_count) AS m_count,
--          AGG(m.sum_sal) AS m_sal
--   FROM scott_model.emp AS e
--   RIGHT JOIN scott_model.emp AS m ON e.mgr = m.empno;
WITH joined AS (
  SELECT e.empno AS e_k, e.sal AS e_sal,
         m.empno AS m_k, m.sal AS m_sal
  FROM scott.emp AS e
  RIGHT JOIN scott.emp AS m ON e.mgr = m.empno)
SELECT (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.m_k
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.m_sal) AS sal
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_sal;
-- +---------+----------+---------+----------+
-- | E_COUNT | E_SAL    | M_COUNT | M_SAL    |
-- +---------+----------+---------+----------+
-- |      13 | 24025.00 |      14 | 29025.00 |
-- +---------+----------+---------+----------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B12. FULL OUTER JOIN.
-- Both sides keep all 14 employees. Each side counts each employee once,
-- although the join has 22 rows.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(e.emp_count) AS e_count,
--          AGG(e.sum_sal) AS e_sal,
--          AGG(m.emp_count) AS m_count,
--          AGG(m.sum_sal) AS m_sal
--   FROM scott_model.emp AS e
--   FULL JOIN scott_model.emp AS m ON e.mgr = m.empno;
WITH joined AS (
  SELECT e.empno AS e_k, e.sal AS e_sal,
         m.empno AS m_k, m.sal AS m_sal
  FROM scott.emp AS e
  FULL JOIN scott.emp AS m ON e.mgr = m.empno)
SELECT (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.m_k
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.m_sal) AS sal
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_sal;
-- +---------+----------+---------+----------+
-- | E_COUNT | E_SAL    | M_COUNT | M_SAL    |
-- +---------+----------+---------+----------+
-- |      14 | 29025.00 |      14 | 29025.00 |
-- +---------+----------+---------+----------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B13. CROSS JOIN.
-- The cross join has no condition and gives 14 x 14 = 196 rows. Each side
-- still counts each employee once. Naive SUM(e.sal) is 406350.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(e.emp_count) AS e_count,
--          AGG(e.sum_sal) AS e_sal,
--          AGG(m.emp_count) AS m_count,
--          AGG(m.sum_sal) AS m_sal
--   FROM scott_model.emp AS e
--   CROSS JOIN scott_model.emp AS m;
WITH joined AS (
  SELECT e.empno AS e_k, e.sal AS e_sal,
         m.empno AS m_k, m.sal AS m_sal
  FROM scott.emp AS e
  CROSS JOIN scott.emp AS m)
SELECT (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL
              GROUP BY j.e_k) AS x) AS e_sal,
       (SELECT COUNT(*)
        FROM (SELECT j.m_k
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.m_sal) AS sal
              FROM joined AS j
              WHERE j.m_k IS NOT NULL
              GROUP BY j.m_k) AS x) AS m_sal;
-- +---------+----------+---------+----------+
-- | E_COUNT | E_SAL    | M_COUNT | M_SAL    |
-- +---------+----------+---------+----------+
-- |      14 | 29025.00 |      14 | 29025.00 |
-- +---------+----------+---------+----------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B14-B17. One dept-emp query with inner and outer joins.
--
-- The query computes the payroll per department as a ratio of two measures
-- from different relations: AGG(e.sum_sal) / AGG(d.dept_count). The join
-- repeats each dept row once for each employee in it (fan-out), but
-- dept_count still counts each department once. Naive COUNT(d.deptno) is 14
-- for INNER and 15 for LEFT.
--
-- The join type is the tool's choice, and it changes the answer (spec 10.2,
-- A.6). The LEFT join keeps OPERATIONS, which has no employees, so the
-- payroll is divided by 4 departments and not by 3. A filter in the ON clause
-- of the LEFT join keeps all 4 departments but matches only the clerks.
-- A RIGHT join with the sides swapped, or a FULL join, gives the LEFT
-- result, because every employee has a department.
-- Spec: 7.2, 7.6, 8.1, A.6
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- B14. INNER JOIN.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(d.dept_count) AS dept_count,
--          AGG(e.emp_count) AS emp_count,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(e.sum_sal) / AGG(d.dept_count) AS sal_per_dept
--   FROM scott_model.dept AS d
--   JOIN scott_model.emp AS e ON e.deptno = d.deptno;
WITH joined AS (
  SELECT d.deptno AS d_k,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.dept AS d
  JOIN scott.emp AS e ON e.deptno = d.deptno),
measures AS (
  SELECT (SELECT COUNT(*)
          FROM (SELECT j.d_k
                FROM joined AS j
                WHERE j.d_k IS NOT NULL
                GROUP BY j.d_k) AS x) AS dept_count,
         (SELECT COUNT(*)
          FROM (SELECT j.e_k
                FROM joined AS j
                WHERE j.e_k IS NOT NULL
                GROUP BY j.e_k) AS x) AS emp_count,
         (SELECT SUM(x.sal)
          FROM (SELECT ANY_VALUE(j.e_sal) AS sal
                FROM joined AS j
                WHERE j.e_k IS NOT NULL
                GROUP BY j.e_k) AS x) AS sum_sal)
SELECT dept_count, emp_count, sum_sal, sum_sal / dept_count AS sal_per_dept
FROM measures;
-- +------------+-----------+----------+--------------+
-- | DEPT_COUNT | EMP_COUNT | SUM_SAL  | SAL_PER_DEPT |
-- +------------+-----------+----------+--------------+
-- |          3 |        14 | 29025.00 |      9675.00 |
-- +------------+-----------+----------+--------------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B15. LEFT JOIN.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(d.dept_count) AS dept_count,
--          AGG(e.emp_count) AS emp_count,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(e.sum_sal) / AGG(d.dept_count) AS sal_per_dept
--   FROM scott_model.dept AS d
--   LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno;
WITH joined AS (
  SELECT d.deptno AS d_k,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.dept AS d
  LEFT JOIN scott.emp AS e ON e.deptno = d.deptno),
measures AS (
  SELECT (SELECT COUNT(*)
          FROM (SELECT j.d_k
                FROM joined AS j
                WHERE j.d_k IS NOT NULL
                GROUP BY j.d_k) AS x) AS dept_count,
         (SELECT COUNT(*)
          FROM (SELECT j.e_k
                FROM joined AS j
                WHERE j.e_k IS NOT NULL
                GROUP BY j.e_k) AS x) AS emp_count,
         (SELECT SUM(x.sal)
          FROM (SELECT ANY_VALUE(j.e_sal) AS sal
                FROM joined AS j
                WHERE j.e_k IS NOT NULL
                GROUP BY j.e_k) AS x) AS sum_sal)
SELECT dept_count, emp_count, sum_sal, sum_sal / dept_count AS sal_per_dept
FROM measures;
-- +------------+-----------+----------+--------------+
-- | DEPT_COUNT | EMP_COUNT | SUM_SAL  | SAL_PER_DEPT |
-- +------------+-----------+----------+--------------+
-- |          4 |        14 | 29025.00 |      7256.25 |
-- +------------+-----------+----------+--------------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B16. LEFT JOIN with a filter in the ON clause.
-- The condition e.job = 'CLERK' in ON keeps every department and matches only
-- clerks. If the condition were in WHERE, the query would remove the
-- NULL-padded OPERATIONS row, and dept_count would be 3.
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT AGG(d.dept_count) AS dept_count,
--          AGG(e.emp_count) AS emp_count,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(e.sum_sal) / AGG(d.dept_count) AS sal_per_dept
--   FROM scott_model.dept AS d
--   LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno AND e.job = 'CLERK';
WITH joined AS (
  SELECT d.deptno AS d_k,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.dept AS d
  LEFT JOIN scott.emp AS e ON e.deptno = d.deptno AND e.job = 'CLERK'),
measures AS (
  SELECT (SELECT COUNT(*)
          FROM (SELECT j.d_k
                FROM joined AS j
                WHERE j.d_k IS NOT NULL
                GROUP BY j.d_k) AS x) AS dept_count,
         (SELECT COUNT(*)
          FROM (SELECT j.e_k
                FROM joined AS j
                WHERE j.e_k IS NOT NULL
                GROUP BY j.e_k) AS x) AS emp_count,
         (SELECT SUM(x.sal)
          FROM (SELECT ANY_VALUE(j.e_sal) AS sal
                FROM joined AS j
                WHERE j.e_k IS NOT NULL
                GROUP BY j.e_k) AS x) AS sum_sal)
SELECT dept_count, emp_count, sum_sal, sum_sal / dept_count AS sal_per_dept
FROM measures;
-- +------------+-----------+---------+--------------+
-- | DEPT_COUNT | EMP_COUNT | SUM_SAL | SAL_PER_DEPT |
-- +------------+-----------+---------+--------------+
-- |          4 |         4 | 4150.00 |      1037.50 |
-- +------------+-----------+---------+--------------+
-- (1 row)

-- ----------------------------------------------------------------------------
-- B17. LEFT JOIN, grouped: a group with an empty input set.
-- The B15 query, grouped by department. The OPERATIONS group stays in the
-- result. Its joined row is NULL-padded on the emp side, so the emp measures
-- take their empty-input values: 0 for COUNT, NULL for SUM and AVG. The
-- INNER join of B1 omits OPERATIONS.
-- Spec: 7.6, 8.1, A.6
-- ----------------------------------------------------------------------------
-- Ossie:
--   SELECT d.dname,
--          AGG(e.emp_count) AS emp_count,
--          AGG(e.sum_sal) AS sum_sal,
--          AGG(e.avg_sal) AS avg_sal
--   FROM scott_model.dept AS d
--   LEFT JOIN scott_model.emp AS e ON e.deptno = d.deptno
--   GROUP BY d.dname
--   ORDER BY d.dname;
WITH joined AS (
  SELECT d.dname,
         e.empno AS e_k, e.sal AS e_sal
  FROM scott.dept AS d
  LEFT JOIN scott.emp AS e ON e.deptno = d.deptno),
spine AS (SELECT DISTINCT dname FROM joined)
SELECT g.dname,
       (SELECT COUNT(*)
        FROM (SELECT j.e_k
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS emp_count,
       (SELECT SUM(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS sum_sal,
       (SELECT AVG(x.sal)
        FROM (SELECT ANY_VALUE(j.e_sal) AS sal
              FROM joined AS j
              WHERE j.e_k IS NOT NULL AND j.dname IS NOT DISTINCT FROM g.dname
              GROUP BY j.e_k) AS x) AS avg_sal
FROM spine AS g
ORDER BY g.dname;
-- +------------+-----------+----------+---------+
-- | DNAME      | EMP_COUNT | SUM_SAL  | AVG_SAL |
-- +------------+-----------+----------+---------+
-- | ACCOUNTING |         3 |  8750.00 | 2916.67 |
-- | OPERATIONS |         0 |          |         |
-- | RESEARCH   |         5 | 10875.00 | 2175.00 |
-- | SALES      |         6 |  9400.00 | 1566.67 |
-- +------------+-----------+----------+---------+
-- (4 rows)

-- End relational_semantics.sql
