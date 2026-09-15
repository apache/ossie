"""CLI entry point for the ossie-sigma converter.

Usage:
    ossie-sigma sigma-to-ossie -i data_model.json -o semantic_model.yaml
    ossie-sigma ossie-to-sigma -i semantic_model.yaml -o data_model.json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from ossie import OssieDocument
from ossie_sigma.ossie_to_sigma import OssieToSigmaConverter
from ossie_sigma.sigma_to_ossie import SigmaToOssieConverter


def _cmd_sigma_to_ossie(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    spec = json.loads(input_path.read_text())
    result = SigmaToOssieConverter().convert(spec)

    for issue in result.issues:
        print(f"[WARNING] {issue.issue_type.value}: {issue.element_name} — {issue.detail}", file=sys.stderr)

    output_path.write_text(result.output.to_ossie_yaml())
    print(f"Written to {output_path}", file=sys.stderr)


def _cmd_ossie_to_sigma(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    raw = yaml.safe_load(input_path.read_text())
    document = OssieDocument.model_validate(raw)
    result = OssieToSigmaConverter().convert(document)

    for issue in result.issues:
        print(f"[WARNING] {issue.issue_type.value}: {issue.element_name} — {issue.detail}", file=sys.stderr)

    output_path.write_text(json.dumps(result.output, indent=2))
    print(f"Written to {output_path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ossie-sigma",
        description="Convert between Sigma data model specs and Ossie YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sigma_to_ossie = subparsers.add_parser("sigma-to-ossie", help="Convert Sigma data model spec JSON → Ossie YAML")
    sigma_to_ossie.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to Sigma data model spec JSON")
    sigma_to_ossie.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output Ossie YAML")

    ossie_to_sigma = subparsers.add_parser("ossie-to-sigma", help="Convert Ossie YAML → Sigma data model spec JSON")
    ossie_to_sigma.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to Ossie YAML")
    ossie_to_sigma.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output Sigma data model spec JSON")

    args = parser.parse_args()
    if args.command == "sigma-to-ossie":
        _cmd_sigma_to_ossie(args)
    elif args.command == "ossie-to-sigma":
        _cmd_ossie_to_sigma(args)


if __name__ == "__main__":
    main()
