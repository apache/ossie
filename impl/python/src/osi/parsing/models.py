"""Pydantic schemas for the Foundation semantic model.

Every model uses ``model_config = {"extra": "forbid"}`` — unknown fields
are a hard parse error (``E1001``). This is how we keep the Foundation
thin: new concepts must be added intentionally, not by accident.

Expression strings are parsed into frozen SQLGlot ASTs at validation
time so downstream code never touches raw SQL.

Deferred-feature detection lives in :mod:`osi.parsing.deferred` — these
schemas describe only the shapes the Foundation accepts; anything else
produces ``E1001`` / ``E1002`` / ``E1004`` via pydantic, or ``E1105`` via
the deferred-feature visitor.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Iterable, Optional

import sqlglot
from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField
from pydantic import StringConstraints, field_validator, model_validator

from osi.common.identifiers import Identifier, is_valid_identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.common.types import Dialect
from osi.errors import ErrorCode, OSIParseError

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


def _validate_identifier(value: object) -> Identifier:
    """Validate + normalize a user-supplied identifier."""
    if not isinstance(value, str) or not value:
        raise OSIParseError(
            ErrorCode.E1004_TYPE_MISMATCH,
            "identifier must be a non-empty string",
            context={"value": value},
        )
    if not is_valid_identifier(value):
        raise OSIParseError(
            ErrorCode.E1005_IDENTIFIER_INVALID,
            f"{value!r} is not a valid OSI identifier",
            context={"value": value},
        )
    return normalize_identifier(value)


def _parse_expression(source: object, *, kind: str) -> FrozenSQL:
    """Parse a scalar / aggregate SQL expression into a frozen AST."""
    if not isinstance(source, str) or not source.strip():
        raise OSIParseError(
            ErrorCode.E1004_TYPE_MISMATCH,
            f"{kind} expression must be a non-empty string",
            context={"value": source},
        )
    _check_pre_parse_window_rules(source, kind=kind)
    try:
        expr = parse_sql_expr(source)
    except OSIParseError:
        raise
    except Exception as exc:  # pragma: no cover — SQLGlot internals
        raise OSIParseError(
            ErrorCode.E1001_YAML_SYNTAX,
            f"could not parse {kind} expression: {exc}",
            context={"expression": source},
        ) from exc
    return FrozenSQL.of(expr)


# S-12: pre-parse window-frame rejection. SQLGlot does not parse
# ``GROUPS`` frame clauses (no SQL dialect we ship implements them),
# so we have to detect the keyword in the raw expression *before*
# handing it to sqlglot — otherwise the user sees the SQLGlot
# parser-error wrapped in ``E1001_YAML_SYNTAX`` instead of the
# named Foundation ``E_DEFERRED_FRAME_MODE`` code.
_GROUPS_FRAME_PATTERN = re.compile(r"\bGROUPS\s+BETWEEN\b", re.IGNORECASE)


def _check_pre_parse_window_rules(source: str, *, kind: str) -> None:
    if _GROUPS_FRAME_PATTERN.search(source):
        raise OSIParseError(
            ErrorCode.E_DEFERRED_FRAME_MODE,
            (
                f"{kind} expression uses ``GROUPS`` frame mode which is "
                "deferred from Foundation v0.1 (D-032 — only literal "
                "ROWS / RANGE frames are accepted)"
            ),
            context={"expression": source, "reason": "frame mode 'GROUPS'"},
        )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DatasetRole(StrEnum):
    """Optional dataset role hint — per ``Proposed_OSI_Semantics.md §4.2``.

    Diagnostic only: does not change planning. The planner discovers
    bridges from cardinality alone. Authors MAY tag a dataset's role
    so that ``osi describe`` and error messages can reference it.
    """

    FACT = "fact"
    DIMENSION = "dimension"
    BRIDGE = "bridge"


class FieldRole(StrEnum):
    """Field role — per ``Proposed_OSI_Semantics.md §4.3``."""

    DIMENSION = "dimension"
    FACT = "fact"
    TIME_DIMENSION = "time_dimension"


# Backwards-compat aliases for YAML inputs that follow the
# ``OSI_core_file_format.md`` upper-case spelling. The canonical form
# is :class:`osi.common.types.Dialect`; this map is consulted by the
# ``SemanticModel.dialect`` field validator below.
_DIALECT_ALIASES: dict[str, Dialect] = {
    "ANSI_SQL": Dialect.ANSI,
    "ANSI": Dialect.ANSI,
    "DUCKDB": Dialect.DUCKDB,
    "SNOWFLAKE": Dialect.SNOWFLAKE,
}


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    """Base class: frozen, extra-forbidding pydantic model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,  # FrozenSQL
        populate_by_name=True,
    )


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class ReferentialIntegrity(_Strict):
    """Optional RI hints for a relationship (``§4.4``)."""

    from_all_rows_match: bool = False
    to_all_rows_match: bool = False


class Field(_Strict):
    """A dataset field — dimension, fact, or time dimension (``§4.3``)."""

    name: Identifier
    expression: FrozenSQL
    role: FieldRole = FieldRole.DIMENSION
    data_type: Optional[NonEmptyStr] = None
    description: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))

    @field_validator("expression", mode="before")
    @classmethod
    def _parse_expression(cls, value: object) -> FrozenSQL:
        if isinstance(value, FrozenSQL):
            return value
        return _parse_expression(str(value), kind="field")


class JoinType(StrEnum):
    """Per-metric join-type override (``§6.7``).

    ``RIGHT`` is deliberately excluded: the spec calls it out as never
    being the clearest expression of intent. ``FULL`` is reserved
    today — only ``INNER`` and ``LEFT`` are wired into the planner.
    """

    INNER = "INNER"
    LEFT = "LEFT"
    FULL = "FULL"


class MetricJoins(_Strict):
    """Per-metric join overrides (``Proposed_OSI_Semantics.md §6.7``).

    Both fields are optional. When ``using_relationships`` is set, the
    planner restricts the candidate edges for this metric's
    enrichment paths to the named relationships — which is how the
    spec resolves ``E3001_AMBIGUOUS_JOIN_PATH`` (``§6.6``). When
    ``type`` is set, every aggregation join performed for this metric
    uses the override; today only ``LEFT`` (the default) and ``INNER``
    are wired through. ``FULL`` parses successfully but is reserved.
    """

    using_relationships: tuple[Identifier, ...] = ()
    type: Optional[JoinType] = None

    @field_validator("using_relationships", mode="before")
    @classmethod
    def _normalize_relationships(cls, value: object) -> tuple[Identifier, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise OSIParseError(
                ErrorCode.E1004_TYPE_MISMATCH,
                "joins.using_relationships must be a list of identifiers",
                context={"value": value},
            )
        return tuple(_validate_identifier(str(v)) for v in value)


class Metric(_Strict):
    """A metric — aggregate expression (``§4.5``)."""

    name: Identifier
    expression: FrozenSQL
    description: Optional[str] = None
    joins: Optional[MetricJoins] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))

    @field_validator("expression", mode="before")
    @classmethod
    def _parse_expression(cls, value: object) -> FrozenSQL:
        if isinstance(value, FrozenSQL):
            return value
        return _parse_expression(str(value), kind="metric")


class Dataset(_Strict):
    """A logical dataset (``§4.2``)."""

    name: Identifier
    source: NonEmptyStr
    primary_key: tuple[Identifier, ...] = ()
    unique_keys: tuple[tuple[Identifier, ...], ...] = ()
    fields: tuple[Field, ...] = ()
    metrics: tuple[Metric, ...] = ()
    description: Optional[str] = None
    role: Optional[DatasetRole] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))

    @field_validator("primary_key", mode="before")
    @classmethod
    def _normalize_pk(cls, value: object) -> tuple[Identifier, ...]:
        return _coerce_key_tuple(value, field_name="primary_key")

    @field_validator("unique_keys", mode="before")
    @classmethod
    def _normalize_uks(cls, value: object) -> tuple[tuple[Identifier, ...], ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise OSIParseError(
                ErrorCode.E1004_TYPE_MISMATCH,
                "unique_keys must be a list of lists",
                context={"value": value},
            )
        return tuple(
            _coerce_key_tuple(uk, field_name="unique_keys entry") for uk in value
        )

    @model_validator(mode="after")
    def _check_field_uniqueness(self) -> "Dataset":
        seen: set[Identifier] = set()
        for f in self.fields:
            if f.name in seen:
                raise OSIParseError(
                    ErrorCode.E2003_DUPLICATE_NAME,
                    f"dataset {self.name!r} declares field {f.name!r} twice",
                    context={"dataset": self.name, "field": f.name},
                )
            seen.add(f.name)
        metric_seen: set[Identifier] = set()
        for m in self.metrics:
            if m.name in metric_seen or m.name in seen:
                raise OSIParseError(
                    ErrorCode.E2003_DUPLICATE_NAME,
                    f"dataset {self.name!r} name {m.name!r} declared twice",
                    context={"dataset": self.name, "name": m.name},
                )
            metric_seen.add(m.name)
        return self


class Relationship(_Strict):
    """An equijoin relationship (``§4.4``)."""

    name: Identifier
    from_dataset: Identifier = PydField(alias="from")
    to_dataset: Identifier = PydField(alias="to")
    from_columns: tuple[Identifier, ...]
    to_columns: tuple[Identifier, ...]
    referential_integrity: Optional[ReferentialIntegrity] = None
    description: Optional[str] = None

    @field_validator("name", "from_dataset", "to_dataset", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))

    @field_validator("from_columns", "to_columns", mode="before")
    @classmethod
    def _normalize_columns(cls, value: object) -> tuple[Identifier, ...]:
        return _coerce_key_tuple(value, field_name="join columns")

    @model_validator(mode="after")
    def _check_arity(self) -> "Relationship":
        if len(self.from_columns) != len(self.to_columns):
            raise OSIParseError(
                ErrorCode.E2006_INVALID_RELATIONSHIP,
                (
                    f"relationship {self.name!r}: from_columns and to_columns "
                    "must have the same length"
                ),
                context={
                    "name": self.name,
                    "from_columns": list(self.from_columns),
                    "to_columns": list(self.to_columns),
                },
            )
        if not self.from_columns:
            raise OSIParseError(
                ErrorCode.E2006_INVALID_RELATIONSHIP,
                f"relationship {self.name!r} has no join columns",
                context={"name": self.name},
            )
        return self


class NamedFilter(_Strict):
    """Reusable boolean filter (``§4.6``)."""

    name: Identifier
    expression: FrozenSQL
    description: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))

    @field_validator("expression", mode="before")
    @classmethod
    def _parse_expression(cls, value: object) -> FrozenSQL:
        if isinstance(value, FrozenSQL):
            return value
        return _parse_expression(str(value), kind="filter")


class Parameter(_Strict):
    """Typed query-time parameter with a default."""

    name: Identifier
    data_type: NonEmptyStr
    default: Any = None
    description: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))


class SemanticModel(_Strict):
    """Top-level semantic model (``§4.1``)."""

    name: Identifier
    dialect: Dialect = Dialect.ANSI
    datasets: tuple[Dataset, ...]
    relationships: tuple[Relationship, ...] = ()
    metrics: tuple[Metric, ...] = ()
    filters: tuple[NamedFilter, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    description: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> Identifier:
        return _validate_identifier(str(value))

    @field_validator("dialect", mode="before")
    @classmethod
    def _normalize_dialect(cls, value: object) -> object:
        """Accept SPEC upper-case spellings as well as canonical values.

        ``ANSI_SQL`` and ``ANSI`` both map to :attr:`Dialect.ANSI`; same
        for the per-dialect spellings in :data:`_DIALECT_ALIASES`.
        """
        if isinstance(value, Dialect):
            return value
        if isinstance(value, str):
            alias = _DIALECT_ALIASES.get(value)
            if alias is not None:
                return alias
        return value

    @field_validator("datasets", mode="before")
    @classmethod
    def _datasets_nonempty(cls, value: object) -> object:
        if not value:
            raise OSIParseError(
                ErrorCode.E1002_MISSING_REQUIRED_FIELD,
                "semantic_model must declare at least one dataset",
            )
        return value

    @model_validator(mode="after")
    def _check_global_uniqueness(self) -> "SemanticModel":
        _require_unique("dataset", (d.name for d in self.datasets))
        _require_unique("relationship", (r.name for r in self.relationships))
        _require_unique("metric", (m.name for m in self.metrics))
        _require_unique("filter", (f.name for f in self.filters))
        _require_unique("parameter", (p.name for p in self.parameters))
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_key_tuple(value: object, *, field_name: str) -> tuple[Identifier, ...]:
    """Accept list / tuple of strings; normalize each to an Identifier."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise OSIParseError(
            ErrorCode.E1004_TYPE_MISMATCH,
            f"{field_name} must be a list of identifiers",
            context={"value": value},
        )
    return tuple(_validate_identifier(str(item)) for item in value)


def _require_unique(kind: str, names: Iterable[Identifier]) -> None:
    seen: set[Identifier] = set()
    for n in names:
        if n in seen:
            raise OSIParseError(
                ErrorCode.E2003_DUPLICATE_NAME,
                f"{kind} {n!r} declared twice at the model scope",
                context={"kind": kind, "name": n},
            )
        seen.add(n)


# Keep sqlglot import here so removing helper `_parse_expression` never
# silently drops it from linter scans.
_ = sqlglot  # noqa: F841


__all__ = [
    "Dataset",
    "Dialect",
    "Field",
    "FieldRole",
    "Metric",
    "NamedFilter",
    "Parameter",
    "ReferentialIntegrity",
    "Relationship",
    "SemanticModel",
]
