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

"""Command-line interface for the Apache Ossie <-> Databricks Metric View converter.

    ossie-databricks export -i model.yaml [-o view.yaml] [--source orders]
    ossie-databricks import -i view.yaml  [-o model.yaml] [--name my_model]

`export` converts an Apache Ossie semantic model to a Databricks Metric View; `import` does the
reverse. With no `-o`, the result is written to stdout. Conversions that drop
information emit warnings to stderr.
"""

import argparse
import sys

from ._common import ConversionError
from .metric_view_to_osi import convert_metric_view_to_osi
from .osi_to_metric_view import convert_osi_to_metric_view


def _build_parser():
    parser = argparse.ArgumentParser(prog="ossie-databricks", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.required = True  # set as attribute (the add_subparsers kwarg is 3.7+)

    exp = sub.add_parser("export", help="Apache Ossie semantic model -> Databricks Metric View YAML")
    exp.add_argument("-i", "--input", required=True, help="Apache Ossie YAML file")
    exp.add_argument("-o", "--output", help="output Metric View YAML (default: stdout)")
    exp.add_argument("-s", "--source",
                     help="dataset to use as the fact/grain (default: the FK-sink dataset); "
                          "naming a coarser-grain dataset unlocks one_to_many joins")

    imp = sub.add_parser("import", help="Databricks Metric View YAML -> Apache Ossie semantic model")
    imp.add_argument("-i", "--input", required=True, help="Metric View YAML file")
    imp.add_argument("-o", "--output", help="output Apache Ossie YAML (default: stdout)")
    imp.add_argument("--name", help="Apache Ossie model name (default: derived from the source)")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        with open(args.input) as fh:
            text = fh.read()
        if args.command == "export":
            out = convert_osi_to_metric_view(text, source=args.source)
        else:
            out = convert_metric_view_to_osi(text, model_name=args.name)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
