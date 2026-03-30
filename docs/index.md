# Open Semantic Interchange (OSI)

## Overview

The [Open Semantic Interchange (OSI)](https://open-semantic-interchange.org/) initiative is a collaborative, open-source effort dedicated to standardizing and streamlining semantic model exchange and utilization across the data analytics, AI, and BI ecosystem. Our shared vision is to establish a common, vendor-agnostic semantic model specification, promoting interoperability, efficiency, and collaboration among all participants.

By providing a single, consistent source of truth, the OSI standard ensures that your data's definitions and value remain consistent as they are interchanged between AI agents, BI platforms, and all other tools in your ecosystem — eliminating inconsistencies across your different tools.

### The Problem: Semantic Fragmentation

Today's data ecosystem is fragmented. Organizations rely on a wide array of tools for analytics, business intelligence, data engineering, and AI — each with its own way of defining and interpreting semantic models. This fragmentation leads to:

- **Metric Drift**: The same KPI is defined differently across dashboards and platforms, leading to conflicting numbers and eroded trust in data.
- **Manual Translation**: Teams spend significant effort manually reconciling semantic definitions when data moves between systems — an expensive and error-prone process.
- **AI Hallucinations**: When AI agents encounter conflicting or incomplete business logic across tools, they produce unreliable outputs grounded in inconsistent data definitions.
- **Integration Debt**: Every new tool added to the stack requires custom integration work, creating a web of brittle, point-to-point connectors that are costly to maintain.

### How OSI Solves It

OSI addresses semantic fragmentation by providing:

- **Single Source of Truth**: A unified specification for semantic and metric definitions that all tools can read and write, ensuring consistency across the entire data stack.
- **Native Interoperability**: A hub-and-spoke model where tools exchange semantic models through OSI as a common format — enabling direct platform-to-platform exchange without custom connectors.
- **Trusted AI Grounding**: Consistent business logic and rich AI context annotations ensure that AI agents and LLMs can reliably interpret and query data.
- **Reduced Total Cost of Ownership**: Automated model exchange eliminates manual reconciliation work and reduces the engineering effort needed to integrate new tools.

### Specification at a Glance

The OSI core specification (current version: **0.1.1**) defines a YAML-based format for describing semantic models. The key constructs are:

| Construct | Description |
|-----------|-------------|
| **Semantic Model** | The top-level container representing a complete semantic model, including datasets, relationships, and metrics. |
| **Datasets** | Logical datasets representing business entities (fact and dimension tables), with fields, primary keys, and unique keys. |
| **Fields** | Row-level attributes for grouping, filtering, and metric expressions. Fields support multiple SQL dialects for cross-platform compatibility. |
| **Relationships** | Foreign key connections between datasets, supporting both simple and composite keys. |
| **Metrics** | Quantitative measures (sums, averages, ratios, etc.) defined at the model level, capable of spanning multiple datasets. |
| **Custom Extensions** | Vendor-specific metadata stored as JSON, allowing platforms to carry additional information without breaking core compatibility. |
| **AI Context** | Optional annotations at every level (model, dataset, field, relationship, metric) to help AI tools understand business meaning — including instructions, synonyms, and example queries. |

The specification supports multiple SQL dialects (`ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS`, `MDX`, `TABLEAU`) so that expressions can be tailored to each platform while maintaining a common model structure.

For the full specification, see [core-spec/spec.md](../core-spec/spec.md). For validation tooling, see [validation/validate.py](../validation/validate.py). For a complete example, see the [TPC-DS semantic model](../examples/tpcds_semantic_model.yaml).

### Participating Organizations

OSI is supported by a broad coalition of 50+ organizations across the data ecosystem, including:

Alation, Anomalo, Atlan, AtScale, Bigeye, BlackRock, Blue Yonder, Carto, Cloudera, Coalesce, Collate, Collibra, Cogniti, Count, Credible, Cube, Databricks, DataHub, Denodo, dbt Labs, Dremio, Domo, Elementum AI, Firebolt, GoodData, Hex, Honeydew, Informatica, Instacart, JetBrains, Lightdash, Mistral AI, Omni, Oracle, Preset, Qlik, RelationalAI, Salesforce, Select Star, Sigma, Snowflake, Starburst Data, Strategy, Sundial, ThoughtSpot, and more.

### Converters

OSI converters follow a **hub-and-spoke** architecture: the OSI core specification acts as the central, vendor-neutral format, and each converter handles translation to or from a specific vendor format (e.g., Snowflake, dbt, Salesforce, Databricks). This avoids the need for point-to-point converters between every pair of vendors.

For details on implementing a converter, see the [Converters Guide](../converters/index.md).

---

## Project Governance

We wanted the OSI project to be a collaborative effort from the start. The purpose is to grow a community of developers, contributors, and users who are actively involved in shaping the OSI Specification as it moves along.
Open Semantic Interchange (OSI) project governance is inspired by the governance model from [The ASF](https://www.apache.org).

### The Apache Way

The Apache Way articulates around several values:

1. **Meritocracy**
Merit is based on your contribution, and it never expires. Those with merit get more responsibility.
Any constructive contribution earns merit — code, documentation, testing, community support, and specification reviews all count equally.

2. **Peer-based**
The community involves mutual trust and respect. Every participant is treated as a peer regardless of employer affiliation or seniority. Decisions are made by the community, not by any single company or individual.

3. **Consensus decision making**
We strive for consensus in all decisions. When full consensus cannot be reached, a formal vote may be called. This ensures that all voices are heard and that decisions reflect the broadest possible agreement within the community.

4. **Collaborative development and Open Communications**
All technical discussions, design decisions, and specification changes happen in the open — on GitHub issues, pull requests, discussions. Private decisions are discouraged. If it didn't happen on a public resource, it didn't happen.

5. **Responsible oversight**
The community collectively ensures that the specification and its associated tooling remain high quality, secure, and aligned with the project's mission of vendor-agnostic semantic interoperability.

6. **Independence**
The OSI project operates independently of any single vendor or organization. While contributors may be employed by companies that have a stake in semantic interoperability, the project's direction is determined by the community as a whole.

### Technical Governing Bodies

The **Technical Steering Committee (TSC)** is responsible for the overall technical direction of the OSI Specification. The TSC:

- Reviews and approves significant changes to the core specification
- Resolves technical disputes that cannot be settled through normal consensus
- Ensures backward compatibility and coherence across specification versions
- Oversees the roadmap for new specification features and extensions
- Has binding vote on the Specification Change vote

New TSC members are elected by the TSC members.

The TSC members are:

| Name | Affiliation |
|------|-------------|
| Khushboo Bhatia | Snowflake |
| Lior Ebel | Salesforce |
| Quigley Malcom | dbt Labs |
| JB Onofré | The ASF |

### Non-Governing Bodies

- **Contributors**: Anyone who contributes to the project in any form — code, documentation, bug reports, specification feedback, or community support. Contributors do not have binding vote rights but are encouraged to participate in all discussions.
- **Committers**: Contributors who have earned write access to the repository through sustained contributions. Committers can merge pull requests and have binding votes on non-specification matters.
- **Specification Reviewers**: Community members with domain expertise (e.g., in BI, AI, data engineering) who review proposed specification changes for correctness, completeness, and practical applicability.

### Vote on Specification Changes

Changes to the OSI Specification follow a structured voting process:

1. **Proposal**: A specification change is submitted as a GitHub pull request with a clear description of the motivation, the change itself, and its impact on existing implementations.
2. **Discussion period**: The community has a minimum of 7 days to review and discuss the proposed change. Complex changes may require a longer review window.
3. **Vote**: Once the discussion period has elapsed, a vote is called. TSC members and committers cast votes using the following convention:
   - **+1** (Yes): In favor of the change
   - **0** (Abstain): No opinion or willing to go with the majority
   - **-1** (Veto): Against the change — must be accompanied by a technical justification
  The votes from the TSC members are considered as binding votes.
4. **Resolution**: A specification change passes with at least 2 binding +1 votes and no vetoes. A veto can only be overridden by addressing the stated concern or by a supermajority (two-thirds) vote of the TSC.

### Working Groups

Working groups are focused teams that drive specific areas of the specification forward. Each working group has a designated lead and members drawn from across the participating organizations.

#### 1. Metric Language and Relationships

This group focuses on the expression language for defining metrics, including aggregation semantics, cross-dataset references, and the relationship model that connects datasets. Key areas include advanced metric types (ratios, cumulative metrics, derived metrics) and the rules governing how metrics traverse relationships.

| Name | Affiliation |
|------|-------------|
| Will Pugh (lead) | |
| Khushboo Bhatia | Snowflake |
| LLyod Tabb | Malloy |
| Dianne Wood | AtScale |
| Lior Ebel | Salesforce |
| Quigley Malcolm | dbt Labs |
| Justin Talbot | Databricks |
| Pavel Tiunov | Cube |
| Damian Waldron | Thoughtspot |
| Oliver Laslett | Lightdash |
| Martin Traverso | Starburst |
| Kurt | Relational AI |
| JB Onofré | The ASF |

#### 2. Composability

This group explores how semantic models can be composed from smaller, reusable building blocks — enabling modular model definitions, model inheritance, and layered semantic models that build on top of each other.

| Name | Affiliation |
|------|-------------|
| Dianne Wood (lead) | AtScale |
| Kevin Wang | Snowflake |
| LLyod Tabb | Malloy |
| Quigley Malcolm | dbt Labs |
| Kurt | Relational AI |
| Antonio Scaramuzzino | Thoughtspot |
| Martin Traverso | Starburst |

#### 3. Catalog

This group addresses how OSI semantic models integrate with data catalogs and metadata management platforms — including discovery, lineage, and the ability to register, search, and govern semantic models across an organization.

| Name | Affiliation |
|------|-------------|
| Shubham Bhargav (lead) | Atlan |
| Krzysztof | Colibra |
| Khushboo Bhatia | Snowflake |
| Akshat Shah | Thoughtspot |
| Matt Fuller | Starbust |
| JB Onofré | The ASF |

#### 4. Ontology

This group works on the formal ontological representation of semantic models — defining the conceptual framework and vocabulary for describing business entities, their properties, and the relationships between them at a higher level of abstraction.

| Name | Affiliation |
|------|-------------|
| Kurt (lead) | Relational AI |
| Krzysztof | Colibra |
| Will Pugh | Snowflake |
| Antonio Scaramuzzino | Thoughtspot |
| Matt Fuller | Starbust |
| JB Onofré | The ASF |

#### 5. Sync API

This group defines the API specification for synchronizing semantic models between platforms — enabling real-time or scheduled exchange of semantic model updates across tools in an automated fashion.

| Name | Affiliation |
|------|-------------|
| Francois Lopitaux (Lead) | Thoughtspot |
| Yogitha Chilukuri | Snowflake |
| JB Onofré | The ASF |

### Community Meetings

Community meetings are open to all participants and provide a forum for discussing specification progress, working group updates, and community-wide topics. Meetings are held at both the global level and within individual working groups.

- **Global Community Meetings**: Regular meetings open to all community members for cross-cutting discussions, roadmap reviews, and community announcements.
- **Working Group Meetings**: Each working group holds its own meetings to drive focused progress on their specific area of the specification.

Meeting schedules, agendas, and notes are published on the [OSI website](https://open-semantic-interchange.org/) and the project's GitHub repository. All community members are welcome to attend and participate.

---

## Architecture

### Hub-and-Spoke Model

OSI is designed around a hub-and-spoke architecture that dramatically simplifies the integration landscape. Instead of requiring every tool to build custom connectors to every other tool, OSI acts as the universal interchange format at the center.

```
                        ┌─────────────┐
                        │  Snowflake  │
                        └──────┬──────┘
                               │
      ┌─────────────┐    ┌─────┴─────┐    ┌─────────────┐
      │     dbt     ├────┤    OSI    ├────┤  Salesforce │
      └─────────────┘    └─────┬─────┘    └─────────────┘
                               │
                        ┌──────┴──────┐
                        │  Databricks │
                        └─────────────┘
```

With N vendors, a point-to-point strategy would require **N×(N-1)** converters. With OSI as the hub, only **2×N** converters are needed (one import and one export per vendor), and interoperability with all other vendors comes for free.

### How It Flows

A typical OSI-based workflow looks like this:

1. **Author**: A semantic model is authored in one tool (e.g., dbt) or directly in the OSI YAML format.
2. **Import**: If authored in a vendor tool, the vendor's import converter translates it into an OSI model, preserving vendor-specific metadata in `custom_extensions`.
3. **Validate**: The OSI model is validated against the [JSON Schema](../core-spec/osi-schema.json) and the [validation script](../validation/validate.py) to ensure correctness.
4. **Exchange**: The OSI model is shared — via Git, a data catalog, or a sync API — with other teams and tools.
5. **Export**: Each consuming tool's export converter translates the OSI model into its native format, selecting the appropriate SQL dialect and applying vendor-specific extensions.
6. **Round-Trip**: When changes are made in a downstream tool, they can be imported back into the OSI model, preserving all metadata for lossless round-tripping.

### Multi-Dialect Expression System

A key architectural feature of OSI is its multi-dialect expression system. Fields and metrics can carry expressions in multiple SQL dialects simultaneously:

```yaml
expression:
  dialects:
    - dialect: ANSI_SQL
      expression: LOWER(email)
    - dialect: SNOWFLAKE
      expression: LOWER(email)::VARCHAR
    - dialect: DATABRICKS
      expression: lower(email)
```

This allows a single semantic model to be consumed natively by different platforms without expression translation. Each converter selects the dialect that matches its target platform, falling back to `ANSI_SQL` when a platform-specific dialect is not available.

---

## Frequently Asked Questions

### General

**What is a semantic model?**
A semantic model is a structured description of business data that defines what datasets exist, what their fields mean, how datasets relate to each other, and what metrics (KPIs) can be computed from the data. It serves as a shared vocabulary between data producers and consumers — whether those consumers are humans using BI tools or AI agents generating queries.

**How is OSI different from existing standards?**
Most existing standards focus on data formats (e.g., Parquet, Arrow), query interfaces (e.g., ODBC, JDBC), or catalog metadata (e.g., Hive Metastore, OpenMetadata). OSI focuses specifically on the *semantic layer* — the business meaning, metric definitions, and relationships that sit on top of raw data. It is complementary to these other standards rather than a replacement.

**Is OSI tied to any specific vendor?**
No. OSI is vendor-agnostic by design. The specification is developed and governed by a community of contributors from many organizations. While vendor-specific metadata can be carried via `custom_extensions`, the core specification is neutral.

### Adoption

**Can I use OSI with my existing BI tool?**
Yes, as long as a converter exists (or is built) for your tool. The hub-and-spoke model means that adding OSI support to a single tool gives it interoperability with every other OSI-compatible tool. Check the [Converters Guide](../converters/index.md) for currently supported vendors.

**What if my vendor isn't supported yet?**
You can contribute a converter. The [Converters Guide](../converters/index.md) provides a step-by-step guide for implementing import and export converters for new vendors. The community is happy to help with design reviews and testing.

**Do I need to rewrite my existing semantic models?**
No. Import converters translate existing vendor-specific models into the OSI format automatically. Your existing models remain intact — OSI provides an additional interchange layer on top of them.

**How do I validate an OSI model?**
Use the [validation script](../validation/validate.py) included in the repository. It checks your model against the [JSON Schema](../core-spec/osi-schema.json), validates SQL expressions across dialects, and ensures referential integrity between datasets and relationships.

### Technical

**Why YAML and not JSON?**
YAML is more human-readable and easier to author by hand, which is important for a specification that teams may edit directly. The OSI JSON Schema is available for programmatic validation, and converters can work with either format.

**How does OSI handle vendor-specific features?**
Through `custom_extensions`. Each vendor can store arbitrary JSON metadata in extension blocks tagged with their vendor name. This metadata is preserved during round-trip conversions and ignored by tools that don't understand it — ensuring that no information is lost.

**Can metrics reference multiple datasets?**
Yes. Metrics are defined at the semantic model level (not within a dataset) and can reference fields from multiple datasets.

**What SQL dialects are supported?**
The current specification supports `ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS`, `MDX`, and `TABLEAU`. New dialects can be proposed through the standard specification change process.

---

## Versioning and Compatibility Policy

### Specification Versioning

The OSI specification follows [Semantic Versioning](https://semver.org/) (SemVer):

- **Major version** (e.g., 1.0.0 → 2.0.0): Breaking changes that are not backward compatible. Existing valid models may not be valid under the new version. Major version bumps are rare and go through an extended review and migration period.
- **Minor version** (e.g., 0.1.0 → 0.2.0): New features or constructs added in a backward-compatible way. Existing valid models remain valid.
- **Patch version** (e.g., 0.1.0 → 0.1.1): Bug fixes, clarifications, and editorial improvements to the specification. No functional changes.

### Backward Compatibility

The community is committed to minimizing breaking changes. When backward-incompatible changes are necessary:

1. They are flagged clearly in the specification changelog.
2. A migration guide is provided describing how to update existing models.
3. A deprecation period is observed when possible — deprecated constructs are marked in one version and removed in a subsequent version.

### Extension Compatibility

Custom extensions (`custom_extensions`) are explicitly outside the scope of core compatibility guarantees. Vendors are responsible for maintaining compatibility within their own extension schemas. However, the core specification guarantees that `custom_extensions` blocks are always preserved during round-trip conversions, even by tools that do not understand them.

---

## Adoption Guide

A practical guide for organizations looking to adopt OSI.

### Phase 1: Evaluate

- **Inventory your semantic layer**: Identify which tools in your organization define semantic models — BI platforms, data modeling tools, AI/ML pipelines, metrics stores.
- **Map your pain points**: Determine where semantic fragmentation causes the most friction — conflicting metric definitions, manual reconciliation, onboarding new tools.
- **Check converter availability**: Review the [Converters Guide](../converters/index.md) to see if converters exist for your tools. If not, assess the effort to build one.

### Phase 2: Pilot

- **Start with one model**: Choose a well-understood semantic model (e.g., a core sales or finance model) and express it in the OSI format.
- **Validate**: Run the [validation script](../validation/validate.py) to ensure the model conforms to the specification.
- **Test round-tripping**: If converters are available, export the OSI model to your vendor format and compare with the original. Identify any gaps or lossy conversions.
- **Gather feedback**: Share the pilot results with your data team and collect feedback on the experience.

### Phase 3: Expand

- **Convert additional models**: Gradually bring more semantic models into the OSI format, prioritizing those shared across multiple tools.
- **Integrate into workflows**: Add OSI validation to your CI/CD pipeline. Store OSI models in version control alongside your data code.
- **Automate synchronization**: Use converters (and eventually the Sync API) to automate the propagation of semantic model changes across your tool ecosystem.

### Phase 4: Govern

- **Establish ownership**: Define who owns each semantic model and who is responsible for approving changes.
- **Implement review processes**: Use the voting and review processes described in this document (or your own governance model) to manage specification changes.
- **Monitor consistency**: Regularly validate that the semantic models consumed by each tool are in sync with the authoritative OSI model.

---

## Glossary

| Term | Definition |
|------|------------|
| **Semantic Model** | A structured description of business data that defines datasets, fields, relationships, and metrics. It provides a shared vocabulary for interpreting data across tools and teams. |
| **Dataset** | A logical representation of a business entity, typically corresponding to a fact table or dimension table in a data warehouse. |
| **Field** | A row-level attribute within a dataset, used for grouping, filtering, or as part of metric expressions. Fields can be simple column references or computed expressions. |
| **Dimension** | A categorical attribute used to slice and filter data (e.g., region, product category, date). In OSI, dimensions are represented as fields with optional metadata such as `is_time`. |
| **Metric** | A quantitative measure computed by aggregating data across one or more datasets (e.g., total revenue, average order value). Metrics are defined at the semantic model level. |
| **Relationship** | A foreign key connection between two datasets, defining how they can be joined. Relationships are always many-to-one (from the referencing dataset to the referenced dataset). |
| **Dialect** | A specific SQL or expression language variant (e.g., `ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS`). OSI supports multiple dialects so expressions can be tailored to each platform. |
| **Custom Extension** | Vendor-specific metadata attached to any OSI construct as a JSON string. Extensions allow platforms to carry additional information without modifying the core specification. |
| **AI Context** | Optional annotations on any OSI construct (model, dataset, field, relationship, metric) that provide additional context for AI tools — including natural language instructions, synonyms, and example queries. |
| **Converter** | A tool that translates between the OSI format and a specific vendor's semantic model format. Converters come in pairs: import (vendor → OSI) and export (OSI → vendor). |
| **Hub-and-Spoke** | The architectural pattern used by OSI, where the specification acts as the central format (hub) and vendor converters act as spokes, avoiding the need for point-to-point integrations. |
| **Round-Trip Fidelity** | The ability to convert a model from one format to OSI and back without losing information. Achieved by preserving vendor-specific metadata in `custom_extensions`. |
| **Fact Table** | A dataset that records business events or transactions (e.g., sales, clicks, shipments). Fact tables typically contain numeric measures and foreign keys to dimension tables. |
| **Dimension Table** | A dataset that describes business entities referenced by fact tables (e.g., customers, products, dates). Dimension tables provide the context for analyzing facts. |
| **Semantic Layer** | The abstraction layer between raw data and business users/tools. It defines the business meaning of data, standardizes metric calculations, and provides a consistent query interface. |

---

## Related Resources

- **Website**: [open-semantic-interchange.org](https://open-semantic-interchange.org/)
- **GitHub**: [github.com/open-semantic-interchange](https://github.com/open-semantic-interchange)
- **Slack**: [join slack](https://join.slack.com/t/opensemanticx/shared_invite/zt-3pq1j0lid-tQBbEvAngAvz0I0vZm~HJw)
- **Core Specification**: [core-spec/spec.md](../core-spec/spec.md)
- **JSON Schema**: [core-spec/osi-schema.json](../core-spec/osi-schema.json)
- **YAML Schema**: [core-spec/spec.yaml](../core-spec/spec.yaml)
- **TPC-DS Example Model**: [examples/tpcds_semantic_model.yaml](../examples/tpcds_semantic_model.yaml)
- **Validation Script**: [validation/validate.py](../validation/validate.py)
- **Converters Guide**: [converters/index.md](../converters/index.md)

---

## Contributing

We welcome contributions from everyone — whether you are a developer, a data engineer, a BI analyst, or simply someone interested in the future of semantic interoperability.

### Ways to Contribute

- **Specification Feedback**: Review proposed specification changes and share your perspective on GitHub pull requests and issues.
- **Use Case Discussions**: Share how your organization uses semantic models and what challenges you face — this helps shape the specification to address real-world needs.
- **Code Contributions**: Contribute to validation tooling, converters, examples, or any other part of the project.
- **Documentation**: Help improve and expand the project documentation.
- **Community Support**: Answer questions, participate in discussions, and help onboard new contributors.

### Getting Started

1. **Read the Specification**: Familiarize yourself with the [core specification](../core-spec/spec.md) to understand the semantic model format.
2. **Explore the Examples**: Review the [TPC-DS example](../examples/tpcds_semantic_model.yaml) to see a complete semantic model in practice.
3. **Join the Conversation**: Open or participate in [GitHub Issues](https://github.com/open-semantic-interchange) and Discussions to share ideas and feedback.
4. **Submit a Pull Request**: Fork the repository, make your changes, and submit a pull request. All contributions go through the standard review process described in the governance section above.
5. **Join the Slack workspace**: You can chat directly with the community by [joining Slack](https://join.slack.com/t/opensemanticx/shared_invite/zt-3pq1j0lid-tQBbEvAngAvz0I0vZm~HJw).

### Code of Conduct

All participants in the OSI community are expected to treat each other with respect and professionalism. We are committed to providing a welcoming and inclusive environment for everyone, regardless of background or experience level.

---

## License

- **Code**: All code in this repository is licensed under the [Apache License 2.0](../LICENSE).
- **Specification and Documentation**: Licensed under the [Creative Commons Attribution License (CC BY)](../LICENSE-Docs).
