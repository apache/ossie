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

"""Command-line interface for the Apache Ossie <-> Hex converter.

    ossie-hex export -i model.yaml [-o hex_project/] [--dialect DIALECT] [-v]

``export`` converts Apache Ossie semantic model(s) to a Hex project directory(s).
If ``-o`` is omitted, files are written to the current working directory.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, NoReturn

from ossie import OssieDialect

from ..ossie_to_hex import convert_ossie_to_hex
from .report import format_export_report

# Ossie spells its dialects in upper case, but it's a bit nicer to show/take them
# in lowercase. Parsing will transform them back.
_OSSIE_DIALECTS = [d.value for d in OssieDialect]
_OSSIE_DIALECT_CHOICES = [d.lower() for d in _OSSIE_DIALECTS]
_OSSIE_DIALECT_LIST = ", ".join(_OSSIE_DIALECT_CHOICES)


def _build_parser() -> argparse.ArgumentParser:
    parser = _CustomArgumentParser(
        prog="ossie-hex",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    export = sub.add_parser(
        "export",
        help="Apache Ossie semantic model -> Hex project directory",
        dialect_list=_OSSIE_DIALECT_LIST,
    )
    export.add_argument(
        "-i",
        "--input",
        required=True,
        help="Apache Ossie YAML file",
    )
    export.add_argument(
        "-o",
        "--output",
        default=".",
        help="output directory for the Hex project files (default: current directory)",
    )
    export.add_argument(
        "-d",
        "--dialect",
        type=str.lower,
        choices=_OSSIE_DIALECT_CHOICES,
        metavar="DIALECT",
        help=f"Ossie expression dialect, one of: {_OSSIE_DIALECT_LIST}",
        default=OssieDialect.ANSI_SQL.value.lower(),
    )
    export.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show grouped problems; repeat (-vv) for phase and cause details",
    )

    return parser


class _CustomArgumentParser(argparse.ArgumentParser):
    def __init__(
        self,
        *args: Any,
        dialect_list: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.dialect_list = dialect_list

    def error(self, message: str) -> NoReturn:
        # Name the accepted dialects in every ``--dialect`` error. The usage line
        # abbreviates the option as ``DIALECT`` to stay readable, so a missing or
        # misspelled dialect would otherwise leave nothing on screen to infer the valid
        # values from.
        if (
            "--dialect" in message
            and "choose from" not in message
            and self.dialect_list is not None
        ):
            message = f"{message} (choose from {self.dialect_list})"
        super().error(message)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        if args.command == "export":
            hex_projects, problems = convert_ossie_to_hex(
                input=args.input,
                output=args.output,
                dialect=args.dialect,
            )
            report = format_export_report(
                input=args.input,
                output=args.output,
                projects=hex_projects,
                problems=problems,
                verbosity=args.verbose,
            )
            print(report, file=sys.stderr)
            if any(problem.severity == "fatal" for problem in problems):
                return 1
            return 0
        else:
            raise ValueError(f"Unknown command: {args.command}")
    except Exception as e:  # noqa: BLE001
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1
    return 0
