# OSI

The **Open Semantic Interchange (OSI)** initiative is a collaborative, open-source effort dedicated to standardizing and streamlining semantic model exchange and utilization across the diverse array of tools and platforms within the data analytics, AI, and BI ecosystem. Our shared vision is to establish a common, vendor-agnostic semantic model specification, promoting unparalleled interoperability, efficiency, and collaboration among all participants. By providing a single, consistent source of truth, this vendor-agnostic standard ensures that your data's definitions and value remain consistent as they are interchanged between AI agents, BI platforms, and all other tools in your ecosystem, eliminating inconsistencies across your different tools.

OSI provides a single JSON- and YAML-based specification that any tool can read and write, addressing the semantic fragmentation common across today's data stack: the same KPI defined differently across tools, teams spending significant effort manually reconciling definitions, and AI agents producing unreliable outputs grounded in inconsistent business logic.

## What's in this repository

- [`core-spec/`](core-spec/) — The OSI core specification (`spec.md`), the machine-readable schema (`spec.yaml`, `osi-schema.json`), and accompanying documentation.
- [`converters/`](converters/) — Reference converters that translate between OSI and other semantic formats (e.g., dbt, GoodData, Polaris, Salesforce).
- [`examples/`](examples/) — Example semantic models, including a complete TPC-DS model.
- [`validation/`](validation/) — Tooling for validating semantic models against the OSI schema.
- [`docs/`](docs/) — Project documentation and overview.

## Get involved

- **Contribute:** See [CONTRIBUTING.md](CONTRIBUTING.md) for how to propose specification changes, contribute code, and participate in the community.
- **Roadmap:** See [ROADMAP.md](ROADMAP.md) for current working groups, future efforts, and planned enhancements informed by community discussion.
- **Discuss:** Join the conversation on [GitHub Discussions](https://github.com/open-semantic-interchange/OSI/discussions) and [Issues](https://github.com/open-semantic-interchange/OSI/issues).
- **Join the Slack community:** Chat directly with contributors on [Slack](https://join.slack.com/t/opensemanticx/shared_invite/zt-3yuad6c0h-MaoPgVSD1g9MEOf1_QeaiQ).

# License

All code in this repository is licensed under the Apache 2.0 license.

The specification and documentation is licensed under the Creative Commons Attribution license (CC BY).
