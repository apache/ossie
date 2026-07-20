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

# Apache Ossie Common

This Java module generates Jackson-compatible POJOs from the canonical
[`core-spec/osi-schema.json`](../../../core-spec/osi-schema.json) with
`jsonschema2pojo-maven-plugin`. Generated sources are written under `target/`
and are not checked into source control.

## Build

Java 17 or newer and Maven 3.9.12 or newer are required by the code-generation
plugin. The generated artifact targets Java 11 so it can be shared by the
existing Java converters.

```bash
mvn clean verify
```

The generated model classes use the `org.apache.ossie.model` package. Install
the artifact into the local Maven repository for use by another converter:

```bash
mvn install
```

```xml
<dependency>
    <groupId>org.apache.ossie</groupId>
    <artifactId>ossie-common</artifactId>
    <version>0.1.0-SNAPSHOT</version>
</dependency>
```

## Validation boundary

The generated classes are transport POJOs, not a JSON Schema validator.
Constraints such as `required`, `const`, and `minItems` must be checked against
the canonical schema before deserialization. In particular, `ai_context` is
generated as `Object` because its schema accepts either a string or a structured
object and `jsonschema2pojo` does not generate a typed `oneOf` union. Consumers
should validate input first and then handle this value as `String` or `Map`.
