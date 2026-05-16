"""Unit tests for :mod:`osi.parsing.validation`.

Exercise every ``E2xxx`` code raised by cross-reference validation.
"""

from __future__ import annotations

import pytest

from osi.errors import ErrorCode, OSIParseError
from osi.parsing.models import Dataset, Field, Metric, Relationship, SemanticModel
from osi.parsing.validation import validate_model


def _orders() -> Dataset:
    return Dataset(
        name="orders",
        source="sales.orders",
        primary_key=["id"],
        fields=[
            Field(name="id", expression="id"),
            Field(name="customer_id", expression="customer_id"),
            Field(name="amount", expression="amount", role="fact"),
        ],
    )


def _customers() -> Dataset:
    return Dataset(
        name="customers",
        source="sales.customers",
        primary_key=["id"],
        fields=[Field(name="id", expression="id")],
    )


class TestRelationshipReferences:
    def test_happy_path(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders(), _customers()],
            relationships=[
                Relationship.model_validate(
                    {
                        "name": "o_c",
                        "from": "orders",
                        "to": "customers",
                        "from_columns": ["customer_id"],
                        "to_columns": ["id"],
                    }
                )
            ],
        )
        validate_model(model)

    def test_unknown_dataset_E2006(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            relationships=[
                Relationship.model_validate(
                    {
                        "name": "o_c",
                        "from": "orders",
                        "to": "customers",  # missing
                        "from_columns": ["customer_id"],
                        "to_columns": ["id"],
                    }
                )
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E2006_INVALID_RELATIONSHIP

    def test_unknown_column_E2006(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders(), _customers()],
            relationships=[
                Relationship.model_validate(
                    {
                        "name": "o_c",
                        "from": "orders",
                        "to": "customers",
                        "from_columns": ["no_such_col"],
                        "to_columns": ["id"],
                    }
                )
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E2006_INVALID_RELATIONSHIP


class TestMetricReferences:
    def test_metric_qualifies_unknown_dataset_E2002(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[Metric(name="rev", expression="SUM(bogus.amount)")],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_metric_qualifies_known_dataset_ok(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[Metric(name="rev", expression="SUM(orders.amount)")],
        )
        validate_model(model)

    def test_bare_metric_expression_ok(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[Metric(name="rev", expression="SUM(amount)")],
        )
        validate_model(model)


class TestReservedNames:
    """D-019: ``GRAIN``, ``FILTER``, ``QUERY_FILTER`` are reserved.

    Each name class — dataset, field, model-scope metric, dataset-
    scope metric, relationship — must reject a reserved name at
    parse time with ``E_RESERVED_NAME``. Casing is ignored.
    """

    def test_dataset_name_reserved_E_RESERVED_NAME(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                Dataset(
                    name="filter",
                    source="x",
                    primary_key=["id"],
                    fields=[Field(name="id", expression="id")],
                ),
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E_RESERVED_NAME
        assert "filter" in str(exc.value)

    def test_field_name_reserved_E_RESERVED_NAME(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                Dataset(
                    name="orders",
                    source="x",
                    primary_key=["id"],
                    fields=[
                        Field(name="id", expression="id"),
                        Field(name="grain", expression="'placeholder'"),
                    ],
                ),
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E_RESERVED_NAME
        ctx = exc.value.context or {}
        assert ctx.get("kind") == "field"
        assert ctx.get("owner") == "orders"

    def test_model_metric_name_reserved_E_RESERVED_NAME(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[Metric(name="QUERY_FILTER", expression="SUM(orders.amount)")],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E_RESERVED_NAME
        ctx = exc.value.context or {}
        assert ctx.get("kind") == "metric"
        assert ctx.get("owner") is None

    def test_dataset_metric_name_reserved_E_RESERVED_NAME(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                Dataset(
                    name="orders",
                    source="x",
                    primary_key=["id"],
                    fields=[Field(name="amount", expression="amount", role="fact")],
                    metrics=[
                        Metric(name="grain", expression="SUM(amount)"),
                    ],
                ),
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E_RESERVED_NAME
        ctx = exc.value.context or {}
        assert ctx.get("owner") == "orders"

    def test_relationship_name_reserved_E_RESERVED_NAME(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders(), _customers()],
            relationships=[
                Relationship.model_validate(
                    {
                        "name": "Filter",  # collides case-insensitively
                        "from": "orders",
                        "to": "customers",
                        "from_columns": ["customer_id"],
                        "to_columns": ["id"],
                    }
                ),
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E_RESERVED_NAME

    def test_non_reserved_names_pass_through(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                Dataset(
                    name="filter_pop",  # contains the substring but is not equal
                    source="x",
                    primary_key=["id"],
                    fields=[
                        Field(name="id", expression="id"),
                        Field(name="grain_size", expression="'p'"),
                    ],
                ),
            ],
        )
        validate_model(model)


class TestMetricCycles:
    def test_self_reference_is_a_cycle(self) -> None:
        """A metric that references itself is a 1-cycle.

        Previously the validator silently dropped self-edges, which let
        ``m1 := m1 + 1`` slip through and explode in the planner. The
        Foundation rule is uniform: every back-edge — including self —
        is :attr:`ErrorCode.E2005_CIRCULAR_METRIC`.
        """
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[Metric(name="m1", expression="m1 + 1")],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E2005_CIRCULAR_METRIC
        assert "m1" in str(exc.value)

    def test_two_step_cycle_E2005(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[
                Metric(name="m_a", expression="m_b + 1"),
                Metric(name="m_b", expression="m_a + 1"),
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E2005_CIRCULAR_METRIC

    def test_three_step_cycle_E2005(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[
                Metric(name="m_a", expression="m_b + 1"),
                Metric(name="m_b", expression="m_c + 1"),
                Metric(name="m_c", expression="m_a + 1"),
            ],
        )
        with pytest.raises(OSIParseError) as exc:
            validate_model(model)
        assert exc.value.code is ErrorCode.E2005_CIRCULAR_METRIC

    def test_linear_chain_ok(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[
                Metric(name="m_a", expression="m_b + 1"),
                Metric(name="m_b", expression="1"),
            ],
        )
        validate_model(model)
