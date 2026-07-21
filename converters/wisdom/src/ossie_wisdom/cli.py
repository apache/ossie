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

"""CLI entry point for the ossie-wisdom converter.

Usage:
    ossie-wisdom wisdom-to-osi -i domain-export.json -o output.yaml
    ossie-wisdom osi-to-wisdom -i input.yaml -o domain-export.json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from ossie import OSIDocument
from ossie_wisdom.converter_issues import ConverterIssueType
from ossie_wisdom.osi_to_wisdom import OSIToWisdomConverter
from ossie_wisdom.wisdom_to_osi import WisdomToOSIConverter

_ISSUE_REASON: dict[ConverterIssueType, str] = {
    ConverterIssueType.UNSUPPORTED_DIALECT: "the connection dialect has no Ossie equivalent; expressions were emitted verbatim as ANSI_SQL",
    ConverterIssueType.CARDINALITY_LOSS: "Ossie relationships encode cardinality by direction and cannot represent many-to-many",
    ConverterIssueType.RELATIONSHIP_DROPPED: "the relationship uses a join that cannot be represented (non-table source, OR/non-equi condition, or unknown dataset)",
    ConverterIssueType.METRIC_NAME_COLLISION: "another table defines a measure with the same name; this one was prefixed with its table name",
    ConverterIssueType.STALE_MEASURE: "wisdom marked this measure stale; it was converted anyway",
    ConverterIssueType.DUPLICATE_FIELD_DROPPED: "the dataset already has a field with this name",
    ConverterIssueType.EXTRA_MODEL_DROPPED: "a wisdom domain export holds a single domain; only the first semantic model was converted",
    ConverterIssueType.AI_CONTEXT_DROPPED: "wisdom has no equivalent for ai_context at this level (or for synonyms/examples)",
    ConverterIssueType.METRIC_TABLE_UNRESOLVED: "the metric expression references no known dataset; it was attached to the first dataset",
    ConverterIssueType.MISSING_DIALECT_EXPRESSION: "no expression was available in the dataset's dialect or ANSI_SQL; the first available dialect was used",
    ConverterIssueType.UNIQUE_KEYS_DROPPED: "wisdom has no unique-key construct",
    ConverterIssueType.CUSTOM_EXTENSION_DROPPED: "wisdom cannot store Ossie custom extensions",
}

_DROPPED_ISSUE_TYPES = {
    ConverterIssueType.RELATIONSHIP_DROPPED,
    ConverterIssueType.DUPLICATE_FIELD_DROPPED,
    ConverterIssueType.EXTRA_MODEL_DROPPED,
    ConverterIssueType.AI_CONTEXT_DROPPED,
    ConverterIssueType.UNIQUE_KEYS_DROPPED,
    ConverterIssueType.CUSTOM_EXTENSION_DROPPED,
}


def _print_issues(result) -> None:
    for issue in result.issues:
        verb = "was dropped" if issue.issue_type in _DROPPED_ISSUE_TYPES else "was converted with loss"
        reason = _ISSUE_REASON.get(issue.issue_type, issue.issue_type.value)
        print(f"[WARNING] {issue.issue_type.value}: {issue.element_name} {verb} during conversion because {reason}", file=sys.stderr)


def _cmd_wisdom_to_osi(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    export = json.loads(input_path.read_text())
    result = WisdomToOSIConverter().convert(export)

    _print_issues(result)
    output_path.write_text(result.output.to_osi_yaml())
    print(f"Written to {output_path}", file=sys.stderr)


def _cmd_osi_to_wisdom(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    document = OSIDocument.model_validate(yaml.safe_load(input_path.read_text()))
    result = OSIToWisdomConverter().convert(document)

    _print_issues(result)
    output_path.write_text(json.dumps(result.output, indent=2) + "\n")
    print(f"Written to {output_path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ossie-wisdom",
        description="Convert a WisdomAI domain export JSON to Ossie YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    wisdom_to_osi = subparsers.add_parser("wisdom-to-osi", help="Convert domain-export.json → Ossie YAML")
    wisdom_to_osi.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to wisdom domain export JSON")
    wisdom_to_osi.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output Ossie YAML")

    osi_to_wisdom = subparsers.add_parser("osi-to-wisdom", help="Convert Ossie YAML → domain-export.json")
    osi_to_wisdom.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to Ossie YAML")
    osi_to_wisdom.add_argument("-o", "--output", required=True, metavar="FILE", help="Path for output wisdom domain export JSON")

    args = parser.parse_args()
    if args.command == "wisdom-to-osi":
        _cmd_wisdom_to_osi(args)
    elif args.command == "osi-to-wisdom":
        _cmd_osi_to_wisdom(args)


if __name__ == "__main__":
    main()
