<!--
 Licensed to the Apache Software Foundation (ASF) under one
 or more contributor license agreements.  See the NOTICE file
 distributed with this work for additional information
 regarding copyright ownership.  The ASF licenses this file
 to you under the Apache License, Version 2.0 (the
 "License"); you may not use this file except in compliance
 with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing,
 software distributed under the License is distributed on an
 "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations
 under the License.
-->

Tableau BI SQL examples
=======================

Real SQL that [Tableau](https://www.tableau.com/) generated while querying metrics
defined outside the tool. The proof of concept ran against a development build of
Databricks Metric Views, but the approach should work with a SQL interface to Ossie.

The queries in this folder are captured for their SQL shape. `setup.sql` shows the model
shape generally and is not runnable; the proof of concept used Databricks Metric
View DDL for the actual model.

How the SQL was produced
------------------------

Tableau queried the model through its existing SQL generation pipeline, with two
workarounds because this Tableau build does not query these natively:

- **Measures**: the POC used Tableau's
  [RAWSQLAGG](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_passthrough.htm)
  functions so that it could invoke the measure function. Every measure is
  evaluated on the engine side.
- **Multi-table models**: built by hand in Tableau using joins and
  relationships.
