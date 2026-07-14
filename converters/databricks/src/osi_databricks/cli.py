"""CLI entry point for the osi-databricks converter.

Usage:
    osi-databricks import -i metric_view.yaml -o output_osi.yaml
    osi-databricks export -i osi_model.yaml -o ./output_dir/
"""

import argparse
import sys
from pathlib import Path

import yaml
from osi import OSIDocument

from osi_databricks.metric_view_to_osi import metric_view_to_osi
from osi_databricks.models import MetricViewModel
from osi_databricks.osi_to_metric_view import osi_to_metric_view


def _cmd_import(args: argparse.Namespace) -> None:
    """Import: Metric View YAML → OSI YAML."""
    input_path = Path(args.input)
    output_path = Path(args.output)

    try:
        mv_model = MetricViewModel.from_yaml(input_path.read_text())
    except Exception as e:
        print(f"Error parsing {input_path}: {e}", file=sys.stderr)
        sys.exit(1)

    osi_doc = metric_view_to_osi(mv_model, model_name=args.model_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(osi_doc.to_osi_yaml())
    print(f"Written to {output_path}", file=sys.stderr)


def _cmd_export(args: argparse.Namespace) -> None:
    """Export: OSI YAML → Metric View YAML file(s)."""
    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        raw = yaml.safe_load(input_path.read_text())
        document = OSIDocument.model_validate(raw)
    except Exception as e:
        print(f"Error parsing {input_path}: {e}", file=sys.stderr)
        sys.exit(1)

    results = osi_to_metric_view(document)
    for dataset_name, mv_model in results:
        out_file = output_dir / f"{dataset_name}.yaml"
        out_file.write_text(mv_model.to_yaml())
        print(f"Written {out_file}", file=sys.stderr)


def main() -> None:
    """Entry point for the osi-databricks CLI."""
    parser = argparse.ArgumentParser(
        prog="osi-databricks",
        description="Convert between Databricks Metric View YAML and OSI YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Import subcommand
    import_cmd = subparsers.add_parser("import", help="Convert Metric View YAML → OSI YAML")
    import_cmd.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to Metric View YAML")
    import_cmd.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output OSI YAML")
    import_cmd.add_argument(
        "--model-name", default="metric_view_model", metavar="NAME",
        help="OSI semantic model name (default: metric_view_model)",
    )

    # Export subcommand
    export_cmd = subparsers.add_parser("export", help="Convert OSI YAML → Metric View YAML")
    export_cmd.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to OSI YAML")
    export_cmd.add_argument("-o", "--output", required=True, metavar="DIR", help="Output directory for Metric View files")

    args = parser.parse_args()
    if args.command == "import":
        _cmd_import(args)
    elif args.command == "export":
        _cmd_export(args)


if __name__ == "__main__":
    main()
