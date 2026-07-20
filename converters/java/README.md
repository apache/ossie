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

# Apache Ossie Java Converters

This Maven reactor contains the shared schema-generated Java model and all Java
converters:

- `ossie-common` generates `org.apache.ossie.model` from the canonical Ossie
  JSON Schema.
- `polaris` consumes those model classes directly for YAML parsing, generation,
  import, and export.
- `salesforce` uses those model classes at its Ossie input and output boundaries
  while retaining its dynamic vendor-mapping pipeline internally.

## Build

Java 17 or newer and Maven 3.9.12 or newer are required to build the reactor.

```bash
cd converters/java
mvn clean verify
```

Build one converter together with its required modules using `-am`:

```bash
mvn -pl polaris -am clean package
mvn -pl salesforce -am clean package
```
