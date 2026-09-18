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

"""Command-line interface for the Apache Ossie <-> Hologres Semantic View converter.

    ossie-hologres export -i model.yaml [-o view.sql] [--schema public] [--drop-if-exists]
    ossie-hologres import -i model_yaml.yaml [-o model.yaml] [--name my_model]

`export` converts an Apache Ossie semantic model into `CREATE SEMANTIC VIEW` DDL, ready
to execute against Hologres V5.0.0 or later. `import` converts the `model_yaml` that
Hologres publishes in `hologres.hg_semantic_view_properties` back into Apache Ossie.

With no `-o`, the result is written to stdout. Conversions that drop information emit
warnings to stderr.
"""

import argparse
import sys

from ._common import ConversionError
from .ossie_to_semantic_view import convert_ossie_to_semantic_view
from .semantic_view_to_ossie import convert_semantic_view_to_ossie


def _metric_owner(value):
    """Parse a `--metric-owner metric=dataset` pair."""
    name, sep, dataset = value.partition("=")
    if not sep or not name.strip() or not dataset.strip():
        raise argparse.ArgumentTypeError(
            f"expected 'metric=dataset', got {value!r}"
        )
    return name.strip(), dataset.strip()


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-hologres",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True  # set as attribute (the add_subparsers kwarg is 3.7+)

    exp = sub.add_parser("export", help="Apache Ossie semantic model -> Hologres DDL")
    exp.add_argument("-i", "--input", required=True, help="Apache Ossie YAML file")
    exp.add_argument("-o", "--output", help="output .sql file (default: stdout)")
    exp.add_argument(
        "-s",
        "--schema",
        help="schema for the view and for datasets whose source has none; "
        "never overrides a schema written into a dataset source",
    )
    exp.add_argument(
        "-d",
        "--database",
        help="assert the database the dataset sources belong to",
    )
    exp.add_argument(
        "--drop-if-exists",
        action="store_true",
        help="prefix a DROP SEMANTIC VIEW IF EXISTS; Hologres has no CREATE OR REPLACE "
        "or ALTER, so this is how a definition is changed",
    )
    exp.add_argument(
        "--metric-owner",
        action="append",
        type=_metric_owner,
        metavar="METRIC=DATASET",
        default=[],
        help="name the table a metric belongs to, for metrics whose expression has no "
        "qualified column to infer it from (such as COUNT(*)); repeatable",
    )
    exp.add_argument(
        "--skip-unsupported-metrics",
        action="store_true",
        help="warn about and skip metrics with no Semantic View form (derived and ratio "
        "metrics) instead of failing",
    )

    imp = sub.add_parser("import", help="Hologres Semantic View model_yaml -> Apache Ossie")
    imp.add_argument("-i", "--input", required=True, help="Hologres model_yaml file")
    imp.add_argument("-o", "--output", help="output Apache Ossie YAML (default: stdout)")
    imp.add_argument("--name", help="Apache Ossie model name (default: the view name)")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        with open(args.input, encoding="utf-8") as fh:
            text = fh.read()
        if args.command == "export":
            out = convert_ossie_to_semantic_view(
                text,
                schema=args.schema,
                database=args.database,
                drop_if_exists=args.drop_if_exists,
                metric_owners=dict(args.metric_owner),
                skip_unsupported_metrics=args.skip_unsupported_metrics,
            )
        else:
            out = convert_semantic_view_to_ossie(text, model_name=args.name)
    except (ConversionError, OSError, UnicodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
