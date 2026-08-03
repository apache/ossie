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

"""CLI entry point for the ossie-dbt converter.

Usage:
    ossie-dbt msi-to-osi -i semantic_manifest.json -o output.yaml
    ossie-dbt osi-to-msi -i input.yaml -o semantic_manifest.json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from ossie import OSIDocument
from ossie_dbt.converter_issues import ConverterIssueType
from ossie_dbt.msi_to_osi import MSIToOSIConverter
from ossie_dbt.osi_to_msi import OSIToMSIConverter

from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest

_ISSUE_REASON: dict[ConverterIssueType, str] = {
    ConverterIssueType.CONVERSION_METRIC_DROPPED: "Ossie has no conversion-funnel metric type",
    ConverterIssueType.PRIVATE_METRIC_DROPPED: "Ossie has no visibility modifiers",
    ConverterIssueType.NATURAL_ENTITY_DROPPED: "Ossie has no natural-key entity type",
    ConverterIssueType.CUMULATIVE_SEMANTICS_LOSS: "Ossie expressions cannot represent window or grain semantics; the base aggregation was preserved",
}

_DROPPED_ISSUE_TYPES = {
    ConverterIssueType.CONVERSION_METRIC_DROPPED,
    ConverterIssueType.PRIVATE_METRIC_DROPPED,
    ConverterIssueType.NATURAL_ENTITY_DROPPED,
}


def _cmd_msi_to_osi(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    manifest = parse_manifest_from_dbt_generated_manifest(input_path.read_text())
    result = MSIToOSIConverter().convert(manifest, osi_model_name=args.model_name)

    if result.issues:
        for issue in result.issues:
            verb = "was dropped" if issue.issue_type in _DROPPED_ISSUE_TYPES else "was converted with loss"
            reason = _ISSUE_REASON[issue.issue_type]
            print(f"[WARNING] {issue.issue_type.value}: {issue.element_name} {verb} during conversion because {reason}", file=sys.stderr)

    output_path.write_text(result.output.to_osi_yaml())
    print(f"Written to {output_path}", file=sys.stderr)


def _cmd_osi_to_msi(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    raw = yaml.safe_load(input_path.read_text())
    document = OSIDocument.model_validate(raw)
    result = OSIToMSIConverter().convert(document)

    output_path.write_text(result.output.model_dump_json(by_alias=True, exclude_none=True, indent=2))
    print(f"Written to {output_path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ossie-dbt",
        description="Convert between dbt semantic_manifest.json and Ossie YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    msi_to_osi = subparsers.add_parser("msi-to-osi", help="Convert semantic_manifest.json → Ossie YAML")
    msi_to_osi.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to semantic_manifest.json")
    msi_to_osi.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output Ossie YAML")
    msi_to_osi.add_argument(
        "--model-name", default="semantic_model", metavar="NAME", help="Ossie semantic model name (default: semantic_model)"
    )

    osi_to_msi = subparsers.add_parser("osi-to-msi", help="Convert Ossie YAML → semantic_manifest.json")
    osi_to_msi.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to Ossie YAML")
    osi_to_msi.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output semantic_manifest.json")

    args = parser.parse_args()
    if args.command == "msi-to-osi":
        _cmd_msi_to_osi(args)
    elif args.command == "osi-to-msi":
        _cmd_osi_to_msi(args)


if __name__ == "__main__":
    main()
