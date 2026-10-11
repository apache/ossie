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

"""CLI entry point for the ossie-sap-csn converter.

Usage:
    ossie-sap-csn csn-to-ossie -i model.csn.json -o semantic_model.yaml [--name NAME]
    ossie-sap-csn ossie-to-csn -i semantic_model.yaml -o model.csn.json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from ossie import OssieDocument
from ossie_sap_csn.converter_issues import ConverterError
from ossie_sap_csn.csn_to_ossie import CsnToOssieConverter
from ossie_sap_csn.ossie_to_csn import OssieToCsnConverter


def _report(issues: list) -> None:
    for issue in issues:
        print(f"[WARNING] {issue.issue_type.value}: {issue.element_name} — {issue.detail}", file=sys.stderr)


def _cmd_csn_to_ossie(args: argparse.Namespace) -> None:
    try:
        csn = json.loads(Path(args.input).read_text())
    except json.JSONDecodeError as exc:
        raise ConverterError(f"{args.input} is not valid JSON: {exc}") from exc
    result = CsnToOssieConverter().convert(csn, name=args.name)
    _report(result.issues)
    Path(args.output).write_text(result.output.to_ossie_yaml())
    print(f"Written to {args.output}", file=sys.stderr)


def _cmd_ossie_to_csn(args: argparse.Namespace) -> None:
    raw = yaml.safe_load(Path(args.input).read_text())
    try:
        document = OssieDocument.model_validate(raw)
    except ValidationError as exc:
        raise ConverterError(f"{args.input} is not a valid Ossie document:\n{exc}") from exc
    result = OssieToCsnConverter().convert(document)
    _report(result.issues)
    Path(args.output).write_text(json.dumps(result.output, indent=2) + "\n")
    print(f"Written to {args.output}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ossie-sap-csn",
        description="Convert between SAP CSN (Core Schema Notation) JSON and Ossie YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    to_ossie = subparsers.add_parser("csn-to-ossie", help="Convert SAP CSN JSON → Ossie YAML")
    to_ossie.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to CSN JSON")
    to_ossie.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output Ossie YAML")
    to_ossie.add_argument("--name", metavar="NAME", help="Name of the Ossie semantic model (CSN has no model name)")

    to_csn = subparsers.add_parser("ossie-to-csn", help="Convert Ossie YAML → SAP CSN JSON")
    to_csn.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to Ossie YAML")
    to_csn.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output CSN JSON")

    args = parser.parse_args()
    try:
        if args.command == "csn-to-ossie":
            _cmd_csn_to_ossie(args)
        else:
            _cmd_ossie_to_csn(args)
    except ConverterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
