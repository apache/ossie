# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Command-line interface for the Apache Ossie <-> Holistics AML converter.

    ossie-holistics to-ossie <compiled.dataset.aml.json> -o <out.yaml> --sql-dialect BIGQUERY
    ossie-holistics to-aml   <ossie.yaml>                -o <out-dir>  --sql-dialect BIGQUERY

`to-ossie` reads the JSON `holistics aml compile` produces. `to-aml` writes AML
source text, which is the format that CLI reads back:

    holistics aml compile <file>.dataset.aml -r <root> -o <dir>
    holistics aml validate <out-dir>/<name>.dataset.aml -r <out-dir>

Every declared loss is written as a YAML sequence of issues, to `--issues` when
given and to stderr otherwise, never mixed into the document output. The process
exits 1 when that log holds an ERROR-severity issue and 0 otherwise. A
conversion that only warned is still a successful conversion, and failing on a
warning would teach scripts to ignore it.

Neither subcommand overwrites an existing output file unless `--force` is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import _yaml, aml_to_ossie, ossie_to_aml, sqlrefs
from .errors import ConversionError
from .issues import IssueLog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ossie-holistics",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    to_ossie = sub.add_parser(
        "to-ossie", help="compiled Holistics AML JSON -> one Apache Ossie semantic model"
    )
    to_ossie.add_argument(
        "input", metavar="COMPILED_JSON", help="output of `holistics aml compile`"
    )
    to_ossie.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="output Apache Ossie YAML file"
    )
    to_ossie.add_argument(
        "--sql-dialect",
        required=True,
        metavar="DIALECT",
        choices=sorted(sqlrefs.SQLGLOT_DIALECT),
        help="the Ossie dialect a computed `@sql` body is written for. AML does not "
        "record the warehouse. The choices are the dialects sqlglot can parse, because "
        "`to-aml` has to read the expression back to restore its field references",
    )
    to_ossie.add_argument(
        "--issues", metavar="FILE", help="write the issue log as YAML here (default: stderr)"
    )
    to_ossie.add_argument(
        "--force", action="store_true", help="overwrite an existing output file"
    )

    to_aml = sub.add_parser("to-aml", help="one Apache Ossie semantic model -> AML source files")
    to_aml.add_argument("input", metavar="OSSIE_YAML", help="Apache Ossie YAML document")
    to_aml.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="DIR",
        help="output directory. One dataset file plus one model file per dataset is "
        "written here, created if it does not already exist",
    )
    to_aml.add_argument(
        "--sql-dialect",
        required=True,
        metavar="DIALECT",
        choices=sorted(sqlrefs.SQLGLOT_DIALECT),
        help="the warehouse the Holistics connection reads. Every `@sql` body is "
        "written in this dialect. An expression already in it is written as it stands, "
        "and an ANSI_SQL or OSSIE_SQL_2026 one is rendered into it",
    )
    to_aml.add_argument(
        "--data-source-name",
        metavar="NAME",
        help="the Holistics connection the models read from. AML requires it and Ossie "
        "has no field for it, so a document this converter did not write needs it",
    )
    to_aml.add_argument(
        "--issues", metavar="FILE", help="write the issue log as YAML here (default: stderr)"
    )
    to_aml.add_argument(
        "--force", action="store_true", help="overwrite existing output files"
    )
    return parser


def _safe_target(directory: Path, relative: str) -> Path:
    """`directory / relative`, which has to resolve inside `directory`.

    A model name comes from user-controlled document content, and the reverse
    path turns it into a file path. Both sides are resolved before comparing,
    because a string-prefix check is wrong on a filesystem with symlinks, as on
    macOS where `/tmp` links to `/private/tmp`.
    """
    root = directory.resolve()
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise ConversionError(
            f"{relative!r} resolves outside the output directory {root}, so nothing is written"
        )
    return target


def _colliding(paths: list[Path]) -> list[Path]:
    """Any path this run would write more than once.

    Compared after `resolve()` and case-folded, because two spellings of one
    file are the same file on disk, and the default filesystem on macOS and
    Windows folds case. Checked before any write, so `--issues` pointing at `-o`
    cannot replace the converted document with the issue log.
    """
    seen: dict[str, Path] = {}
    counts: Counter[str] = Counter()
    for path in paths:
        resolved = path.resolve()
        key = str(resolved).casefold()
        seen.setdefault(key, resolved)
        counts[key] += 1
    return sorted(seen[key] for key, count in counts.items() if count > 1)


def _blocked(paths: list[Path], reason: str) -> str:
    return f"{reason}: {', '.join(str(p) for p in paths)}"


def _write_issues(issues: IssueLog, path: Path | None) -> None:
    """Issues as a YAML sequence, always written even when empty.

    A caller scripting against this output relies on the shape, not on whether
    anything was said. YAML rather than JSON because a message runs to a
    paragraph, and folded YAML wraps it instead of putting it on one long line.
    """
    text = _yaml.dump_issues(issues.as_dicts())
    if path is None:
        print(text, end="", file=sys.stderr)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _guard(targets: list[Path], issues: Path | None, force: bool) -> str | None:
    collisions = _colliding(targets)
    if collisions:
        issue_path = str(issues.resolve()).casefold() if issues else None
        if issue_path and any(str(p).casefold() == issue_path for p in collisions):
            reason = (
                "this run would write the same path twice. --issues must name a "
                "different file from the converted output"
            )
        else:
            reason = (
                "this run would write the same path twice. Two datasets produce "
                "this filename, and the filesystem may not tell them apart by case. "
                "Rename one of them in Ossie"
            )
        return _blocked(collisions, reason)
    if not force:
        existing = [p for p in targets if p.exists()]
        if existing:
            return _blocked(existing, "file(s) already exist; pass --force to overwrite")
    return None


def _cmd_to_ossie(args: argparse.Namespace) -> int:
    output = Path(args.output)
    issues_path = Path(args.issues) if args.issues else None
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = aml_to_ossie.convert(payload, sql_dialect=args.sql_dialect)
    except (ConversionError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    blocked = _guard(
        [output] + ([issues_path] if issues_path else []), issues_path, args.force
    )
    if blocked:
        print(f"Error: {blocked}", file=sys.stderr)
        return 1

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_yaml.dump(result.model), encoding="utf-8")
        _write_issues(result.issues, issues_path)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 1 if result.issues.has_errors() else 0


def _cmd_to_aml(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    issues_path = Path(args.issues) if args.issues else None
    if output_dir.exists() and not output_dir.is_dir():
        print(f"Error: output path {output_dir} exists and is not a directory", file=sys.stderr)
        return 1

    try:
        document = _yaml.load(Path(args.input).read_text(encoding="utf-8"))
        result = ossie_to_aml.convert(
            document, args.sql_dialect, data_source_name=args.data_source_name
        )
        targets = [(_safe_target(output_dir, name), text) for name, text in result.files]
    except (ConversionError, OSError, UnicodeDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    paths = [path for path, _ in targets] + ([issues_path] if issues_path else [])
    blocked = _guard(paths, issues_path, args.force)
    if blocked:
        print(f"Error: {blocked}", file=sys.stderr)
        return 1

    try:
        for path, text in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _write_issues(result.issues, issues_path)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 1 if result.issues.has_errors() else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "to-ossie":
        return _cmd_to_ossie(args)
    return _cmd_to_aml(args)


if __name__ == "__main__":
    sys.exit(main())
