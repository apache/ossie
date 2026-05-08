# OSI Roadmap (Community-Informed)

This roadmap synthesizes community discussions and voting signals from the [OSI GitHub Discussions](https://github.com/open-semantic-interchange/OSI/discussions) board. It groups work into three categories:

- **Current Efforts / Working Groups** — strategic initiatives with active working groups driving spec evolution now
- **Future Efforts** — strategic initiatives planned for future working groups
- **Enhancements & Additions** — incremental improvements that extend the current model

---

## Current Efforts / Working Groups

These are the strategic initiatives where working groups are actively driving spec evolution.

---

### Metric Semantics & Core Semantic Model

**Goal:** Enable expressive, composable, and well-defined semantic models with clear entity, relationship, and grain semantics.

**Motivation:**
The current model lacks sufficient support for metrics at different grains, filters, aggregation semantics, and relationships between metrics. Ambiguity in how entities, joins, and grain are represented limits interoperability.

**Key Discussions:**

- [Top-level "metrics" vs. dataset-level "measures"](https://github.com/open-semantic-interchange/OSI/discussions/29)
- [Cumulative and other "expansions" to metrics](https://github.com/open-semantic-interchange/OSI/discussions/39)
- [Structured aggregation_method for Metrics](https://github.com/open-semantic-interchange/OSI/discussions/19)
- [Add "entity / grain" as a first-class concept](https://github.com/open-semantic-interchange/OSI/discussions/12)
- [Add explicit datasets reference to Metrics](https://github.com/open-semantic-interchange/OSI/discussions/18)
- [Relationship Semantics](https://github.com/open-semantic-interchange/OSI/discussions/24)
- [Complex Relationship Definitions](https://github.com/open-semantic-interchange/OSI/discussions/4)
- [Make Relationship Cardinality Explicit](https://github.com/open-semantic-interchange/OSI/discussions/50)
- [Inner join in relationships](https://github.com/open-semantic-interchange/OSI/discussions/11)
- [Support for cross-dataset dimensions & single-dataset measures](https://github.com/open-semantic-interchange/OSI/discussions/27)
- [Semantic Filters](https://github.com/open-semantic-interchange/OSI/discussions/5)

**Roadmap Deliverables:**

- Standard metrics specification language
- First-class aggregation, relationship, and grain semantics, including a specification that documents the expected behavior that the community has aligned on
- Support for derived and cumulative metrics
- Explicit entity modeling
- Enhanced relationship definitions & capabilities
- Cross-domain modeling support
- Reusable semantic filter definitions

---

### Catalog Integration & Semantic Services

**Goal:** Integrate OSI with data catalogs and enable centralized semantic services.

**Motivation:**
Semantic models need to be discoverable, governable, and shareable across systems.

**Roadmap Deliverables:**

- Integration patterns with catalogs (e.g., Polaris)
- Standalone semantic service / registry
- Discovery, versioning, and access control for OSI models

**Related Issues & PRs:**

- [PR #94 — Add Apache Polaris converter module](https://github.com/open-semantic-interchange/OSI/pull/94)
- [Issue #107 — Proposal: Adopt ontology-query as an Ontology Access Layer tool](https://github.com/open-semantic-interchange/OSI/issues/107)

---

## Future Efforts

These strategic initiatives are planned for future working groups as the spec matures.

---

### Dataset Abstraction & Logical Modeling

**Goal:** Decouple semantic definitions from physical storage.

**Motivation:**
Users want reusable semantic models independent of underlying tables or views.

**Key Discussions:**

- [Add support for "Logical Datasets" (query-defined entities / view definitions)](https://github.com/open-semantic-interchange/OSI/discussions/49)
- [Support one-to-many binding between a logical dataset and the physical table, view, or query](https://github.com/open-semantic-interchange/OSI/discussions/61)
- [Structured Dataset Sources](https://github.com/open-semantic-interchange/OSI/discussions/23)

**Roadmap Deliverables:**

- Mapping layer between logical and physical datasets
- Reusable semantic definitions across environments

**Related Issues & PRs:**

- [Issue #104 — First-class representation of file-backed datasets (e.g. Parquet)](https://github.com/open-semantic-interchange/OSI/issues/104)

---

### Semantic Query Language & Reference Engine

**Goal:** Define a standard query interface for interacting with OSI models and provide a canonical implementation for interpreting and executing them.

**Motivation:**
Consumers (BI tools, AI systems, APIs) need a consistent way to query semantic models independent of underlying SQL dialects. A reference engine ensures consistent interpretation of the spec and accelerates ecosystem adoption.

**Roadmap Deliverables:**

- Standard semantic query language (OSI-native or SQL-extended)
- Mapping from semantic queries → execution plans
- Support for metrics, dimensions, filters, and relationships
- Reference compiler from OSI → SQL
- Canonical handling of joins, aggregations, and filters
- Test suite to validate conformance across implementations

**Related Issues & PRs:**

- [Issue #107 — Proposal: Adopt ontology-query as an Ontology Access Layer tool](https://github.com/open-semantic-interchange/OSI/issues/107)

---

### SQL Dialect, Expressions, and Execution Boundaries

**Goal:** Clarify the role of SQL and execution within OSI.

**Motivation:**
There is tension between portability and practical execution requirements.

**Key Discussions:**

- [Add Default Dialect at Dataset Level](https://github.com/open-semantic-interchange/OSI/discussions/16)
- [Expectations around SQL expression dialects and conversion](https://github.com/open-semantic-interchange/OSI/discussions/28)
- [Use templating engine instead of plain yaml](https://github.com/open-semantic-interchange/OSI/discussions/62)
- [Jinja Templates](https://github.com/open-semantic-interchange/OSI/discussions/6)

**Roadmap Deliverables:**

- Explicit dialect handling strategy
- Clear boundaries between semantic definition and execution
- Optional templating support

**Related Issues & PRs:**

- [Issue #52 — Only allow one dialect per OSI document](https://github.com/open-semantic-interchange/OSI/issues/52)
- [PR #60 — POC for a switch to having a single dialect for the whole file](https://github.com/open-semantic-interchange/OSI/pull/60)

---

### Dimensions, Hierarchies, and Time Semantics

**Goal:** Standardize how dimensions and time are modeled.

**Motivation:**
Inconsistent handling of hierarchies and time impacts usability and interoperability.

**Key Discussions:**

- [Dimension Hierarchies](https://github.com/open-semantic-interchange/OSI/discussions/21)
- [Dimension Groups](https://github.com/open-semantic-interchange/OSI/discussions/20)
- [Replace is_time with dimension_type Enum](https://github.com/open-semantic-interchange/OSI/discussions/17)
- [Universal calendar support](https://github.com/open-semantic-interchange/OSI/discussions/44)
- [Date Spine models](https://github.com/open-semantic-interchange/OSI/discussions/47)

**Roadmap Deliverables:**

- Hierarchical dimension modeling
- Standardized time semantics
- Calendar abstractions

**Related Issues & PRs:**

- [Issue #84 — Support field datatype rather than is_time](https://github.com/open-semantic-interchange/OSI/issues/84)

---

### AI-Native Semantic Layer

**Goal:** Enable OSI as a reliable foundation for AI-driven analytics.

**Motivation:**
There is growing demand for structured semantic context and grounded query generation.

**Key Discussions:**

- [Do not prescribe "AI Context" as a key name](https://github.com/open-semantic-interchange/OSI/discussions/32)
- [Keyword for skipping context for AI](https://github.com/open-semantic-interchange/OSI/discussions/14)
- [Usage guidelines with samples especially for ai_context field](https://github.com/open-semantic-interchange/OSI/discussions/9)
- [Add verified_queries as a core element of the spec](https://github.com/open-semantic-interchange/OSI/discussions/82)

**Roadmap Deliverables:**

- Standardized AI context metadata
- Verified or curated query definitions
- Mechanisms for controlling AI exposure to semantic elements

**Related Issues & PRs:**

- [PR #81 — Add verified queries (draft)](https://github.com/open-semantic-interchange/OSI/pull/81)

---

### Governance, Identity, and Validation

**Goal:** Ensure trust, stability, and long-term interoperability.

**Motivation:**
Enterprise adoption requires consistent identifiers, validation, and governance hooks.

**Key Discussions:**

- [Make stable identifiers explicit rather than reusing name](https://github.com/open-semantic-interchange/OSI/discussions/31)
- [Metrics schema - Certified and Certifying Authority](https://github.com/open-semantic-interchange/OSI/discussions/53)
- [Governance metadata hooks](https://github.com/open-semantic-interchange/OSI/discussions/13)
- [Add more rigor to the spec using LinkML](https://github.com/open-semantic-interchange/OSI/discussions/67)
- [OSI-level validations?](https://github.com/open-semantic-interchange/OSI/discussions/35)

**Roadmap Deliverables:**

- Stable identifiers across environments
- Validation and conformance standards
- Governance and certification frameworks

**Related Issues & PRs:**

- [Issue #102 — Add semantic versioning and Git releases for core-spec/osi-schema.json](https://github.com/open-semantic-interchange/OSI/issues/102)
- [Issue #92 — Community Implementation: Trust Control Center — OSI-compatible governance & reconciliation platform](https://github.com/open-semantic-interchange/OSI/issues/92)
- [Issue #87 — New Flags: Restricted and Internal only indicators](https://github.com/open-semantic-interchange/OSI/issues/87)

---

### Industry / Domain-Specific Semantic Models

**Goal:** Accelerate adoption through reusable, standardized domain models.

**Motivation:**
Organizations repeatedly recreate similar semantic models (e.g., SaaS, finance, retail). Standardized models can drive faster adoption and consistency.

**Roadmap Deliverables:**

- Curated domain-specific semantic model templates
- Best-practice metric and dimension definitions by industry
- Interoperable model packages aligned with OSI

---

## Enhancements & Additions (Incremental Improvements)

These items improve usability, clarity, and completeness without fundamentally changing the spec.

---

### Naming, Terminology, and UX Improvements

**Goal:** Align OSI vocabulary with how practitioners think about semantic models, and improve the authoring experience.

**Motivation:**
Several naming conventions in the current spec create confusion or clash with established industry terminology. Clearer naming reduces onboarding friction and improves readability of OSI definitions.

**Roadmap Deliverables:**

- Revised terminology that reflects community consensus (e.g., "Dimension" over "Field")
- Consistent naming conventions for source references, descriptions, and display labels

**Key Discussions:**

- [Rename Field to Dimension](https://github.com/open-semantic-interchange/OSI/discussions/33)
- [Rename Dataset.source to avoid conflation with where an entity was first defined](https://github.com/open-semantic-interchange/OSI/discussions/34)
- [Generalise description field](https://github.com/open-semantic-interchange/OSI/discussions/36)
- [Introduce a concept for "Display name"](https://github.com/open-semantic-interchange/OSI/discussions/37)

**Related Issues & PRs:**

- [PR #91 — Change vendor_name from enum to free-form string](https://github.com/open-semantic-interchange/OSI/pull/91)

---

### Data Types and Field Semantics

**Goal:** Provide native support for rich data typing so downstream tools can interpret fields without guesswork.

**Motivation:**
Consuming systems (BI tools, AI agents, dashboards) frequently need to know whether a field represents a currency, a physical unit, or sensitive data — but this context is lost in the current spec and must be re-inferred or hard-coded per tool.

**Roadmap Deliverables:**

- First-class unit and currency annotations on measures and dimensions
- Standardized semantic field type taxonomy (dimension type, data type, PII classification)

**Key Discussions:**

- [Native support for units](https://github.com/open-semantic-interchange/OSI/discussions/42)
- [Native support for currencies](https://github.com/open-semantic-interchange/OSI/discussions/43)
- [Semantic Field Types: dimension_type, data_type, and pii_classification](https://github.com/open-semantic-interchange/OSI/discussions/55)

**Related Issues & PRs:**

- [Issue #58 — New attribute: contain personal data](https://github.com/open-semantic-interchange/OSI/issues/58)
- [Issue #59 — New attribute: confidential indicator](https://github.com/open-semantic-interchange/OSI/issues/59)
- [PR #66 — Add personal data and confidentiality indicators](https://github.com/open-semantic-interchange/OSI/pull/66)

---

### Extended Metadata for OSI

**Goal:** Introduce a lightweight, optional metadata layer that improves how data is interpreted, presented, and consumed — without affecting execution semantics.

**Motivation:**
OSI standardizes structural and logical semantics well, but there is limited support for conveying interpretability context such as display conventions, default aggregation behavior, KPI polarity, sorting preferences, and alignment to external semantic concepts. These details are often redefined or inferred inconsistently across developers, BI tools, and AI systems.

**Roadmap Deliverables:**

- [Extended Metadata Proposal for OSI](https://github.com/open-semantic-interchange/OSI/issues/100) — optional, backward-compatible metadata fields (e.g., `measurement`, `display_format`, `semantic_type`, `default_aggregation`, `desired_direction`, `default_sort`, `semantic_mappings`)
- Richer application-specific extension points beyond `custom_extensions`
- Sample value annotations for documentation and AI grounding

**Key Discussions:**

- [Expand custom_extensions to be more suitable for application-specific metadata](https://github.com/open-semantic-interchange/OSI/discussions/30)
- [Sample values](https://github.com/open-semantic-interchange/OSI/discussions/7)
- [Governance metadata hooks](https://github.com/open-semantic-interchange/OSI/discussions/13) *(also informs strategic governance work)*

---

### Developer Experience & Documentation

**Goal:** Lower the barrier to adopting and correctly using OSI through better guidance, examples, and tooling-friendly formatting.

**Motivation:**
New adopters and tool authors need clearer documentation, real-world samples, and support for rich-text descriptions to effectively author and consume OSI models.

**Roadmap Deliverables:**

- Comprehensive usage guides with annotated examples, especially for AI context fields
- Data modeling best-practice documentation
- Markdown support in description fields for richer inline documentation

**Key Discussions:**

- [Usage guidelines with samples especially for ai_context field](https://github.com/open-semantic-interchange/OSI/discussions/9)
- [Information about data modelling](https://github.com/open-semantic-interchange/OSI/discussions/8)
- [Markdown support](https://github.com/open-semantic-interchange/OSI/discussions/38)

**Existing Artifacts:**

- [Core Specification (spec.md)](core-spec/spec.md) — the current OSI spec document
- [TPC-DS Example Model](examples/tpcds_semantic_model.yaml) — reference semantic model using the TPC-DS benchmark
- [Converter Guide (converters/index.md)](converters/index.md) — hub-and-spoke converter architecture and authoring guide

---

### Specialized Capabilities

**Goal:** Extend OSI to support domain-specific data types, audience definitions, and patterns that go beyond traditional tabular analytics.

**Motivation:**
Geospatial analytics, time-series modeling, and audience segmentation have unique requirements that benefit from first-class spec support rather than ad-hoc workarounds.

**Roadmap Deliverables:**

- Spatial field types, spatial relationships, and geographic hierarchies
- Date spine model support for time-series alignment and gap-filling
- Audience / segment definitions as first-class constructs

**Key Discussions:**

- [Geospatial data support: spatial field types, spatial relationships, and geographic hierarchies](https://github.com/open-semantic-interchange/OSI/discussions/69)
- [Date Spine models](https://github.com/open-semantic-interchange/OSI/discussions/47)
- [Add Support for Audiences](https://github.com/open-semantic-interchange/OSI/discussions/51)

---

### Tooling & Ecosystem Support

**Goal:** Provide reference tooling that makes it easy to validate, convert, and adopt OSI models.

**Motivation:**
Broad ecosystem adoption depends on practical tools that let teams validate their models against the spec and convert between OSI and existing vendor formats without manual effort.

**Roadmap Deliverables:**

- Validator code (schema validation, linting, conformance checks)
- Participant ↔ OSI converter code (read/write interoperability with existing tools)

**Existing Artifacts:**

- [JSON Schema (osi-schema.json)](core-spec/osi-schema.json) — schema for structural validation
- [Validation Script (validate.py)](validation/validate.py) — validates OSI YAML against JSON Schema, unique names, references, and SQL syntax
- [Snowflake Converter](converters/snowflake/) — OSI → Snowflake Cortex Analyst YAML converter
- [Databricks Converter](converters/databricks/) — bidirectional OSI ↔ Databricks Unity Catalog Metric View YAML converter

**Related Issues & PRs:**

- [PR #97 — Add validation rules for names, relationship columns, extensions, and dialects](https://github.com/open-semantic-interchange/OSI/pull/97)
- [PR #96 — Add GoodData support: MAQL dialect, vendor registration, and bidirectional LDM converter](https://github.com/open-semantic-interchange/OSI/pull/96)
- [PR #94 — Add Apache Polaris converter module](https://github.com/open-semantic-interchange/OSI/pull/94)
- [PR #93 — Add Apache Spark converter module](https://github.com/open-semantic-interchange/OSI/pull/93)
- [Issue #111 — Follow up on OSI ai_context and custom_extensions mapping in Snowflake YAML](https://github.com/open-semantic-interchange/OSI/issues/111)

