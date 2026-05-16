"""Thin command-line surface for the diagnostics module.

The CLI is deliberately minimal: it exists so humans and CI jobs can
reach :func:`osi.diagnostics.describe`, :func:`explain`, and
:func:`resolve` without writing a driver script. Heavy lifting belongs
in library code; this module does argument parsing and I/O only.

Usage::

    python -m osi describe <model.yaml>
    python -m osi explain   <model.yaml> <query.json>
    python -m osi resolve   <model.yaml> <query.json>

Add ``--json`` to any subcommand to emit machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlglot

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.diagnostics import (
    describe,
    describe_json,
    explain,
    explain_json,
    resolve,
    resolve_json,
)
from osi.diagnostics.error_catalog import all_explanations, explain_error
from osi.errors import ErrorCode, OSIError
from osi.parsing.parser import parse_semantic_model
from osi.planning import OrderBy, Reference, SemanticQuery, SortDirection, plan
from osi.planning.planner_context import PlannerContext


def _load_context(path: Path) -> PlannerContext:
    """Load a model file and reuse the parser's pre-built indexes.

    :func:`parse_semantic_model` already builds and caches both the
    namespace and the relationship graph; rebuilding them here would
    re-do the same work and silently risk drift if either builder
    grows side-effects.
    """
    result = parse_semantic_model(path.read_text())
    return PlannerContext(
        model=result.model,
        namespace=result.namespace,
        graph=result.graph,
    )


def _load_query(path: Path) -> SemanticQuery:
    data = json.loads(path.read_text())

    def _ref(spec: dict[str, object]) -> Reference:
        dataset_raw = spec.get("dataset")
        return Reference(
            dataset=(
                normalize_identifier(str(dataset_raw))
                if dataset_raw is not None
                else None
            ),
            name=normalize_identifier(str(spec["name"])),
        )

    where = (
        FrozenSQL.of(sqlglot.parse_one(data["where"])) if data.get("where") else None
    )
    order_by = tuple(
        OrderBy(
            target=_ref(entry["target"]),
            direction=(
                SortDirection.DESC if entry.get("descending") else SortDirection.ASC
            ),
        )
        for entry in data.get("order_by", [])
    )
    return SemanticQuery(
        dimensions=tuple(_ref(r) for r in data.get("dimensions", [])),
        measures=tuple(_ref(r) for r in data.get("measures", [])),
        where=where,
        order_by=order_by,
        limit=data.get("limit"),
    )


def _emit(args: argparse.Namespace, text: str, data: object) -> None:
    if getattr(args, "json", False):
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


def _cmd_describe(args: argparse.Namespace) -> int:
    ctx = _load_context(Path(args.model))
    _emit(args, describe(ctx.model), describe_json(ctx.model))
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    ctx = _load_context(Path(args.model))
    query = _load_query(Path(args.query))
    plan_ = plan(query, ctx)
    _emit(args, explain(plan_), explain_json(plan_))
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    ctx = _load_context(Path(args.model))
    query = _load_query(Path(args.query))
    _emit(args, resolve(query, ctx), resolve_json(query, ctx))
    return 0


def _cmd_explain_code(args: argparse.Namespace) -> int:
    """Print the prose explanation for a single OSI error code.

    The catalogue lives in :mod:`osi.diagnostics.error_catalog` so this
    command's only job is argument resolution and pretty-printing.
    """
    if getattr(args, "list", False):
        return _cmd_explain_code_list(args)
    raw = (args.code or "").strip()
    if not raw:
        sys.stderr.write("error: an OSI error code is required (e.g. E_NAME_NOT_FOUND)\n")
        return 2
    try:
        code = _resolve_error_code(raw)
    except KeyError:
        sys.stderr.write(
            f"error: {raw!r} is not a known OSI error code. "
            "Run `osi explain-code --list` to see all codes.\n"
        )
        return 2
    if getattr(args, "json", False):
        json.dump(
            {"code": code.value, "explanation": explain_error(code)},
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    else:
        sys.stdout.write(f"{code.value}\n\n{explain_error(code)}\n")
    return 0


def _cmd_explain_code_list(args: argparse.Namespace) -> int:
    """List every error code and its short explanation."""
    explanations = all_explanations()
    if getattr(args, "json", False):
        json.dump(
            {c.value: explanations[c] for c in sorted(explanations, key=lambda c: c.value)},
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
        return 0
    for code in sorted(explanations, key=lambda c: c.value):
        head = _first_sentence(explanations[code])
        sys.stdout.write(f"{code.value:36}  {head}\n")
    return 0


def _first_sentence(text: str, max_len: int = 100) -> str:
    """Return the first sentence of ``text``, ignoring ``e.g.`` / ``i.e.`` periods."""
    cleaned = " ".join(text.split())
    sentinel = "\x00"
    safe = cleaned.replace("e.g.", "e" + sentinel + "g" + sentinel).replace(
        "i.e.", "i" + sentinel + "e" + sentinel
    )
    head = safe.split(". ", 1)[0].replace(sentinel, ".")
    if not head.endswith("."):
        head += "."
    if len(head) > max_len:
        head = head[: max_len - 1].rstrip() + "…"
    return head


def _resolve_error_code(raw: str) -> ErrorCode:
    """Look up an :class:`ErrorCode` by either its enum name or value.

    Accepts both the numeric form (``E2002``) and the named form
    (``E_NAME_NOT_FOUND``); case-insensitive on the named form.
    """
    upper = raw.upper()
    for code in ErrorCode:
        if code.value == upper or code.name == upper:
            return code
    raise KeyError(raw)


def _cmd_compile(args: argparse.Namespace) -> int:
    ctx = _load_context(Path(args.model))
    query = _load_query(Path(args.query))
    dialect = Dialect[args.dialect.upper()]
    sql = compile_plan(plan(query, ctx), dialect=dialect)
    sys.stdout.write(sql)
    if not sql.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    describe_p = sub.add_parser("describe", help="Render a semantic model.")
    describe_p.add_argument("model", help="Path to the model YAML.")
    describe_p.add_argument("--json", action="store_true")
    describe_p.set_defaults(func=_cmd_describe)

    explain_p = sub.add_parser("explain", help="Render a plan for a query.")
    explain_p.add_argument("model")
    explain_p.add_argument("query", help="Path to a JSON query file.")
    explain_p.add_argument("--json", action="store_true")
    explain_p.set_defaults(func=_cmd_explain)

    resolve_p = sub.add_parser(
        "resolve", help="Show which model elements a query touches."
    )
    resolve_p.add_argument("model")
    resolve_p.add_argument("query")
    resolve_p.add_argument("--json", action="store_true")
    resolve_p.set_defaults(func=_cmd_resolve)

    compile_p = sub.add_parser("compile", help="Compile a query to SQL.")
    compile_p.add_argument("model")
    compile_p.add_argument("query")
    compile_p.add_argument(
        "--dialect",
        default="ansi",
        choices=[d.name.lower() for d in Dialect],
    )
    compile_p.set_defaults(func=_cmd_compile)

    explain_code_p = sub.add_parser(
        "explain-code",
        help="Explain an OSI error code (e.g. E_NAME_NOT_FOUND, E2002).",
    )
    explain_code_p.add_argument(
        "code",
        nargs="?",
        help="Error code by enum name or numeric value.",
    )
    explain_code_p.add_argument(
        "--list",
        action="store_true",
        help="List every known error code with a short explanation.",
    )
    explain_code_p.add_argument("--json", action="store_true")
    explain_code_p.set_defaults(func=_cmd_explain_code)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the selected subcommand."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except OSIError as err:
        sys.stderr.write(f"{err.code.value}: {err}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
