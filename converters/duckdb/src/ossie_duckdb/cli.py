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

"""Command-line interface for the DuckDB <-> Ossie converter.

    ossie-duckdb export -i model.yaml [-o out.sql] [--view-schema semantic]
    ossie-duckdb import -i analytics.duckdb [-o model.yaml] [--schema main] [--name my_model]

`export` converts an Ossie semantic model into a DuckDB SQL script of views;
`import` reads a DuckDB database (file path or connection string, including
MotherDuck `md:` URIs) and writes the corresponding Ossie YAML. With no `-o`,
output goes to stdout.
"""

import argparse
import sys

from ossie_duckdb._common import ConversionError
from ossie_duckdb.duckdb_to_osi import convert_duckdb_to_osi_yaml
from ossie_duckdb.osi_to_duckdb import convert_osi_to_duckdb


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-duckdb",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    exp = sub.add_parser("export", help="Ossie semantic model -> DuckDB SQL script")
    exp.add_argument("-i", "--input", required=True, help="Ossie YAML file")
    exp.add_argument("-o", "--output", help="output SQL file (default: stdout)")
    exp.add_argument("--view-schema", help="create the views inside this schema")

    imp = sub.add_parser("import", help="DuckDB database -> Ossie semantic model YAML")
    imp.add_argument("-i", "--input", required=True, help="DuckDB database path or connection string")
    imp.add_argument("-o", "--output", help="output Ossie YAML file (default: stdout)")
    imp.add_argument("--schema", default="main", help="database schema to import (default: main)")
    imp.add_argument("--name", help="Ossie model name (default: <database>_<schema>)")
    return parser


def _write(text: str, output: str | None) -> None:
    if output:
        with open(output, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "export":
            with open(args.input) as fh:
                osi_yaml = fh.read()
            _write(convert_osi_to_duckdb(osi_yaml, view_schema=args.view_schema), args.output)
        else:
            _write(convert_duckdb_to_osi_yaml(args.input, schema=args.schema, model_name=args.name), args.output)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
