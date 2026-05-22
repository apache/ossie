"""Unit tests for :mod:`osi.parsing.models`.

These cover validators that raise :class:`OSIParseError` directly. Pure
pydantic errors (``extra_forbidden``, ``missing``, ``enum``) are covered
in :mod:`test_parser` where they flow through the error translator.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIError, OSIParseError
from osi.parsing.models import (
    Dataset,
    Dialect,
    Field,
    FieldRole,
    Metric,
    NamedFilter,
    Parameter,
    Relationship,
    SemanticModel,
)


def _minimal_dataset(name: str = "orders") -> Dataset:
    return Dataset(
        name=name,
        source="schema.table",
        primary_key=["id"],
        fields=[
            Field(name="id", expression="id", role=FieldRole.DIMENSION),
            Field(name="amount", expression="amount", role=FieldRole.FACT),
        ],
    )


class TestFieldValidation:
    def test_happy_path(self) -> None:
        f = Field(name="amount", expression="amount", role="fact")
        assert f.name == normalize_identifier("amount")
        assert f.role is FieldRole.FACT
        assert f.expression.canonical

    def test_invalid_identifier_rejected_E1005(self) -> None:
        with pytest.raises(OSIError) as exc:
            Field(name="1bad", expression="id", role="dimension")
        assert exc.value.code is ErrorCode.E1005_IDENTIFIER_INVALID

    def test_empty_expression_rejected_E1004(self) -> None:
        with pytest.raises(OSIError) as exc:
            Field(name="id", expression="   ", role="dimension")
        assert exc.value.code is ErrorCode.E1004_TYPE_MISMATCH

    def test_default_role_is_dimension(self) -> None:
        f = Field(name="id", expression="id")
        assert f.role is FieldRole.DIMENSION


class TestMetricValidation:
    def test_happy_path(self) -> None:
        m = Metric(name="revenue", expression="SUM(amount)")
        assert m.name == normalize_identifier("revenue")
        assert "SUM" in m.expression.canonical.upper()

    def test_invalid_identifier_E1005(self) -> None:
        with pytest.raises(OSIError) as exc:
            Metric(name="1rev", expression="SUM(amount)")
        assert exc.value.code is ErrorCode.E1005_IDENTIFIER_INVALID


class TestDatasetValidation:
    def test_happy_path(self) -> None:
        ds = _minimal_dataset()
        assert len(ds.fields) == 2
        assert ds.primary_key == (normalize_identifier("id"),)

    def test_duplicate_field_name_E2003(self) -> None:
        with pytest.raises(OSIError) as exc:
            Dataset(
                name="orders",
                source="sales.orders",
                primary_key=["id"],
                fields=[
                    Field(name="id", expression="id"),
                    Field(name="id", expression="id"),
                ],
            )
        assert exc.value.code is ErrorCode.E2003_DUPLICATE_NAME

    def test_field_metric_name_collision_E2003(self) -> None:
        with pytest.raises(OSIError) as exc:
            Dataset(
                name="orders",
                source="sales.orders",
                fields=[Field(name="amount", expression="amount", role="fact")],
                metrics=[Metric(name="amount", expression="SUM(amount)")],
            )
        assert exc.value.code is ErrorCode.E2003_DUPLICATE_NAME

    def test_unique_keys_coerced_to_tuples(self) -> None:
        ds = Dataset(
            name="orders",
            source="sales.orders",
            primary_key=["id"],
            unique_keys=[["id"], ["order_number"]],
            fields=[
                Field(name="id", expression="id"),
                Field(name="order_number", expression="order_number"),
            ],
        )
        assert ds.unique_keys == (
            (normalize_identifier("id"),),
            (normalize_identifier("order_number"),),
        )

    def test_unique_keys_wrong_shape_E1004(self) -> None:
        with pytest.raises(OSIError) as exc:
            Dataset(
                name="orders",
                source="sales.orders",
                unique_keys="id",  # type: ignore[arg-type]
                fields=[Field(name="id", expression="id")],
            )
        assert exc.value.code is ErrorCode.E1004_TYPE_MISMATCH


class TestRelationshipValidation:
    def test_happy_path_alias_from_to(self) -> None:
        rel = Relationship.model_validate(
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
            }
        )
        assert rel.from_dataset == normalize_identifier("orders")
        assert rel.to_dataset == normalize_identifier("customers")

    def test_referential_integrity_is_a_deferred_key(self) -> None:
        """``referential_integrity`` is a Foundation-deferred key.

        The pydantic schema for ``Relationship`` has no such field
        (``extra="forbid"`` makes the unknown key a hard error). YAML
        callers are routed through :func:`parse_semantic_model`, which
        translates pydantic's ``extra_forbidden`` into the user-facing
        ``E1001_YAML_SYNTAX``; here we assert the underlying pydantic
        behaviour directly so a programmatic construction can't
        reintroduce the field by skipping the parser.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc:
            Relationship.model_validate(
                {
                    "name": "r",
                    "from": "a",
                    "to": "b",
                    "from_columns": ["x"],
                    "to_columns": ["y"],
                    "referential_integrity": {"from_all_rows_match": True},
                }
            )
        error_types = {err["type"] for err in exc.value.errors()}
        assert "extra_forbidden" in error_types
        error_locs = {err["loc"][0] for err in exc.value.errors()}
        assert "referential_integrity" in error_locs

    def test_column_arity_mismatch_E2006(self) -> None:
        with pytest.raises(OSIError) as exc:
            Relationship.model_validate(
                {
                    "name": "r",
                    "from": "a",
                    "to": "b",
                    "from_columns": ["x", "y"],
                    "to_columns": ["z"],
                }
            )
        assert exc.value.code is ErrorCode.E2006_INVALID_RELATIONSHIP

    def test_empty_columns_E2006(self) -> None:
        with pytest.raises(OSIError) as exc:
            Relationship.model_validate(
                {
                    "name": "r",
                    "from": "a",
                    "to": "b",
                    "from_columns": [],
                    "to_columns": [],
                }
            )
        assert exc.value.code is ErrorCode.E2006_INVALID_RELATIONSHIP


class TestParameterValidation:
    def test_default_any(self) -> None:
        p = Parameter(name="min_amount", data_type="NUMBER", default=0)
        assert p.default == 0
        assert p.data_type == "NUMBER"


class TestNamedFilter:
    def test_happy_path(self) -> None:
        nf = NamedFilter(name="done", expression="status = 'done'")
        assert nf.name == normalize_identifier("done")


class TestSemanticModel:
    def test_empty_datasets_rejected_E1002(self) -> None:
        with pytest.raises(OSIError) as exc:
            SemanticModel(name="x", datasets=[])
        assert exc.value.code is ErrorCode.E1002_MISSING_REQUIRED_FIELD

    def test_dataset_name_uniqueness_E2003(self) -> None:
        with pytest.raises(OSIError) as exc:
            SemanticModel(
                name="x",
                datasets=[_minimal_dataset(), _minimal_dataset()],
            )
        assert exc.value.code is ErrorCode.E2003_DUPLICATE_NAME

    def test_metric_name_uniqueness_E2003(self) -> None:
        with pytest.raises(OSIError) as exc:
            SemanticModel(
                name="x",
                datasets=[_minimal_dataset()],
                metrics=[
                    Metric(name="m", expression="SUM(amount)"),
                    Metric(name="m", expression="SUM(amount)"),
                ],
            )
        assert exc.value.code is ErrorCode.E2003_DUPLICATE_NAME

    def test_defaults(self) -> None:
        # B4 (Phase 3 review): the spec mandates ``OSI_SQL_2026`` as the
        # default dialect (Foundation §1 + SQL_EXPRESSION_SUBSET.md).
        # Omitting ``dialect:`` in YAML therefore parses to
        # ``Dialect.OSI_SQL_2026``, not the codegen-target ``ANSI``.
        model = SemanticModel(name="x", datasets=[_minimal_dataset()])
        assert model.dialect is Dialect.OSI_SQL_2026
        assert model.metrics == ()
        assert model.filters == ()
        assert model.parameters == ()
        assert model.osi_version is None

    def test_osi_version_optional_per_spec(self) -> None:
        """A model omitting ``osi_version`` defaults to the latest supported version.

        Per Proposed_OSI_Semantics.md §opening. Storing ``None`` keeps
        that contract explicit.
        """
        model = SemanticModel(name="x", datasets=[_minimal_dataset()])
        assert model.osi_version is None

    def test_osi_version_accepts_supported_value(self) -> None:
        model = SemanticModel(
            name="x", osi_version="0.1", datasets=[_minimal_dataset()]
        )
        assert model.osi_version == "0.1"

    def test_osi_version_rejects_unsupported_value_E1003(self) -> None:
        """Unknown ``osi_version`` values raise ``E1003_INVALID_ENUM_VALUE``.

        Future ``0.x`` revisions stay additively compatible, but until
        an engine is updated to recognise a new version, declaring it
        is rejected with a context that carries the supported set so
        adopters know what they can write.
        """
        with pytest.raises(OSIError) as exc:
            SemanticModel(name="x", osi_version="0.2", datasets=[_minimal_dataset()])
        assert exc.value.code is ErrorCode.E1003_INVALID_ENUM_VALUE
        assert exc.value.context["value"] == "0.2"
        assert "0.1" in exc.value.context["supported"]

    def test_osi_version_non_string_rejected_E1004(self) -> None:
        with pytest.raises(OSIError) as exc:
            SemanticModel(name="x", osi_version=0.1, datasets=[_minimal_dataset()])
        assert exc.value.code is ErrorCode.E1004_TYPE_MISMATCH


_ = OSIParseError  # re-export used by readers of this module
