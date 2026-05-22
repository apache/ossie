"""Off-by-default feature flags for deferred Foundation v0.1 constructs.

`Proposed_OSI_Semantics.md` §10 enumerates a handful of constructs that
the Foundation explicitly defers to the §10 grain-aware-functions
proposal. Their deferred status was sharpened in the latest revision
pass:

* **D-003** — aggregate-bodied fields (same-grain or cross-grain) are
  rejected with ``E_AGGREGATE_IN_FIELD``; all aggregates live in
  model-scoped metrics (§4.5).
* **D-027** — nested aggregation in metric expressions
  (``AVG(COUNT(orders.oid))`` and similar) is rejected with
  ``E_NESTED_AGGREGATION_DEFERRED``; the rules for choosing the inner
  grain wait for §10.
* **§4.5** — per-dataset ``metrics:`` blocks (``customers.metrics:``)
  carry the same implicit "this metric's home dataset is fixed" pin
  as aggregate-bodied fields and are therefore deferred too. Existing
  models port mechanically: move the entry to the top-level
  ``metrics:`` section and qualify the body with the dataset name —
  ``orders.total_revenue = SUM(amount)`` becomes top-level
  ``total_revenue = SUM(orders.amount)``.

The Foundation contract per `Proposed_OSI_Semantics.md` §11 / D-009 is
"engines MAY accept these keys behind a clearly-named, off-by-default
extension flag — a model that uses such a flag is non-portable until
the corresponding deferred proposal lands". This module is that
extension surface for this reference implementation.

Every flag in :class:`FoundationFlags` defaults to ``False``. Calling
``parse_semantic_model(source)`` with no ``flags`` argument therefore
runs the strict Foundation parser; opting back into the legacy
behaviour requires an explicit ``parse_semantic_model(source,
flags=FoundationFlags(allow_aggregate_in_field=True, ...))``.

The flags surface deliberately sits outside :mod:`osi.parsing` so that
later layers (planner, codegen, adapter) can read it without dragging
the entire parsing module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FoundationFlags:
    """Toggles for features deferred from Foundation v0.1.

    Every flag defaults to ``False``. The ``False`` setting matches
    the strict Foundation as defined in
    ``Proposed_OSI_Semantics.md``; the ``True`` setting opts a model
    or session into the legacy / experimental behaviour for that
    feature, at the cost of portability across compliant engines.

    Flags
    -----
    allow_aggregate_in_field
        D-003. When ``True`` the parser accepts aggregate functions
        inside a field's ``expression`` (the legacy implicit
        home-grain rewrite in :mod:`osi.planning.home_grain` then
        runs). When ``False`` (default) the parser raises
        :class:`~osi.errors.ErrorCode.E_AGGREGATE_IN_FIELD` and the
        author must move the aggregate to a top-level metric.

    allow_dataset_scoped_metrics
        Foundation v0.1 §4.5 deferral. When ``True`` the parser
        accepts a per-dataset ``metrics:`` block under a dataset
        (``customers.metrics: [...]``) and the legacy planner /
        namespace paths consume them. When ``False`` (default) the
        parser raises
        :class:`~osi.errors.ErrorCode.E_DEFERRED_KEY_REJECTED` if any
        dataset declares a ``metrics:`` block.

    allow_nested_aggregation
        D-027. When ``True`` the parser accepts nested aggregation in
        metric expressions (``AVG(COUNT(orders.oid))``, …) and the
        :mod:`osi.planning.planner_nested` two-step planner runs.
        When ``False`` (default) the parser raises
        :class:`~osi.errors.ErrorCode.E_NESTED_AGGREGATION_DEFERRED`
        on the offending metric.

    experimental_exists_in
        D-017 / Foundation §10. **Experimental.** When ``True`` the
        planner accepts ``EXISTS_IN(...)`` / ``NOT EXISTS_IN(...)``
        semi-join predicates inside a query's ``where`` clause and
        compiles them through ``filtering_join`` in the algebra.
        When ``False`` (default) the planner rejects them at query
        classification time with
        :class:`~osi.errors.ErrorCode.E_DEFERRED_KEY_REJECTED` —
        matching the published Foundation v0.1, which lists
        semi-joins as deferred. This is the only flag in this class
        that gates a *query*-side (not model-side) construct, and
        the only flag for a feature that does not yet have a
        published OSI proposal; it lives behind a flag so the
        reference implementation can keep its working semi-join
        codepath alive without claiming Foundation conformance for
        models / queries that use it.
    """

    allow_aggregate_in_field: bool = False
    allow_dataset_scoped_metrics: bool = False
    allow_nested_aggregation: bool = False
    experimental_exists_in: bool = False

    @classmethod
    def strict(cls) -> "FoundationFlags":
        """Return the strict Foundation defaults (every flag off).

        The same value as ``FoundationFlags()``; provided as a named
        constructor so call sites can read like prose:
        ``parse_semantic_model(src, flags=FoundationFlags.strict())``.
        """
        return cls()

    @classmethod
    def legacy_permissive(cls) -> "FoundationFlags":
        """Return the legacy-permissive set (every flag on).

        Convenience for callers — most notably internal test fixtures
        — that were written against the pre-deferral model and need
        every legacy construct enabled at once. Production callers
        SHOULD opt into specific flags rather than flip them all at
        once; this constructor exists so the *intent* of "legacy
        behaviour" is searchable.
        """
        return cls(
            allow_aggregate_in_field=True,
            allow_dataset_scoped_metrics=True,
            allow_nested_aggregation=True,
            experimental_exists_in=True,
        )


__all__ = ["FoundationFlags"]
