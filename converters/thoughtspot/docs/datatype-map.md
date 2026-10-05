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

<!-- GENERATED FILE -- do not edit by hand.
     Produced by `tools/generate_reference_docs.py` from:
     - `src/ossie_thoughtspot/datatypes.py`
     Regenerate with:
       uv run --python 3.13 python tools/generate_reference_docs.py
     tests/test_reference_docs_current.py fails the suite if this file
     drifts from what the generator currently produces. -->

# Ossie <-> ThoughtSpot Datatype Map

The bidirectional Ossie <-> ThoughtSpot TML datatype map. The map is **not injective** — several Ossie types collapse onto one TML spelling and cannot be told apart on the way back; see "Not injective" below.

## The closed Ossie datatype enum

`Boolean`, `Date`, `DateTime`, `DateTimeTz`, `Decimal`, `Float`, `Integer`, `Opaque`, `String`, `Time`

## Ossie -> TML

| Ossie datatype | TML `data_type` (default) | Notes |
|---|---|---|
| `Boolean` | `BOOLEAN` | connection-dependent spelling — `BOOLEAN` by default, `BOOL` when the connection's own TML spells it that way |
| `Date` | `DATE` | exact, single spelling |
| `DateTime` | `DATE_TIME` | exact, single spelling |
| `DateTimeTz` | `DATE_TIME` | ThoughtSpot has no offset-aware column type; the value becomes DATE_TIME and returns as DateTime. |
| `Decimal` | `DOUBLE` | exact, single spelling |
| `Float` | `DOUBLE` | connection-dependent spelling — `DOUBLE` by default, `FLOAT` when the connection's own TML spells it that way; ThoughtSpot has one approximate numeric type, so Float and Decimal both become DOUBLE and return as Decimal. |
| `Integer` | `INT64` | exact, single spelling |
| `Opaque` | `VARCHAR` | Opaque is Ossie's marker for a type outside the portable vocabulary; it becomes VARCHAR and returns as String. |
| `String` | `VARCHAR` | exact, single spelling |
| `Time` | `VARCHAR` | ThoughtSpot has no time-of-day column type; the value becomes VARCHAR. |

A column with no declared `datatype` at all infers `INT64` rather than raising — `datatype` is optional in Ossie, but TML rejects a column with no `db_column_properties` block at all.

## TML -> Ossie

| TML `data_type` | Ossie datatype |
|---|---|
| `BOOL` | `Boolean` |
| `BOOLEAN` | `Boolean` |
| `DATE` | `Date` |
| `DATE_TIME` | `DateTime` |
| `DOUBLE` | `Decimal` |
| `FLOAT` | `Float` |
| `INT64` | `Integer` |
| `VARCHAR` | `String` |

A TML `data_type` outside this map returns no Ossie datatype at all — `datatype` is optional in Ossie, so omitting it is preferred over inventing one.

## Not injective — declared losses

| Ossie datatype | Why the round trip is lossy |
|---|---|
| `DateTimeTz` | ThoughtSpot has no offset-aware column type; the value becomes DATE_TIME and returns as DateTime. |
| `Float` | ThoughtSpot has one approximate numeric type, so Float and Decimal both become DOUBLE and return as Decimal. |
| `Opaque` | Opaque is Ossie's marker for a type outside the portable vocabulary; it becomes VARCHAR and returns as String. |
| `Time` | ThoughtSpot has no time-of-day column type; the value becomes VARCHAR. |

