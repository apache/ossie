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

-- The SCOTT schema, loaded by connect("scott").

create schema scott;

create table scott.dept (
  deptno tinyint not null primary key,
  dname varchar(14),
  loc varchar(13));

insert into scott.dept values
  (10, 'ACCOUNTING', 'NEW YORK'),
  (20, 'RESEARCH', 'DALLAS'),
  (30, 'SALES', 'CHICAGO'),
  (40, 'OPERATIONS', 'BOSTON');

create table scott.emp (
  empno smallint not null primary key,
  ename varchar(10),
  job varchar(9),
  mgr smallint,
  hiredate date,
  sal decimal(7, 2),
  comm decimal(7, 2),
  deptno tinyint);

insert into scott.emp values
  (7369, 'SMITH', 'CLERK', 7902, date '1980-12-17', 800.00, null, 20),
  (7499, 'ALLEN', 'SALESMAN', 7698, date '1981-02-20', 1600.00, 300.00, 30),
  (7521, 'WARD', 'SALESMAN', 7698, date '1981-02-22', 1250.00, 500.00, 30),
  (7566, 'JONES', 'MANAGER', 7839, date '1981-02-04', 2975.00, null, 20),
  (7654, 'MARTIN', 'SALESMAN', 7698, date '1981-09-28', 1250.00, 1400.00, 30),
  (7698, 'BLAKE', 'MANAGER', 7839, date '1981-01-05', 2850.00, null, 30),
  (7782, 'CLARK', 'MANAGER', 7839, date '1981-06-09', 2450.00, null, 10),
  (7788, 'SCOTT', 'ANALYST', 7566, date '1987-04-19', 3000.00, null, 20),
  (7839, 'KING', 'PRESIDENT', null, date '1981-11-17', 5000.00, null, 10),
  (7844, 'TURNER', 'SALESMAN', 7698, date '1981-09-08', 1500.00, 0.00, 30),
  (7876, 'ADAMS', 'CLERK', 7788, date '1987-05-23', 1100.00, null, 20),
  (7900, 'JAMES', 'CLERK', 7698, date '1981-12-03', 950.00, null, 30),
  (7902, 'FORD', 'ANALYST', 7566, date '1981-12-03', 3000.00, null, 20),
  (7934, 'MILLER', 'CLERK', 7782, date '1982-01-23', 1300.00, null, 10);

create table scott.bonus (
  ename varchar(10),
  job varchar(9),
  sal decimal(7, 2),
  comm decimal(7, 2));

create table scott.salgrade (
  grade integer,
  losal decimal(7, 2),
  hisal decimal(7, 2));

insert into scott.salgrade values
  (1, 700, 1200),
  (2, 1201, 1400),
  (3, 1401, 2000),
  (4, 2001, 3000),
  (5, 3001, 9999);

set search_path = 'scott,main';
