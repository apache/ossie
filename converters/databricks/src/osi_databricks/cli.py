"""Command-line interface for the OSI <-> Databricks Metric View converter.

    osi-databricks export -i model.yaml [-o view.yaml] [--source orders]
    osi-databricks import -i view.yaml  [-o model.yaml] [--name my_model]

`export` converts an OSI semantic model to a Databricks Metric View; `import` does the
reverse. With no `-o`, the result is written to stdout. Conversions that drop
information emit warnings to stderr.
"""

import argparse
import sys

from ._common import ConversionError
from .metric_view_to_osi import convert_metric_view_to_osi
from .osi_to_metric_view import convert_osi_to_metric_view


def _build_parser():
    parser = argparse.ArgumentParser(prog="osi-databricks", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.required = True  # set as attribute (the add_subparsers kwarg is 3.7+)

    exp = sub.add_parser("export", help="OSI semantic model -> Databricks Metric View YAML")
    exp.add_argument("-i", "--input", required=True, help="OSI YAML file")
    exp.add_argument("-o", "--output", help="output Metric View YAML (default: stdout)")
    exp.add_argument("-s", "--source",
                     help="dataset to use as the fact/grain (default: the FK-sink dataset); "
                          "naming a coarser-grain dataset unlocks one_to_many joins")

    imp = sub.add_parser("import", help="Databricks Metric View YAML -> OSI semantic model")
    imp.add_argument("-i", "--input", required=True, help="Metric View YAML file")
    imp.add_argument("-o", "--output", help="output OSI YAML (default: stdout)")
    imp.add_argument("--name", help="OSI model name (default: derived from the source)")
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
