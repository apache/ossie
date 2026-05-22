"""Tests for Foundation v0.1 strictness checks gated by FoundationFlags.

Three deferral classes are exercised here, mirroring
:mod:`osi.parsing.foundation`:

1. ``allow_aggregate_in_field`` (D-003) — every aggregate function in a
   field expression must surface ``E_AGGREGATE_IN_FIELD`` by default and
   parse cleanly when the flag is on.
2. ``allow_dataset_scoped_metrics`` (§4.5) — a per-dataset ``metrics:``
   block must surface ``E_DEFERRED_KEY_REJECTED`` by default and parse
   cleanly when the flag is on.
3. ``allow_nested_aggregation`` (D-027) — a metric expression that nests
   one aggregate inside another must surface
   ``E_NESTED_AGGREGATION_DEFERRED`` by default and parse cleanly when
   the flag is on.

Each class also asserts the negative case: a model that does *not* use
the deferred construct parses cleanly under the strict defaults so we
know the check isn't accidentally over-rejecting.

The ``FoundationFlags.legacy_permissive()`` constructor is exercised on
its own to lock in the contract that flipping every flag at once
restores pre-deferral behaviour for the same offending models.
"""

from __future__ import annotations

from textwrap import dedent

import pytest

from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIParseError
from osi.parsing import parse_semantic_model

# ---------------------------------------------------------------------------
# Fixtures: minimal models that exercise one deferred construct each
# ---------------------------------------------------------------------------

# A field whose body is an aggregate over its own dataset's columns.
# Pre-deferral this would have been treated as a same-grain aggregate;
# Foundation v0.1 §4.3 / D-003 rejects every aggregate-bodied field.
_AGGREGATE_IN_FIELD_YAML = dedent("""
    semantic_model:
      - name: demo
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [id]
            fields:
              - name: id
                expression: id
              - name: amount
                expression: amount
                role: fact
              - name: total_amount
                expression: SUM(amount)
                role: fact
    """).strip()

# Same model as above but with the aggregate moved out of the field and
# into a top-level metric — the strict-Foundation shape.
_AGGREGATE_IN_TOP_LEVEL_METRIC_YAML = dedent("""
    semantic_model:
      - name: demo
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [id]
            fields:
              - name: id
                expression: id
              - name: amount
                expression: amount
                role: fact
        metrics:
          - name: total_amount
            expression: SUM(orders.amount)
    """).strip()

# A per-dataset ``metrics:`` block. The model is otherwise minimal; the
# rejection must fire purely on the presence of the key.
_DATASET_SCOPED_METRIC_YAML = dedent("""
    semantic_model:
      - name: demo
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [id]
            fields:
              - name: id
                expression: id
              - name: amount
                expression: amount
                role: fact
            metrics:
              - name: total_amount
                expression: SUM(amount)
    """).strip()

# A metric whose body is an aggregate of another aggregate. The two
# aggregates are distributive (SUM of SUM), which the §10 proposal
# would collapse to a single SUM, but the Foundation rejects all
# nested forms uniformly.
_NESTED_AGGREGATION_YAML = dedent("""
    semantic_model:
      - name: demo
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [id]
            fields:
              - name: id
                expression: id
              - name: amount
                expression: amount
                role: fact
        metrics:
          - name: bad_double_sum
            expression: SUM(SUM(orders.amount))
    """).strip()

# A windowed aggregate inside a field expression. Window functions are
# permitted in fields (§4.3.1); the strictness check must therefore
# leave this alone even though it contains an :class:`exp.AggFunc` node.
_WINDOWED_AGGREGATE_FIELD_YAML = dedent("""
    semantic_model:
      - name: demo
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [id]
            fields:
              - name: id
                expression: id
              - name: customer_id
                expression: customer_id
              - name: amount
                expression: amount
                role: fact
              - name: rank_in_customer
                expression: >-
                  ROW_NUMBER() OVER (PARTITION BY customer_id
                                     ORDER BY amount DESC)
    """).strip()


# ---------------------------------------------------------------------------
# D-003 — aggregate-bodied fields
# ---------------------------------------------------------------------------


class TestAggregateInFieldRejection:
    """An aggregate function in a field's ``expression`` is deferred."""

    def test_default_flags_reject_aggregate_field(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(_AGGREGATE_IN_FIELD_YAML)
        assert exc.value.code is ErrorCode.E_AGGREGATE_IN_FIELD
        # Diagnostic context surfaces the offending field so authors
        # can grep to it without re-parsing.
        assert exc.value.context["dataset"] == "orders"
        assert exc.value.context["field"] == "total_amount"
        assert exc.value.context["aggregate"] == "SUM"
        assert exc.value.context["flag"] == "allow_aggregate_in_field"

    def test_explicit_strict_rejects_aggregate_field(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(
                _AGGREGATE_IN_FIELD_YAML, flags=FoundationFlags.strict()
            )
        assert exc.value.code is ErrorCode.E_AGGREGATE_IN_FIELD

    def test_legacy_flag_accepts_aggregate_field(self) -> None:
        result = parse_semantic_model(
            _AGGREGATE_IN_FIELD_YAML,
            flags=FoundationFlags(allow_aggregate_in_field=True),
        )
        # The aggregate field is preserved as a fact-role field.
        names = {str(f.name) for f in result.model.datasets[0].fields}
        assert "total_amount" in names

    def test_top_level_metric_replacement_parses_strict(self) -> None:
        # The migration path documented in the error message — move
        # the aggregate to a top-level metric — must parse under the
        # strict defaults.
        result = parse_semantic_model(_AGGREGATE_IN_TOP_LEVEL_METRIC_YAML)
        assert {str(m.name) for m in result.model.metrics} == {"total_amount"}

    def test_windowed_aggregate_field_parses_strict(self) -> None:
        # Window expressions are not aggregates in the spec sense
        # (§4.3.1). The strict check must leave them alone.
        result = parse_semantic_model(_WINDOWED_AGGREGATE_FIELD_YAML)
        names = {str(f.name) for f in result.model.datasets[0].fields}
        assert "rank_in_customer" in names


# ---------------------------------------------------------------------------
# §4.5 — per-dataset metrics block
# ---------------------------------------------------------------------------


class TestDatasetScopedMetricRejection:
    """A ``metrics:`` block under a dataset is deferred."""

    def test_default_flags_reject_dataset_scoped_metrics(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(_DATASET_SCOPED_METRIC_YAML)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED
        assert exc.value.context["field"] == "metrics"
        assert exc.value.context["first_metric"] == "total_amount"
        assert exc.value.context["flag"] == "allow_dataset_scoped_metrics"

    def test_legacy_flag_accepts_dataset_scoped_metrics(self) -> None:
        result = parse_semantic_model(
            _DATASET_SCOPED_METRIC_YAML,
            flags=FoundationFlags(
                allow_dataset_scoped_metrics=True,
                # The legacy block uses ``SUM(amount)`` (an aggregate
                # in a metric body, which is fine) but the per-dataset
                # block by itself doesn't pull in the field-aggregate
                # path; only the dataset-metric flag is required.
            ),
        )
        assert len(result.model.datasets[0].metrics) == 1


# ---------------------------------------------------------------------------
# D-027 — nested aggregation in metrics
# ---------------------------------------------------------------------------


class TestNestedAggregationRejection:
    """An aggregate of an aggregate in a metric is deferred."""

    def test_default_flags_reject_nested_aggregation(self) -> None:
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(_NESTED_AGGREGATION_YAML)
        assert exc.value.code is ErrorCode.E_NESTED_AGGREGATION_DEFERRED
        assert exc.value.context["metric"] == "bad_double_sum"
        assert exc.value.context["scope"] == "model"
        assert exc.value.context["outer"] == "SUM"
        assert exc.value.context["inner"] == "SUM"
        assert exc.value.context["flag"] == "allow_nested_aggregation"

    def test_legacy_flag_accepts_nested_aggregation(self) -> None:
        result = parse_semantic_model(
            _NESTED_AGGREGATION_YAML,
            flags=FoundationFlags(allow_nested_aggregation=True),
        )
        assert {str(m.name) for m in result.model.metrics} == {"bad_double_sum"}


# ---------------------------------------------------------------------------
# Cross-flag interactions and convenience constructors
# ---------------------------------------------------------------------------


class TestFoundationFlagsConstructors:
    """``strict()`` and ``legacy_permissive()`` are equivalent shortcuts."""

    def test_default_construction_matches_strict(self) -> None:
        assert FoundationFlags() == FoundationFlags.strict()
        assert FoundationFlags().allow_aggregate_in_field is False
        assert FoundationFlags().allow_dataset_scoped_metrics is False
        assert FoundationFlags().allow_nested_aggregation is False

    def test_legacy_permissive_enables_all_three(self) -> None:
        flags = FoundationFlags.legacy_permissive()
        assert flags.allow_aggregate_in_field is True
        assert flags.allow_dataset_scoped_metrics is True
        assert flags.allow_nested_aggregation is True

    def test_legacy_permissive_round_trips_each_offender(self) -> None:
        # One umbrella check: every model that the strict defaults
        # reject above must parse under ``legacy_permissive()``. This
        # locks in the symmetry between the deferral list in
        # ``Proposed_OSI_Semantics.md`` and the flag set here.
        flags = FoundationFlags.legacy_permissive()
        for source in (
            _AGGREGATE_IN_FIELD_YAML,
            _DATASET_SCOPED_METRIC_YAML,
            _NESTED_AGGREGATION_YAML,
        ):
            parse_semantic_model(source, flags=flags)

    def test_dataset_scoped_metric_check_runs_before_aggregate_check(self) -> None:
        # A model that uses both a dataset-scoped metric and an
        # aggregate-bodied field must surface the dataset-scoped
        # rejection first; that ordering is part of the documented
        # contract in :mod:`osi.parsing.foundation` and gives authors
        # the more familiar deferred-key error message before the
        # newer aggregate-in-field one.
        both_yaml = dedent("""
            semantic_model:
              - name: demo
                datasets:
                  - name: orders
                    source: sales.orders
                    primary_key: [id]
                    fields:
                      - name: id
                        expression: id
                      - name: amount
                        expression: amount
                        role: fact
                      - name: total_amount
                        expression: SUM(amount)
                        role: fact
                    metrics:
                      - name: total_amount_metric
                        expression: SUM(amount)
            """).strip()
        with pytest.raises(OSIParseError) as exc:
            parse_semantic_model(both_yaml)
        assert exc.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED
