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

# Apache Ossie（孵化中）

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Apache Ossie 是一项协作式开源计划，致力于在数据分析、AI 和 BI 生态系统中种类繁多的工具与平台之间，推动语义模型交换和使用方式的标准化与简化。我们的共同愿景是建立一套通用、厂商无关的语义模型规范，为所有参与者带来前所未有的互操作性、效率与协作能力。这项厂商无关的标准提供单一且一致的事实来源，确保数据的定义和价值在 AI 智能体、BI 平台及生态系统中的其他工具之间交换时保持一致，从而消除不同工具之间的不一致。

Apache Ossie 的原名为 **Open Semantic Interchange (OSI)**。

Apache Ossie 提供一套基于 JSON 和 YAML 的统一规范，任何工具都可以读写。它旨在解决当今数据技术栈中普遍存在的语义碎片化问题，例如：同一个 KPI 在不同工具中定义不一致、团队耗费大量精力手动协调定义，以及 AI 智能体基于不一致的业务逻辑生成不可靠的输出。

## 本仓库包含什么

- [`core-spec/`](core-spec/) — Ossie 核心规范（`spec.md`）、机器可读的 Schema（`spec.yaml`、`osi-schema.json`）及相关文档。
- [`converters/`](converters/) — 在 Ossie 与其他语义格式（例如 dbt、GoodData、Polaris、Salesforce）之间进行转换的参考转换器。
- [`examples/`](examples/) — 语义模型示例，其中包括完整的 TPC-DS 模型。
- [`validation/`](validation/) — 用于依据 Ossie Schema 验证语义模型的工具。
- [`docs/`](docs/) — 项目文档和概览。

## 参与项目

- **贡献：** 有关如何提出规范变更、贡献代码以及参与社区的信息，请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。
- **路线图：** 有关当前工作组、未来工作以及由社区讨论推动的计划改进，请参阅 [ROADMAP.md](ROADMAP.md)。
- **讨论：** 在 [GitHub Discussions](https://github.com/apache/ossie/discussions) 和 [Issues](https://github.com/apache/ossie/issues) 中参与交流。
- **加入 Slack 社区：** 在 [Slack](https://join.slack.com/t/apache-ossie/shared_invite/zt-42zw4rflt-Gpve8_NFJq7AsdAQTY~SCg) 上直接与贡献者交流。
