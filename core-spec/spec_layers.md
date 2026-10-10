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

# Ossie Specification Layers

**Status:** Informational / Architecture Overview (Draft).
**Contributors:** Justin Talbot, Will Pugh, Chris Eubank.

---

## Overview

This document is an informational architecture overview. It describes how the Ossie
specifications fit together and links to the specification for each layer. It does not
define normative requirements itself; those are in each layer's specification.

Ossie is organized into four specification layers, each addressing a distinct concern:

| Layer | Name | Role |
|-------|------|------|
| 1 | Model Definition | Model definition: the Ossie model format and the expression language used within it |
| 2 | Relational Query Interface | Query interface: SQL-native, grain-safe measure evaluation, full SQL invariants |
| 3 | Wide Table Query Interface | Query interface: declarative flat-object queries, heuristic join resolution |
| 4 | Ontology | Ontology workstream: higher-order semantic and knowledge-graph concepts |

Layer 1 defines what an Ossie model *is*—the model format and the expression language authors
use to write it. Layers 2 and 3 are query interfaces that sit on top of a deployed model;
they differ in how much SQL knowledge they require from the consumer. Layer 4 is a separate
workstream covering ontological concepts that SQL does not naturally express.

Each layer can be adopted independently. Layers 2 and 3 compose: the meaning of a wide-table
query (Layer 3) is defined by an equivalent SQL measures query (Layer 2), so correctness
guarantees flow through.

---

## The Layers

### Layer 1 — Model Definition

Layer 1 defines what an Ossie model *is*. It covers two related specifications:

**Model format** ([`spec.md`](spec.md)): the structure of an Ossie model file—datasets,
relationships, fields, and metrics. This is the interchange format: the common schema that
allows semantic model definitions to be moved between tools.

**Expression language** ([`expression_language.md`](expression_language.md)): the portable SQL
expression subset used to write measure definitions, calculated fields, and filter predicates
within a model file. Layer 1 establishes the guarantee: **expressions in an Ossie model
evaluate identically on any conforming engine.** A model authored once can be deployed anywhere
without rewriting its expressions.

Together these define the authoring layer of Ossie. Layer 1 is a model *definition* concern,
not a query concern. It does not prescribe the query language a consumer uses against a deployed
model—that is a per-layer decision for Layers 2 and 3.

### Layer 2 — Relational Query Interface

Layer 2 is the SQL-native query interface for Ossie models. Its query language is the Layer 1
expression language extended with the relational constructs of SQL: joins, group-by, filters,
CTEs, ordering, etc. To this it adds one extension, `MEASURE()`, which evaluates a named
measure at its declared grain.

Layer 2 establishes the guarantee: **measures are evaluated without duplication regardless of
how they are queried.** Fan-out and chasm-trap errors cannot occur when measures are queried
through this interface. The model's datasets are exposed as SQL relations; consumers query them
with ordinary SQL, using `MEASURE()` in place of raw aggregates.

This layer preserves full SQL invariants. Tools that generate SQL, embed SQL in notebooks, or
build on the relational algebra can use Layer 2 without giving up any SQL guarantees.

**Specification:** *(open PR: [apache/ossie#354](https://github.com/apache/ossie/pull/354))*

### Layer 3 — Wide Table Query Interface

Layer 3 is the declarative query interface for Ossie models. It exposes a multi-table model as
a single flat object. The consumer specifies dimensions, measures, and filters; the layer
resolves which source tables to join, in what order, and with what join type. No SQL generation
is required from the consumer.

This interface trades explicit query control for simplicity, making it accessible to ad-hoc
users, AI agents, and BI tools without their own data model. Measure correctness is inherited
from Layer 2: a wide-table query is defined as equivalent to a SQL measures query (with
heuristic join and filter choices), so the same grain-safe evaluation guarantee holds.

**Specification:** *(open PR: [apache/ossie#246](https://github.com/apache/ossie/pull/246))*

### Layer 4 — Ontology

Layer 4 covers higher-order semantic concepts—entities, relationships, and knowledge-graph
constructs that SQL does not naturally represent. It is defined by the Ossie Ontology working
group (`#ossie-ontology-wg`) and is a separate workstream from the query interfaces in Layers
2 and 3.

**Specification:** [`../ontology/ontology.md`](../ontology/ontology.md)

---

## Composition

Layers 2 and 3 compose: the meaning of a wide-table (Layer 3) query is defined by an
equivalent SQL measures (Layer 2) query, with heuristic join and filter choices. This means
correctness guarantees flow from Layer 2 through Layer 3—a consumer using the declarative
interface gets the same grain-safe measure evaluation as one writing SQL directly.

Layer 2 is the semantic target for Layer 3, not a required execution path. An implementation
does not have to translate Layer 3 queries into Layer 2 SQL text; it only has to return the
same results as the equivalent Layer 2 query.

Providers may support one query interface layer or both, at varying compliance levels. The
layered model makes partial adoption coherent: a tool that only needs SQL measures adopts
Layer 2 without taking on the wide-table join semantics of Layer 3.

---

## Background and Prior Work

The layered approach was proposed by Justin Talbot in
[*OSI Layered Query Interface*](https://docs.google.com/document/d/1FOtRNBu6UqA2yeOwUa4jt1r2v45BWQJqS5_BTk34a1c/edit)
(May 2026) and refined by Will Pugh in
[*Proposed Semantics Priorities*](https://docs.google.com/document/d/1EgG5vOlJSubm01S1AGMJ4MKcMcjxBhJHenGKWMr849E/edit)
(August 2026). Will's document introduced the four-layer framing (adding the Ontology layer),
clarified the relationship between the wide-table and SQL-compliant interfaces, and proposed a
prioritization for defining semantics across layers.

The grain-safe measure model underlying Layer 2 is grounded in Hyde and Fremlin,
[*Measures in SQL*](https://doi.org/10.48550/arXiv.2406.00251) (arXiv:2406.00251).

### Mailing List Discussion

The following `dev@ossie.apache.org` threads trace how the layered model emerged and converged:

- **[Ossie Foundational Semantics](https://lists.apache.org/thread/qo2fx0hl0cvlzn9q1ob9z7v7g81syj88)**
  (Will Pugh, July 2026). Will proposed a foundational semantics spec (PR #246) covering the
  wide-table query interface and asked the community for feedback and a path to a reference
  implementation.

- **[[DISCUSS] PRs #246 (Foundational Semantics) and #237 (Compliance Suite)](https://lists.apache.org/thread/mqlbbb4ndhb0qo9sf2yn60fb9tlzv59t)**
  (Justin Talbot and Chris Eubank, August 2026). Raised concerns that PR #246 bundled normative
  correctness guarantees (grain-safe aggregation) with opinionated choices (join paths, filter
  propagation, join types) in a way that made adoption all-or-nothing for BI vendors. Argued for
  separating the two into distinct layers.

- **[[DISCUSS] SQL with measures as the Ossie BI / semantic layer interface](https://lists.apache.org/thread/0td2v8n2llqxyqw0r6w51fkt8klp8x5w)**
  (Chris Eubank, August 2026) and
  **[[DISCUSS] Ossie BI / semantic layer interface based on SQL with measures](https://lists.apache.org/thread/q95or2395khvs21nzmwkmwy1vpdgjy87)**
  (Justin Talbot, August 2026). Proposed SQL with measures (the *Measures in SQL* model) as a
  lower, SQL-native layer beneath the wide-table interface, giving SQL-fluent BI tools a path to
  adopt Ossie without taking on multi-table join semantics.

- **[[DISCUSS] Relational Query Interface spec (core-spec)](https://lists.apache.org/thread/b3nty67729wm5vc3ylnp8phgtxl90n4t)**
  (Justin Talbot, September 2026). Announced PR #354, the Layer 2 specification.

- **[[DISCUSS] Landing Ossie semantics and compliance suites](https://lists.apache.org/thread/1jhvnw8vg5fgtct347t62ycc2zkrox6l)**
  (Chris Eubank, September 2026). Summarized the convergence: working-group discussion settled on
  the hybrid layered model, with Layer 3 (PR #246) and Layer 2 (PR #354) as separate specs both
  building on Layer 1, and Layer 3's output expressible in terms of Layer 2 primitives.
