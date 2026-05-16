# Proposal: Relationship Enhancements — Index

**Status:** Split into two focused proposals  
**Author:** will.pugh@snowflake.com  
**Date:** 2026-02-23

This proposal has been split into two independent specs:

---

## [Part I — Referential Integrity Settings](./OSI_Proposal_Referential_Integrity.md)

Adds an optional `referential_integrity` object to relationships, allowing model authors to declare FK completeness once at the relationship level. Eliminates repetitive `joins: { type: INNER }` annotations on individual metrics.

**Key changes:** `referential_integrity.from_all_rows_match`, `referential_integrity.to_all_rows_match`  
**TPC-DS impact:** Eliminates per-metric INNER JOIN boilerplate on Q46, Q47, Q57, Q68, Q69, Q79, Q89  
**Implementation risk:** Low — additive schema field, join type selection logic change only

---

## [Part II — Non-Equijoin Relationships](./OSI_Proposal_Non_Equijoins.md)

Adds `condition` (a SQL predicate) and `cardinality` (explicit or override) fields to relationships, enabling range joins, band/tier joins, overlap joins, and inequality/exclusion self-joins.

**Key changes:** `condition`, `cardinality`, self-join `from.`/`to.` qualifier syntax, DiGraph → MultiDiGraph graph upgrade  
**TPC-DS impact:** Unblocks Q16, Q94, Q95 (self-join EXISTS) and Q17, Q25, Q29 (aliased dimension joins)  
**Implementation risk:** Medium-to-high — graph layer breaking change, transpiler alias generation, path disambiguation

---

## Why Split?

The two parts are **orthogonal**: RI settings are a low-risk, high-value ergonomic improvement to the existing equijoin system. Non-equijoins are a significant new capability with substantial graph and transpiler changes. Shipping them separately allows Part I to move quickly while Part II gets the design review it needs — particularly around the self-join column disambiguation syntax and the DiGraph → MultiDiGraph migration.
