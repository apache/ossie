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

"""Command-line interface for the Apache Ossie <-> Solid converter.

    ossie-solid import -i solid_model.yaml [-o model.yaml] [--dialect SNOWFLAKE]
    ossie-solid export -i model.yaml [-o solid_model.yaml] [--dialect SNOWFLAKE]

`import` converts a Solid semantic model export into an Apache Ossie semantic model;
`export` does the reverse. With no `-o` the result goes to stdout. Conversions that drop
or approximate information emit warnings to stderr.
"""

import argparse
import sys
import warnings

from ._common import SUPPORTED_DIALECTS, ConversionError
from .ossie_to_solid import convert_ossie_to_solid
from .solid_to_ossie import convert_solid_to_ossie


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-solid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    imp = sub.add_parser(
        "import", help="Solid semantic model YAML -> Apache Ossie semantic model")
    imp.add_argument("-i", "--input", required=True, help="Solid YAML file")
    imp.add_argument("-o", "--output", help="output Apache Ossie YAML (default: stdout)")
    imp.add_argument(
        "-d", "--dialect", choices=SUPPORTED_DIALECTS,
        help="expression dialect to label expressions with (default: inferred from the "
             "column type vocabulary, falling back to ANSI_SQL)")
    imp.add_argument(
        "--name", help="Apache Ossie model name (default: the Solid model's name)")

    exp = sub.add_parser(
        "export", help="Apache Ossie semantic model -> Solid semantic model YAML")
    exp.add_argument("-i", "--input", required=True, help="Apache Ossie YAML file")
    exp.add_argument("-o", "--output", help="output Solid YAML (default: stdout)")
    exp.add_argument(
        "-d", "--dialect", choices=SUPPORTED_DIALECTS,
        help="expression dialect to read (default: the dialect recorded at import, "
             "else the one the model's expressions use)")
    exp.add_argument("--name", help="Solid model name (default: the Apache Ossie "
                                    "model's name)")
    return parser


def _show_warning(message, category, filename, lineno, file=None, line=None):
    """Render conversion warnings as plain stderr lines rather than Python tracebacks."""
    print(f"Warning: {message}", file=sys.stderr)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    convert = convert_solid_to_ossie if args.command == "import" else convert_ossie_to_solid
    try:
        with open(args.input) as handle:
            source = handle.read()
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.showwarning = _show_warning
            output = convert(source, dialect=args.dialect, model_name=args.name)
        if args.output:
            with open(args.output, "w") as handle:
                handle.write(output)
        else:
            sys.stdout.write(output)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
