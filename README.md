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

# Apache Ossie (incubating)

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Apache Ossie is a collaborative, open-source effort dedicated to standardizing and streamlining semantic model exchange and utilization across the diverse array of tools and platforms within the data analytics, AI, and BI ecosystem. Our shared vision is to establish a common, vendor-agnostic semantic model specification, promoting unparalleled interoperability, efficiency, and collaboration among all participants. By providing a single, consistent source of truth, this vendor-agnostic standard ensures that your data's definitions and value remain consistent as they are interchanged between AI agents, BI platforms, and all other tools in your ecosystem, eliminating inconsistencies across your different tools.

Apache Ossie was formerly known as **Open Semantic Interchange (OSI)**.

Apache Ossie provides a single JSON- and YAML-based specification that any tool can read and write, addressing the semantic fragmentation common across today's data stack: the same KPI defined differently across tools, teams spending significant effort manually reconciling definitions, and AI agents producing unreliable outputs grounded in inconsistent business logic.

## What's in this repository

- [`core-spec/`](core-spec/) — The Ossie core specification (`spec.md`), the machine-readable schema (`spec.yaml`, `osi-schema.json`), and accompanying documentation.
- [`converters/`](converters/) — Reference converters that translate between Ossie and other semantic formats (e.g., dbt, GoodData, Polaris, Salesforce).
- [`examples/`](examples/) — Example semantic models, including a complete TPC-DS model.
- [`validation/`](validation/) — Tooling for validating semantic models against the Ossie schema.
- [`docs/`](docs/) — Project documentation and overview.

## Get involved

- **Contribute:** See [CONTRIBUTING.md](CONTRIBUTING.md) for how to propose specification changes, contribute code, and participate in the community.
- **Roadmap:** See [ROADMAP.md](ROADMAP.md) for current working groups, future efforts, and planned enhancements informed by community discussion.
- **Discuss:** Join the conversation on [GitHub Discussions](https://github.com/apache/ossie/discussions) and [Issues](https://github.com/apache/ossie/issues).
- **Join the Slack community:** Chat directly with contributors on [Slack](https://join.slack.com/t/apache-ossie/shared_invite/zt-42zw4rflt-Gpve8_NFJq7AsdAQTY~SCg).
