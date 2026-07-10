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
"""

import argparse
import json
import sys
from pathlib import Path

from ossie_wisdom.converter_issues import ConverterIssueType
from ossie_wisdom.wisdom_to_osi import WisdomToOSIConverter

_ISSUE_REASON: dict[ConverterIssueType, str] = {
    ConverterIssueType.UNSUPPORTED_DIALECT: "the connection dialect has no Ossie equivalent; expressions were emitted verbatim as ANSI_SQL",
    ConverterIssueType.CARDINALITY_LOSS: "Ossie relationships encode cardinality by direction and cannot represent many-to-many",
    ConverterIssueType.RELATIONSHIP_DROPPED: "the relationship uses a join Ossie cannot represent (non-table source, OR/non-equi condition, or unknown dataset)",
    ConverterIssueType.METRIC_NAME_COLLISION: "another table defines a measure with the same name; this one was prefixed with its table name",
    ConverterIssueType.STALE_MEASURE: "wisdom marked this measure stale; it was converted anyway",
    ConverterIssueType.DUPLICATE_FIELD_DROPPED: "the dataset already has a field with this name",
}

_DROPPED_ISSUE_TYPES = {
    ConverterIssueType.RELATIONSHIP_DROPPED,
    ConverterIssueType.DUPLICATE_FIELD_DROPPED,
}


def _cmd_wisdom_to_osi(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    export = json.loads(input_path.read_text())
    result = WisdomToOSIConverter().convert(export)

    for issue in result.issues:
        verb = "was dropped" if issue.issue_type in _DROPPED_ISSUE_TYPES else "was converted with loss"
        reason = _ISSUE_REASON[issue.issue_type]
        print(f"[WARNING] {issue.issue_type.value}: {issue.element_name} {verb} during conversion because {reason}", file=sys.stderr)

    output_path.write_text(result.output.to_osi_yaml())
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

    args = parser.parse_args()
    if args.command == "wisdom-to-osi":
        _cmd_wisdom_to_osi(args)


if __name__ == "__main__":
    main()
