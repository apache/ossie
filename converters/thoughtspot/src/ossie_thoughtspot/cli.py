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

"""Command-line interface for the Apache Ossie <-> ThoughtSpot TML converter.

    ossie-thoughtspot to-ossie   <tml-file>... -o <out.yaml>  [--issues <issues.json>]
    ossie-thoughtspot to-tml     <ossie.yaml>  -o <out-dir>   [--issues <issues.json>]

`to-ossie` reads a Model TML document plus the Table/SQL View documents it references
and writes one Apache Ossie semantic model. `to-tml` reads one Apache Ossie semantic
model and writes the corresponding TML document set -- one file per document, tables
before the model -- into an output directory.

`-o`/`--output` is required in both directions: `to-ossie` writes exactly one file, and
`to-tml` writes a set of files that has no single-file stdout representation, so unlike
some sibling converters there is no "default: stdout" fallback here.

Every declared loss or degradation the conversion records is written as a JSON array of
issues -- to `--issues` when given, to stderr otherwise -- and never mixed into the
document output. The process exits 1 when that issue log contains an ERROR-severity
issue, 0 otherwise: a conversion that only warned or informed about a declared loss is
still a successful conversion, and failing the exit code on a warning would just teach
scripts to ignore it.

Neither subcommand overwrites an existing output file unless `--force` is given; without
it, a target that already exists refuses the run before anything is written.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import _yaml, tml, tml_to_ossie, ossie_to_thoughtspot
from .errors import ConversionError
from .issues import IssueLog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ossie-thoughtspot",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True  # set as attribute (the add_subparsers kwarg is 3.7+)

    to_ossie = sub.add_parser(
        "to-ossie", help="ThoughtSpot TML documents -> one Apache Ossie semantic model"
    )
    to_ossie.add_argument(
        "tml_files",
        nargs="+",
        metavar="TML_FILE",
        help="the Model TML document plus every Table/SQL View document it references",
    )
    to_ossie.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="output Apache Ossie YAML file"
    )
    to_ossie.add_argument(
        "--issues",
        metavar="FILE",
        help="write the issue log as JSON to this path (default: stderr)",
    )
    to_ossie.add_argument(
        "--force",
        action="store_true",
        help="overwrite the output file (and --issues file) if either already exists; "
        "without this flag, an existing target refuses the run before writing anything",
    )

    to_tml = sub.add_parser(
        "to-tml", help="one Apache Ossie semantic model -> ThoughtSpot TML documents"
    )
    to_tml.add_argument("ossie_file", metavar="OSSIE_FILE", help="Apache Ossie YAML document")
    to_tml.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="DIR",
        help="output directory; one TML file per document is written here (tables before "
        "the model), created if it does not already exist",
    )
    to_tml.add_argument(
        "--issues",
        metavar="FILE",
        help="write the issue log as JSON to this path (default: stderr)",
    )
    to_tml.add_argument(
        "--force",
        action="store_true",
        help="overwrite output files (and --issues file) if any already exist; without "
        "this flag, an existing target refuses the run before writing anything",
    )
    return parser


def _safe_target_path(directory: Path, filename: str) -> Path:
    """`directory / filename`, refusing to resolve outside `directory`.

    `tml.dump_document_set` already sanitises `filename` (a document's `name` is
    user-controlled TML content, and a hostile one -- `../../etc/passwd` -- previously
    produced a path that escaped its output directory), so this should never trigger in
    practice. It exists anyway because this module is the first caller that actually
    writes these documents to disk, and a defence that lives only in the dumper is not
    one this module can verify it still has. Both paths are resolved before comparing --
    a naive string-prefix check is wrong on a filesystem with symlinks in play, e.g.
    macOS where `/tmp` is a symlink to `/private/tmp`.
    """
    resolved_directory = directory.resolve()
    target = (resolved_directory / filename).resolve()
    if target != resolved_directory and resolved_directory not in target.parents:
        raise ConversionError(
            f"refusing to write {filename!r}: it resolves outside the output directory "
            f"{resolved_directory}"
        )
    return target


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def _refuse_overwrite(paths: list[Path]) -> str:
    names = ", ".join(str(p) for p in paths)
    return f"refusing to overwrite existing file(s): {names} (pass --force to overwrite)"


def _write_issues(issues: IssueLog, issues_path: Path | None) -> None:
    """Issues as a JSON array -- to `issues_path` when given, to stderr otherwise.

    Always written, even when empty: a caller scripting against this output should be
    able to rely on the shape (a JSON array) rather than on whether anything was said.
    """
    text = json.dumps(issues.as_dicts(), indent=2)
    if issues_path is not None:
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        issues_path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text, file=sys.stderr)


def _cmd_to_ossie(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    issues_path = Path(args.issues) if args.issues else None

    try:
        texts = [(path, Path(path).read_text(encoding="utf-8")) for path in args.tml_files]
        document_set = tml.load_document_set(texts)
        result = tml_to_ossie.convert(document_set)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    targets = [output_path] + ([issues_path] if issues_path else [])
    if not args.force:
        existing = _existing(targets)
        if existing:
            print(f"Error: {_refuse_overwrite(existing)}", file=sys.stderr)
            return 1

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_yaml.dump(result.model), encoding="utf-8")
        _write_issues(result.issues, issues_path)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 1 if result.issues.has_errors() else 0


def _cmd_to_tml(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    issues_path = Path(args.issues) if args.issues else None

    if output_dir.exists() and not output_dir.is_dir():
        print(f"Error: output path {output_dir} exists and is not a directory", file=sys.stderr)
        return 1

    try:
        text = Path(args.ossie_file).read_text(encoding="utf-8")
        ossie_document = _yaml.load(text)
        if not isinstance(ossie_document, dict):
            raise ConversionError(f"{args.ossie_file} is not an Ossie document: expected a mapping")
        result = ossie_to_thoughtspot.convert(ossie_document)
        files = tml.dump_document_set(result.documents)
        # Path safety belongs here, before any write: a document name is user-controlled
        # TML/Ossie content, and `dump_document_set` sanitises filenames for exactly this
        # reason (see `_safe_target_path`).
        targets = [(_safe_target_path(output_dir, name), text_) for name, text_ in files]
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    all_targets = [path for path, _ in targets] + ([issues_path] if issues_path else [])
    if not args.force:
        existing = _existing(all_targets)
        if existing:
            print(f"Error: {_refuse_overwrite(existing)}", file=sys.stderr)
            return 1

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for path, text_ in targets:
            path.write_text(text_, encoding="utf-8")
        _write_issues(result.issues, issues_path)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 1 if result.issues.has_errors() else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "to-ossie":
        return _cmd_to_ossie(args)
    return _cmd_to_tml(args)


if __name__ == "__main__":
    sys.exit(main())
