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

"""The ``ost`` command. See ``docs/ost.md``."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from ossie_ost.parser import ScriptError
from ossie_ost.runner import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ost",
        description="Run Ossie SQL Test (.ost) scripts. Each command's output "
        "is compared with its expected output, and the regenerated script "
        "is written to the output directory. For each script that fails, "
        "prints a diff between the script and the regenerated script.",
        epilog="Exit status is 0 if every script passed, 1 if at least one "
        "failed, 2 for a usage error or a malformed script.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="file",
        help="a script, a directory (runs every .ost file under it), "
        "or '-' to read a script from standard input",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="overwrite each script that fails with the regenerated script",
    )
    parser.add_argument(
        "--output-dir",
        metavar="dir",
        default="target/ost",
        help="directory for regenerated scripts (default: %(default)s)",
    )
    parser.add_argument(
        "--echo",
        action="store_true",
        help="write the regenerated script to standard output as each "
        "command completes",
    )
    parser.add_argument(
        "--timeout",
        metavar="seconds",
        type=float,
        default=60,
        help="interrupt any command that runs longer than this (default: %(default)g)",
    )
    return parser


def main() -> None:
    sys.exit(run_main(sys.argv[1:]))


def run_main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    status = 0
    for name in args.files:
        if name == "-":
            status = max(status, run_stdin(args))
            continue
        path = Path(name)
        if path.is_dir():
            scripts = sorted(
                p
                for p in path.rglob("*.ost")
                if p.is_file() and not _is_under(p, output_dir)
            )
        elif path.is_file():
            scripts = [path]
        else:
            print(f"ost: {name}: no such file or directory", file=sys.stderr)
            status = 2
            continue
        for script in scripts:
            status = max(status, run_file(script, output_dir, args))
    return status


def run_file(path: Path, output_dir: Path, args: argparse.Namespace) -> int:
    text = path.read_bytes().decode("utf-8")
    try:
        result = run(text, str(path), timeout=args.timeout, echo=_echo(args))
    except ScriptError as e:
        print(e, file=sys.stderr)
        return 2
    out = output_dir / _relative(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.output.encode("utf-8"))
    if result.passed:
        return 0
    print(f"ost: {path} failed; regenerated script is {out}")
    sys.stdout.writelines(_diff(text, result.output, str(path), str(out)))
    if args.update:
        path.write_bytes(result.output.encode("utf-8"))
        print(f"ost: updated {path}")
    return 1


def run_stdin(args: argparse.Namespace) -> int:
    text = sys.stdin.read()
    try:
        result = run(text, "-", timeout=args.timeout, echo=_echo(args))
    except ScriptError as e:
        print(e, file=sys.stderr)
        return 2
    if not args.echo:
        sys.stdout.write(result.output)
    if result.passed:
        return 0
    sys.stderr.writelines(_diff(text, result.output, "-", "-"))
    return 1


def _echo(args: argparse.Namespace):
    if not args.echo:
        return None

    def echo(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    return echo


def _diff(a: str, b: str, a_name: str, b_name: str) -> list[str]:
    lines = difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True), a_name, b_name
    )
    return [line if line.endswith("\n") else line + "\n" for line in lines]


def _relative(path: Path) -> Path:
    """Returns the path of a script relative to the current directory,
    or its file name if it is outside the current directory."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return Path(path.name)


def _is_under(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory.resolve())
