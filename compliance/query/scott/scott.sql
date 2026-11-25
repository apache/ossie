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
-- Data: the SCOTT schema
--
-- Shared input data for the query compliance cases in ../cases/. Load this
-- file before a case runs. Do not change the values: the expected results of
-- all cases depend on them.
--
-- The SCOTT tables have the same values as in
-- https://github.com/julianhyde/scott-data-hsqldb, except that BONUS, which
-- is empty there, has three rows. Our SCOTT also has a table PET.
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

-- Bonuses, in comm. JONES, who has three pets, has two bonuses, and KING,
-- who has no pet, has one. The table has no key, but each row is distinct.
CREATE TABLE scott.bonus (
  ename VARCHAR NOT NULL,
  job   VARCHAR NOT NULL,
  sal   DECIMAL(7, 2) NOT NULL,
  comm  DECIMAL(7, 2) NOT NULL
);

INSERT INTO scott.bonus VALUES
  ('JONES', 'MANAGER',   2975.00,  200.00),
  ('JONES', 'MANAGER',   2975.00,  300.00),
  ('KING',  'PRESIDENT', 5000.00, 1000.00);

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

-- Pets, and their owners. TRAMP has no owner.
CREATE TABLE scott.pet (
  pname   VARCHAR NOT NULL PRIMARY KEY,
  empno   INTEGER REFERENCES scott.emp (empno),
  species VARCHAR NOT NULL,
  age     INTEGER NOT NULL
);

INSERT INTO scott.pet VALUES
  ('FELIX',   7566, 'CAT',     5),
  ('HAMMY',   7876, 'HAMSTER', 1),
  ('OREO',    7566, 'CAT',     2),
  ('RUBY',    7566, 'DOG',     7),
  ('SNOOPY',  7499, 'DOG',     7),
  ('STEALTH', 7902, 'CAT',     5),
  ('TIGER',   7788, 'CAT',     3),
  ('TRAMP',   NULL, 'DOG',     4);
