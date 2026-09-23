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

# Layered Query Interface for Ossie Models

**Status:** Draft for the Ossie Semantics Working Group.
**Contributors:** Justin Talbot, Will Pugh, Chris Eubank.

---

## Overview

Ossie consumers (humans, BI tools, and agents) need a query *interface*: a way to evaluate queries 
against an Ossie model with correctness guarantees.

A single interface cannot serve all integration styles equally well. An AI agent may want the
flexibility to generate low-level SQL queries directly from an Ossie model. A SQL-native BI tool
may want full control over query semantics while querying a shared model in a database. A data
analyst may prefer a simple declarative interface with no SQL at all. A higher-order ontology or
knowledge-graph layer may require concepts that SQL does not naturally express.

Ossie therefore adopts a **layered query interface**—four layers that each address a distinct
correctness or expressiveness concern. Consumers choose the layer that best matches their
integration use case. Higher layers inherit correctness from lower ones, and each layer can be
adopted independently.

This document describes the four layers at a high level. Each layer is specified in full detail in
its own document (linked below).

---

## The Four Layers

### Layer 1 — Common Expression Language

Layer 1 defines the shared expression language used inside Ossie model definitions. Measure
definitions, calculated fields, and filter predicates in an Ossie model file are written in this
portable SQL expression subset. Layer 1 establishes the guarantee: **expressions in an Ossie model
evaluate identically on any conforming engine.** A model authored once can be deployed anywhere
without rewriting its expressions.

**Specification:** [`expression_language.md`](expression_language.md)

### Layer 2 — Relational Interface (SQL Measures)

Layer 2 is the relational query interface for Ossie models. Its query language is the Layer 1
expression language extended with the relational constructs of SQL: joins, group-by, filters,
CTEs, ordering, etc.—everything outside of scalar and aggregate expressions. To
this it adds one extension, `MEASURE()`, which evaluates a named measure at its declared grain.

Layer 2 establishes the guarantee: **measures are evaluated without duplication regardless of
how they are queried.** Fan-out and chasm-trap errors cannot occur when measures are queried
through this interface. The model's datasets are exposed as SQL relations; consumers query them
with ordinary SQL, using `MEASURE()` in place of raw aggregates.

This layer preserves full SQL invariants. Tools that generate SQL, embed SQL in notebooks, or
build on the relational algebra can use Layer 2 without giving up any SQL guarantees.

**Specification:** [`relational_semantics.md`](relational_semantics.md) *(open PR: [apache/ossie#354](https://github.com/apache/ossie/pull/354))*

### Layer 3 — Wide Table Interface (Dimensional)

Layer 3 exposes a multi-table Ossie model as a single flat object. The consumer specifies
dimensions, measures, and filters declaratively; the layer resolves which source tables to join,
in what order, and with what join type. No SQL generation is required from the consumer.

This interface trades explicit query control for simplicity, making it accessible to ad-hoc users,
AI agents, and BI tools without their own data model. Measure correctness is inherited from
Layer 2: a wide-table query is rewritten into a SQL measures query (with heuristic join and filter
choices), so the same grain-safe evaluation guarantee holds.

**Specification:** [apache/ossie#246](https://github.com/apache/ossie/pull/246) *(forthcoming)*

### Layer 4 — Ontology

Layer 4 sits above the other layers and covers higher-order semantic concepts—entities,
relationships, and knowledge-graph constructs that SQL does not naturally represent. It is defined
by the Ossie Ontology working group (`#ossie-ontology-wg`).

**Specification:** [`../ontology/ontology.md`](../ontology/ontology.md)

---

## Composition

The four layers compose. Layer 3 can be implemented on top of Layer 2: a wide-table query is
rewritten into a SQL measures query. Layer 2 queries can be rewritten into Layer 1 queries by
correcting for duplication. Layer 1 queries are highly portable across engines. A deployment that
supports Layers 1–3 can expose both a SQL interface for SQL-native tools and a declarative
wide-table interface for tools or users who prefer it—with the same expression semantics and
correctness guarantees underlying both.

Providers may support one layer or several, and at varying compliance levels. The layered model
makes partial adoption coherent: a tool that only needs SQL measures picks up Layer 2 without
taking a dependency on the wide-table semantics of Layer 3.

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
