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

BI SQL examples
===============

Real SQL that BI tools generate when they query, via SQL, metrics defined outside
the tool. The
goal is to study these query shapes to ensure that Ossie can fully and safely
cover the complex cases BI tools produce.

The examples read measures with the `MEASURE()` function, but that is only for
illustration: any proposal will require the BI tool to have some function for
querying measures.

Layout
------

One subfolder per BI tool. Each subfolder holds a `setup.sql` that shows the
shape of the tables and metrics, one `.sql` file per feature area, and a
`README.md`.
