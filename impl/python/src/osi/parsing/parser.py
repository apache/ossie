"""Top-level parse entry point.

``parse_semantic_model(source)`` takes either a filesystem path or a raw
YAML string and returns a tuple ``(model, namespace, graph)``.

Pipeline:

1. YAML load (syntax error → ``E1001_YAML_SYNTAX``).
2. Root normalization — accept ``{semantic_model: [{...}]}`` or a bare
   model mapping.
3. Deferred-feature screen — reject YAML keys reserved for deferred
   proposals (``E1105``). Done *before* pydantic so we can give a
   friendlier error than "extra field forbidden".
4. Pydantic schema validation (``E1001`` / ``E1002`` / ``E1004``).
5. Deferred-feature expression screen — walk every parsed SQL AST and
   reject window / pivot / grouping-set constructs (``E1105``).
6. Cross-reference validation (``E2xxx``).
7. Foundation-strictness screen — reject deferred constructs not
   enabled in the caller's :class:`~osi.config.FoundationFlags`
   (D-003 / D-027 / per-dataset metrics).
8. Build namespace + relationship graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIParseError
from osi.parsing._root import unwrap_model_root
from osi.parsing.deferred import check_expression_deferred, check_yaml_deferred
from osi.parsing.foundation import check_foundation_strictness
from osi.parsing.function_whitelist import check_expression_functions
from osi.parsing.graph import RelationshipGraph, build_graph
from osi.parsing.models import Field, Metric, NamedFilter, SemanticModel
from osi.parsing.namespace import Namespace, build_namespace
from osi.parsing.validation import validate_model


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Bundle returned by :func:`parse_semantic_model`."""

    model: SemanticModel
    namespace: Namespace
    graph: RelationshipGraph


def parse_semantic_model(
    source: str | Path,
    *,
    flags: FoundationFlags | None = None,
) -> ParseResult:
    """Parse and fully validate a YAML semantic model.

    ``source`` is a filesystem path (``Path``) or a raw YAML string.
    ``flags`` controls which deferred Foundation v0.1 constructs the
    parser tolerates; ``None`` (the default) uses the strict
    Foundation defaults — every flag off — which matches
    ``Proposed_OSI_Semantics.md`` as currently published. Pass an
    explicit :class:`~osi.config.FoundationFlags` instance to opt
    back into legacy behaviour for one or more deferred features.

    Returns a :class:`ParseResult` with the frozen model, namespace, and
    relationship graph. Raises :class:`OSIParseError` with a stable
    ``code`` on any failure.
    """
    if flags is None:
        flags = FoundationFlags()
    document = _load_yaml(source)
    root = unwrap_model_root(document)
    check_yaml_deferred(document)
    model = _build_model(root)
    # S-12: validation runs *before* the per-expression deferred AST
    # check so windowed-metric-composition can fire its named code
    # ``E_WINDOWED_METRIC_COMPOSITION`` before the blanket
    # ``E_DEFERRED_KEY_REJECTED`` swallows it.
    validate_model(model)
    _check_all_expression_asts(model)
    check_foundation_strictness(model, flags)
    namespace = build_namespace(model)
    graph = build_graph(model)
    return ParseResult(model=model, namespace=namespace, graph=graph)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_yaml(source: str | Path) -> Any:
    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSIParseError(
                ErrorCode.E1001_YAML_SYNTAX,
                f"could not read YAML file {source}: {exc}",
                context={"path": str(source)},
            ) from exc
    else:
        text = source
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OSIParseError(
            ErrorCode.E1001_YAML_SYNTAX,
            f"invalid YAML: {exc}",
            context={},
        ) from exc


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


def _build_model(root: dict[str, Any]) -> SemanticModel:
    try:
        return SemanticModel.model_validate(root)
    except OSIParseError:
        raise
    except ValidationError as exc:
        raise _translate_validation_error(exc) from exc


def _translate_validation_error(err: ValidationError) -> OSIParseError:
    """Map a pydantic ``ValidationError`` into a typed :class:`OSIParseError`.

    Pydantic reports multiple issues; we surface the first one with a
    best-fit :class:`ErrorCode` based on the pydantic error type. Callers
    who want the full list can introspect ``err`` on the ``__cause__``.
    """
    raw: list[dict[str, Any]] = [dict(e) for e in err.errors()]
    first: dict[str, Any] = raw[0] if raw else {}
    err_type = str(first.get("type", ""))
    loc = ".".join(str(p) for p in first.get("loc", ()))
    message = str(first.get("msg", "schema validation failed"))
    if err_type == "extra_forbidden":
        code = ErrorCode.E1001_YAML_SYNTAX
    elif err_type in {"missing", "none_not_allowed"}:
        code = ErrorCode.E1002_MISSING_REQUIRED_FIELD
    elif err_type.startswith("enum"):
        code = ErrorCode.E1003_INVALID_ENUM_VALUE
    else:
        code = ErrorCode.E1004_TYPE_MISMATCH
    return OSIParseError(
        code,
        f"{loc}: {message}" if loc else message,
        context={
            "pydantic_type": err_type,
            "location": loc,
            "errors": raw,
        },
    )


# ---------------------------------------------------------------------------
# Post-pydantic AST screen
# ---------------------------------------------------------------------------


def _check_all_expression_asts(model: SemanticModel) -> None:
    for ds in model.datasets:
        for f in ds.fields:
            _check_field_expression(f, dataset_name=str(ds.name))
        for m in ds.metrics:
            _check_metric_expression(m, scope=f"{ds.name}")
    for m in model.metrics:
        _check_metric_expression(m, scope="model")
    for named_filter in model.filters:
        _check_filter_expression(named_filter)


def _check_field_expression(field: Field, *, dataset_name: str) -> None:
    where = f"field {dataset_name}.{field.name}"
    check_expression_deferred(field.expression, where=where)
    check_expression_functions(field.expression, where=where)


def _check_metric_expression(metric: Metric, *, scope: str) -> None:
    where = f"metric {scope}.{metric.name}"
    check_expression_deferred(metric.expression, where=where)
    check_expression_functions(metric.expression, where=where)


def _check_filter_expression(named_filter: NamedFilter) -> None:
    where = f"filter {named_filter.name}"
    check_expression_deferred(named_filter.expression, where=where)
    check_expression_functions(named_filter.expression, where=where)


__all__ = ["ParseResult", "parse_semantic_model"]
