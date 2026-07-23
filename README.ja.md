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

# Apache Ossie（インキュベーション中）

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Apache Ossie は、データ分析、AI、BI のエコシステムに存在する多様なツールやプラットフォーム間で、セマンティックモデルの交換と利用を標準化・効率化するための、協力型オープンソースプロジェクトです。私たちが共有するビジョンは、共通かつベンダーに依存しないセマンティックモデル仕様を確立し、すべての参加者に比類のない相互運用性、効率性、コラボレーションをもたらすことです。このベンダー非依存の標準は、単一で一貫した信頼できる情報源を提供します。これにより、データの定義と価値は、AI エージェント、BI プラットフォーム、エコシステム内のその他すべてのツールの間で交換されても一貫性を保ち、異なるツール間の不整合を解消します。

Apache Ossie は、以前は **Open Semantic Interchange (OSI)** と呼ばれていました。

Apache Ossie は、あらゆるツールが読み書きできる、JSON および YAML ベースの単一仕様を提供します。これは、今日のデータスタックで一般的なセマンティックの断片化に対処するものです。たとえば、同じ KPI がツールごとに異なって定義されること、チームが定義の手作業による調整に多大な労力を費やすこと、一貫性のないビジネスロジックに基づいて AI エージェントが信頼できない出力を生成することなどです。

## このリポジトリの内容

- [`core-spec/`](core-spec/) — Ossie コア仕様（`spec.md`）、機械可読スキーマ（`spec.yaml`、`osi-schema.json`）、および関連ドキュメント。
- [`converters/`](converters/) — Ossie と他のセマンティック形式（dbt、GoodData、Polaris、Salesforce など）の間で変換するリファレンスコンバーター。
- [`examples/`](examples/) — 完全な TPC-DS モデルを含むセマンティックモデルの例。
- [`validation/`](validation/) — Ossie スキーマに照らしてセマンティックモデルを検証するためのツール。
- [`docs/`](docs/) — プロジェクトのドキュメントと概要。

## プロジェクトへの参加

- **コントリビューション：** 仕様変更の提案、コードの提供、コミュニティへの参加方法については、[CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。
- **ロードマップ：** 現在のワーキンググループ、今後の取り組み、コミュニティでの議論を踏まえた改善計画については、[ROADMAP.md](ROADMAP.md) を参照してください。
- **ディスカッション：** [GitHub Discussions](https://github.com/apache/ossie/discussions) と [Issues](https://github.com/apache/ossie/issues) で会話に参加してください。
- **Slack コミュニティへの参加：** [Slack](https://join.slack.com/t/apache-ossie/shared_invite/zt-42zw4rflt-Gpve8_NFJq7AsdAQTY~SCg) でコントリビューターと直接交流できます。
