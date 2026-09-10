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

"""Command line interface for the Ossie <> Lightdash converter."""

import argparse
import json
import sys
from pathlib import Path

import yaml

from ossie import OSIDocument
from ossie_lightdash.lightdash_to_osi import LightdashToOSIConverter
from ossie_lightdash.osi_to_lightdash import OSIToLightdashConverter


def _read_document(path: Path) -> OSIDocument:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return OSIDocument.model_validate_json(text)
    return OSIDocument.model_validate(yaml.safe_load(text))


def _print_issues(issues) -> None:
    for issue in issues:
        print(f"[{issue.issue_type.value}] {issue.element_name}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="ossie-lightdash")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="Ossie document (.json/.yaml) -> Lightdash dbt schema.yml"
    )
    export_parser.add_argument("input", type=Path)
    export_parser.add_argument("output", type=Path)

    import_parser = subparsers.add_parser(
        "import", help="Lightdash dbt schema.yml -> Ossie document (.json/.yaml)"
    )
    import_parser.add_argument("input", type=Path)
    import_parser.add_argument("output", type=Path)
    import_parser.add_argument("--database", default=None)
    import_parser.add_argument("--schema", default=None)
    import_parser.add_argument(
        "--semantic-model-name", default="lightdash_semantic_model"
    )

    args = parser.parse_args()

    if args.command == "export":
        result = OSIToLightdashConverter().convert(_read_document(args.input))
        args.output.write_text(
            yaml.safe_dump(result.output, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        schema_yml = yaml.safe_load(args.input.read_text(encoding="utf-8"))
        result = LightdashToOSIConverter().convert(
            schema_yml,
            database=args.database,
            schema=args.schema,
            semantic_model_name=args.semantic_model_name,
        )
        document = result.output.model_dump(by_alias=True, exclude_none=True)
        if args.output.suffix == ".json":
            args.output.write_text(
                json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        else:
            args.output.write_text(
                yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

    _print_issues(result.issues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
