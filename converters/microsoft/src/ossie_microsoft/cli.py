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

"""Command-line interface for the Microsoft Power BI <-> Apache Ossie converter.

    ossie-microsoft import -i model.bim   [-o model.yaml]
    ossie-microsoft export -i model.yaml  [-o model.bim]

With no ``-o``, the result is written to stdout.

Every construct the converter cannot carry across faithfully is reported on stderr, so
the log is a complete account of what a conversion changed, skipped or could not
represent. ``--strict`` turns any such report into a non-zero exit, which is the useful
mode in a pipeline that must not silently degrade a model.
"""

import argparse
import json
import logging
import sys
import warnings

import yaml

from ._common import LOGGER, ConversionError
from .ossie_to_semantic_model import convert_ossie_to_semantic_model
from .semantic_model_to_ossie import convert_semantic_model_to_ossie


class _CountingHandler(logging.Handler):
    """Counts reported conversion issues so the CLI can summarize and exit on them."""

    def __init__(self, quiet):
        super().__init__()
        self.count = 0
        self.quiet = quiet

    def emit(self, record):
        self.count += 1
        if not self.quiet:
            print(record.getMessage(), file=sys.stderr)


def _add_common_flags(parser, suppress):
    """Add the flags shared by the root parser and every subcommand.

    `suppress` is used for the subcommand copies: without it argparse would write the
    subparser's own default over a value the root parser already parsed, so
    `--strict` before the subcommand would be silently ignored.
    """
    default = argparse.SUPPRESS if suppress else False
    parser.add_argument(
        "--strict",
        action="store_true",
        default=default,
        help="exit non-zero if anything could not be converted faithfully",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=default,
        help="suppress the report of constructs that could not be converted",
    )


def _build_parser():
    # The flags are accepted both before and after the subcommand, because that is where
    # people actually type them.
    sub_flags = argparse.ArgumentParser(add_help=False)
    _add_common_flags(sub_flags, suppress=True)

    parser = argparse.ArgumentParser(
        prog="ossie-microsoft",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_flags(parser, suppress=False)
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    imp = sub.add_parser(
        "import",
        parents=[sub_flags],
        help="Power BI semantic model (TMSL model.bim) -> Apache Ossie semantic model",
    )
    imp.add_argument("-i", "--input", required=True, help="model.bim (TMSL JSON) file")
    imp.add_argument("-o", "--output", help="output Apache Ossie YAML (default: stdout)")

    exp = sub.add_parser(
        "export",
        parents=[sub_flags],
        help="Apache Ossie semantic model -> Power BI semantic model (TMSL model.bim)",
    )
    exp.add_argument("-i", "--input", required=True, help="Apache Ossie YAML file")
    exp.add_argument("-o", "--output", help="output model.bim (default: stdout)")
    return parser


def _run_import(args):
    with open(args.input, encoding="utf-8-sig") as fh:
        return convert_semantic_model_to_ossie(json.load(fh))


def _run_export(args):
    with open(args.input, encoding="utf-8-sig") as fh:
        ossie_yaml = fh.read()
    # Power BI writes model.bim as UTF-8 JSON with non-ASCII characters left as-is.
    return json.dumps(
        convert_ossie_to_semantic_model(ossie_yaml), indent=2, ensure_ascii=False
    ) + "\n"


def main(argv=None):
    args = _build_parser().parse_args(argv)
    handler = _run_import if args.command == "import" else _run_export

    # Each lossy step is reported through both `warnings` and the logger. For a command
    # line the log is the better channel -- it is ordered with respect to the rest of the
    # output and carries no file/line noise -- so the warning channel is silenced here to
    # stop every message being printed twice.
    counter = _CountingHandler(args.quiet)
    LOGGER.addHandler(counter)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out = handler(args)
    except (ConversionError, TypeError, ValueError, OSError, yaml.YAMLError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        LOGGER.removeHandler(counter)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)

    if counter.count and not args.quiet:
        print(
            f"{counter.count} construct(s) could not be converted faithfully; "
            "see the messages above.",
            file=sys.stderr,
        )
    return 1 if args.strict and counter.count else 0


if __name__ == "__main__":
    sys.exit(main())
