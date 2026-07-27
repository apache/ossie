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

    ossie-hex export -i model.yaml -o hex_project/ [--model NAME] [--dialect DIALECT]
    ossie-hex import -i hex_project/ [-o model.yaml] --dialect DIALECT [--name NAME]

``export`` converts an Apache Ossie semantic model to a Hex project directory;
``import`` does the reverse. With no ``-o`` on import, the result is written to
stdout. Conversions that drop information emit warnings to stderr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, NoReturn

from ._common import ConversionError
from .hex_models import HEX_DIALECTS
from .hex_project import write_hex_project
from .hex_to_ossie import convert_hex_to_ossie
from .ossie_models import OSI_DIALECTS
from .ossie_to_hex import convert_ossie_to_hex

_OSI_DIALECT_LIST = ", ".join(OSI_DIALECTS)
_HEX_DIALECT_LIST = ", ".join(HEX_DIALECTS)


def _build_parser() -> argparse.ArgumentParser:
    parser = _CustomArgumentParser(
        prog="ossie-hex",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    exp = sub.add_parser(
        "export",
        help="Apache Ossie semantic model -> Hex project directory",
        dialect_list=_OSI_DIALECT_LIST,
    )
    exp.add_argument("-i", "--input", required=True, help="Apache Ossie YAML file")
    exp.add_argument(
        "-o",
        "--output",
        required=True,
        help="output directory for the Hex project files",
    )
    exp.add_argument(
        "--model",
        help="Ossie semantic model name when the document contains several",
    )
    exp.add_argument(
        "--base-model",
        help="Hex model to attach metrics that cannot be assigned unambiguously",
    )
    exp.add_argument(
        "-d",
        "--dialect",
        type=str.upper,
        choices=OSI_DIALECTS,
        metavar="DIALECT",
        help=(
            f"OSI expression dialect, one of: {_OSI_DIALECT_LIST} "
            "(default: restored from the HEX custom extension or inferred)"
        ),
    )

    imp = sub.add_parser(
        "import",
        help="Hex project directory -> Apache Ossie semantic model",
        dialect_list=_HEX_DIALECT_LIST,
    )
    imp.add_argument("-i", "--input", required=True, help="Hex project directory")
    imp.add_argument(
        "-o",
        "--output",
        help="output Apache Ossie YAML file (default: stdout)",
    )
    imp.add_argument(
        "-d",
        "--dialect",
        required=True,
        choices=HEX_DIALECTS,
        metavar="DIALECT",
        help=f"Hex project warehouse dialect, one of: {_HEX_DIALECT_LIST}",
    )
    imp.add_argument(
        "--name",
        help="Apache Ossie model name (default: Hex project directory name)",
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
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "export":
            text = Path(args.input).read_text(encoding="utf-8")
            files, warnings = convert_ossie_to_hex(
                text,
                model_name=args.model,
                dialect=args.dialect,
                base_model=args.base_model,
            )
            for warning in warnings:
                print(f"Warning: {warning}", file=sys.stderr)
            write_hex_project(args.output, files)
            print(f"Wrote {len(files)} file(s) to {args.output}", file=sys.stderr)
        else:
            out, warnings = convert_hex_to_ossie(
                args.input,
                dialect=args.dialect,
                model_name=args.name,
            )
            for warning in warnings:
                print(f"Warning: {warning}", file=sys.stderr)
            if args.output:
                Path(args.output).write_text(out, encoding="utf-8")
            else:
                sys.stdout.write(out)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
