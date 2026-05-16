#!/usr/bin/env python3
"""OSI compliance suite adapter for the Python reference implementation.

A thin translator from the suite's CLI contract (see
[`compliance/ADAPTER_INTERFACE.md`](../../../compliance/ADAPTER_INTERFACE.md))
to the `osi.*` API. Format conversion only — no validation, planning,
or SQL generation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ADAPTER_DIR = Path(__file__).resolve().parent
_OSI_PYTHON_SRC = _ADAPTER_DIR.parent / "src"
if _OSI_PYTHON_SRC.exists():
    sys.path.insert(0, str(_OSI_PYTHON_SRC))

import sqlglot  # noqa: E402
import yaml  # noqa: E402

from osi.codegen import Dialect, compile_plan  # noqa: E402
from osi.common.identifiers import normalize_identifier  # noqa: E402
from osi.common.sql_expr import FrozenSQL  # noqa: E402
from osi.errors import (  # noqa: E402
    ErrorCode,
    OSIError,
    OSIParseError,
)

# S-10: legacy numeric codes → Appendix C named codes for user-facing
# diagnostics. The internal algebra layer keeps the legacy codes (so
# the algebra contract stays narrow); translation happens at the
# adapter boundary.
_LEGACY_CODE_MAP: dict[ErrorCode, ErrorCode] = {
    ErrorCode.E2002_NAME_NOT_FOUND: ErrorCode.E_NAME_NOT_FOUND,
    ErrorCode.E2001_AMBIGUOUS_NAME: ErrorCode.E_NAME_COLLISION,
    ErrorCode.E2003_DUPLICATE_NAME: ErrorCode.E_NAME_COLLISION,
    ErrorCode.E2004_UNREACHABLE_DATASET: ErrorCode.E_NO_PATH,
    ErrorCode.E2008_RESERVED_IDENTIFIER: ErrorCode.E_RESERVED_IDENTIFIER,
    ErrorCode.E3001_AMBIGUOUS_JOIN_PATH: ErrorCode.E_AMBIGUOUS_PATH,
    # E3013_NO_STITCHING_DIMENSION is kept as ``E3013`` per Appendix C:
    # it is a *distinct* condition from generic path resolution failure
    # (``E_NO_PATH``) — two unrelated facts with no shared stitch
    # dimension would yield a Cartesian product, not a missing path.
    # No mapping → the numeric code surfaces unchanged.
}
from osi.parsing.graph import build_graph  # noqa: E402
from osi.parsing.namespace import build_namespace  # noqa: E402
from osi.parsing.parser import parse_semantic_model  # noqa: E402
from osi.planning import (  # noqa: E402
    OrderBy,
    Reference,
    SemanticQuery,
    SortDirection,
    plan,
)
from osi.planning.planner_context import PlannerContext  # noqa: E402


# Fields in the suite carry an optional ``dimension:`` marker; absence of
# the marker means "fact". The osi_python Foundation uses an explicit
# ``role`` enum instead. Format conversion only — no semantic changes.
def _translate_field(field: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: v
        for k, v in field.items()
        if k not in {"dimension", "fact", "snapshot_dimensions"}
    }
    if "dimension" in field:
        marker = field["dimension"] or {}
        if isinstance(marker, dict) and marker.get("is_time"):
            out["role"] = "time_dimension"
        else:
            out["role"] = "dimension"
    else:
        out["role"] = "fact"
    return out


def _translate_model(raw: dict[str, Any]) -> dict[str, Any]:
    """Reshape suite-format YAML into osi_python's schema."""
    out = dict(raw)
    datasets = []
    for ds in raw.get("datasets", []) or []:
        ds_out = dict(ds)
        ds_out["fields"] = [_translate_field(f) for f in ds.get("fields", []) or []]
        datasets.append(ds_out)
    out["datasets"] = datasets
    return out


def _load_translated_model(model_path: Path):  # noqa: ANN202
    raw = yaml.safe_load(model_path.read_text())
    translated = _translate_model(raw or {})
    # Round-trip through YAML so the parser sees the canonical shape
    # and surfaces the same ``E1xxx`` errors it would for any user model.
    return parse_semantic_model(yaml.safe_dump(translated))


def _ref(name: str, dataset: str | None = None) -> Reference:
    return Reference(
        dataset=normalize_identifier(dataset) if dataset else None,
        name=normalize_identifier(name),
    )


def _dim_ref(spec: Any) -> Reference:
    """Build a dimension reference from a bare string or dict."""
    if isinstance(spec, str):
        ds, _, name = spec.partition(".")
        return _ref(name or ds, ds if name else None)
    return _ref(str(spec.get("name") or spec["field"]), spec.get("dataset"))


def _measure_ref(spec: Any) -> Reference:
    """Suite measures are ``{"name": alias, "metric": metric_name}``."""
    if isinstance(spec, str):
        return _ref(spec)
    name = spec.get("metric") or spec.get("name") or spec.get("field")
    if name is None:
        raise ValueError(f"measure has no metric: {spec!r}")
    return _ref(str(name), spec.get("source_dataset") or spec.get("dataset"))


def _measure_aliases(specs: list[Any]) -> list[tuple[str, str]]:
    """Collect ``(metric_name, output_alias)`` pairs from measure specs.

    The suite shape ``{"name": "revenue", "metric": "total_revenue"}``
    means "compute the metric ``total_revenue`` and emit the column
    as ``revenue``". Only emit a pair when the alias differs from the
    metric name; same-name pairs are no-ops at codegen time.
    """
    out: list[tuple[str, str]] = []
    for spec in specs:
        if isinstance(spec, str):
            continue
        metric = spec.get("metric") or spec.get("field")
        alias = spec.get("name")
        if metric and alias and metric != alias:
            out.append((str(metric), str(alias)))
    return out


def _compile_filters(exprs: list[str]) -> FrozenSQL | None:
    """AND a list of SQL predicates into a single :class:`FrozenSQL`."""
    if not exprs:
        return None
    parsed = [sqlglot.parse_one(e) for e in exprs]
    combined = parsed[0]
    for node in parsed[1:]:
        combined = sqlglot.exp.And(this=combined, expression=node)
    return FrozenSQL.of(combined)


def _order_entry(entry: dict[str, Any]) -> OrderBy:
    descending = (
        entry.get("descending") or str(entry.get("direction", "ASC")).upper() == "DESC"
    )
    target = entry.get("target") or entry.get("name") or entry.get("field")
    return OrderBy(
        target=_dim_ref(target),
        direction=SortDirection.DESC if descending else SortDirection.ASC,
    )


def _build_semantic_query(qdict: dict[str, Any]) -> SemanticQuery:
    """Translate the suite's query JSON into a :class:`SemanticQuery`.

    Foundation v0.1 (D-010 / D-011) routes by query shape. The
    presence of the ``fields`` key in the JSON — even when empty —
    signals scalar-query intent so the right empty-shape error
    code (``E_EMPTY_SCALAR_QUERY`` vs ``E_EMPTY_AGGREGATION_QUERY``)
    is raised. ``SemanticQuery`` itself can't tell empty-list from
    missing-key, so the disambiguation lives at the adapter boundary
    where the user input format is known.
    """
    # Reject deferred query-level keys before any translation. The
    # deferred catalog lives in osi.parsing.deferred; the adapter is
    # the only place that sees raw query JSON, so the check belongs
    # here (the planner only sees translated SemanticQuery values
    # which don't carry these keys at all).
    from osi.parsing.deferred import DEFERRED_QUERY_KEYS

    deferred_present = sorted(set(qdict) & DEFERRED_QUERY_KEYS)
    if deferred_present:
        first = deferred_present[0]
        raise OSIParseError(
            ErrorCode.E_DEFERRED_KEY_REJECTED,
            (
                f"query uses deferred key {first!r}; "
                "see Proposed_OSI_Semantics.md §10 / D-009"
            ),
            context={
                "where": "semantic_query",
                "keys": tuple(deferred_present),
            },
        )
    # Suite contract: ``filters`` ⇒ Where (pre-aggregate), ``qualify``
    # ⇒ Having (post-aggregate). Foundation v0.1 routes by resolved
    # expression shape (D-005), but the suite preserves the user's
    # placement intent so the planner can validate it (D-012).
    filters_raw = list(qdict.get("filters") or [])
    qualify_raw = list(qdict.get("qualify") or [])
    has_fields_key = "fields" in qdict
    fields = qdict.get("fields") or ()
    dimensions = qdict.get("dimensions") or ()
    measures = qdict.get("measures") or ()
    if has_fields_key and not fields and not dimensions and not measures:
        raise OSIParseError(
            ErrorCode.E_EMPTY_SCALAR_QUERY,
            (
                "scalar query has empty Fields list; declare at least "
                "one field. See Proposed_OSI_Semantics.md D-011."
            ),
        )
    return SemanticQuery(
        dimensions=tuple(_dim_ref(d) for d in dimensions),
        measures=tuple(_measure_ref(m) for m in measures),
        fields=tuple(_dim_ref(f) for f in fields),
        where=_compile_filters(filters_raw),
        having=_compile_filters(qualify_raw),
        order_by=tuple(_order_entry(e) for e in (qdict.get("order_by") or ())),
        limit=qdict.get("limit"),
    )


def cmd_sql(args: argparse.Namespace) -> int:
    """Emit SQL for ``--model`` + ``--query-file`` using ``--dialect``."""
    try:
        result = _load_translated_model(Path(args.model))
        ctx = PlannerContext(
            model=result.model,
            namespace=build_namespace(result.model),
            graph=build_graph(result.model),
        )
        qdict = json.loads(Path(args.query_file).read_text())
        query = _build_semantic_query(qdict)
        dialect = Dialect[args.dialect.upper()]
        compiled_plan = plan(query, ctx)
        aliases = _measure_aliases(list(qdict.get("measures") or []))
        if aliases:
            from dataclasses import replace as _replace

            from osi.common.identifiers import (
                normalize_identifier as _norm,
            )

            compiled_plan = _replace(
                compiled_plan,
                output_aliases=tuple(
                    (_norm(metric), _norm(alias)) for metric, alias in aliases
                ),
            )
        sql = compile_plan(compiled_plan, dialect=dialect)
    except OSIError as err:
        code = _LEGACY_CODE_MAP.get(err.code, err.code)
        sys.stderr.write(f"{code.value}: {err}\n")
        return 1
    except Exception as err:  # noqa: BLE001 — intentional top-level catch
        sys.stderr.write(f"Error: {err}\n")
        return 1
    sys.stdout.write(sql.rstrip() + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="osi_python_adapter")
    sub = parser.add_subparsers(dest="command")
    p_sql = sub.add_parser("sql", help="Generate SQL from a model + query")
    p_sql.add_argument("--model", required=True)
    p_sql.add_argument("--query-file", required=True)
    p_sql.add_argument("--dialect", default="duckdb")
    p_sql.set_defaults(func=cmd_sql)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
