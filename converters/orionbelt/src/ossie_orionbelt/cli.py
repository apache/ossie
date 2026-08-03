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

"""Command-line entry point for the OBML <-> OSI converter.

A single ``ossie-orionbelt`` command with two format-named subcommands, mirroring
the OSI converter convention (e.g. ``osi-dbt msi-to-osi``):

    ossie-orionbelt obml-to-osi  -i model.obml.yaml -o model.osi.yaml
    ossie-orionbelt obml-to-osi --ontology -i model.obml.yaml -o model.ontology.yaml
    ossie-orionbelt osi-to-obml  -i model.osi.yaml  -o model.obml.yaml

Both subcommands print conversion warnings and a validation summary to stderr,
and exit non-zero when the produced document fails schema validation (unless
``--no-validate``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from ossie_orionbelt.converter import (
    OBMLtoOSI,
    OBMLtoOSIOntology,
    OSItoOBML,
    validate_obml,
    validate_osi,
    validate_osi_ontology,
)


def _load(input_path: str) -> dict[str, Any]:
    data = yaml.safe_load(Path(input_path).read_text())
    if not isinstance(data, dict):
        print(f"Error: {input_path} is not a YAML mapping", file=sys.stderr)
        raise SystemExit(2)
    return data


def _emit(result: dict[str, Any], output: str) -> None:
    output_yaml = yaml.dump(
        result, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120
    )
    Path(output).write_text(output_yaml)
    print(f"Written to {output}", file=sys.stderr)


def _print_warnings(warnings: list[str]) -> None:
    if warnings:
        print("\nConversion warnings:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)


def _report_validation(label: str, result: dict[str, Any], validate_fn: Any) -> bool:
    """Validate ``result`` and print a summary. Return True if there are errors."""
    print(f"\nValidating {label}...", file=sys.stderr)
    vr = validate_fn(result)
    for line in vr.summary_lines():
        print(line, file=sys.stderr)
    if vr.valid:
        print(f"{label} is valid", file=sys.stderr)
        return False
    print(f"{label} has validation errors", file=sys.stderr)
    return True


def _cmd_obml_to_osi(args: argparse.Namespace) -> int:
    """OBML -> OSI core-spec (or, with --ontology, OSI ontology)."""
    data = _load(args.input)

    validate_fn: Any
    if args.ontology:
        converter: Any = OBMLtoOSIOntology(
            data, args.model_name, args.description, args.ai_instructions
        )
        result = converter.convert()
        validate_fn, label = validate_osi_ontology, "OSI ontology output"
    else:
        converter = OBMLtoOSI(data, args.model_name, args.description, args.ai_instructions)
        result = converter.convert()
        validate_fn, label = validate_osi, "OSI output"

    _emit(result, args.output)
    _print_warnings(converter.warnings)

    if args.no_validate:
        return 0
    return 1 if _report_validation(label, result, validate_fn) else 0


def _cmd_osi_to_obml(args: argparse.Namespace) -> int:
    """OSI core-spec -> OBML."""
    data = _load(args.input)

    converter = OSItoOBML(data, args.database, args.schema)
    result = converter.convert()

    _emit(result, args.output)
    _print_warnings(converter.warnings)

    if args.no_validate:
        return 0
    return 1 if _report_validation("OBML output", result, validate_obml) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ossie-orionbelt",
        description="Convert between OrionBelt OBML and OSI YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    o2s = subparsers.add_parser("obml-to-osi", help="Convert OBML YAML → OSI YAML")
    o2s.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to OBML YAML")
    o2s.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="Path for output OSI YAML"
    )
    o2s.add_argument(
        "--ontology", action="store_true", help="Emit an OSI ontology document instead of core-spec"
    )
    o2s.add_argument(
        "--model-name", default="semantic_model", metavar="NAME", help="OSI semantic model name"
    )
    o2s.add_argument("--description", default="", metavar="TEXT", help="OSI model description")
    o2s.add_argument(
        "--ai-instructions", default="", metavar="TEXT", help="OSI ai_context instructions"
    )
    o2s.add_argument("--no-validate", action="store_true", help="Skip output validation")

    s2o = subparsers.add_parser("osi-to-obml", help="Convert OSI YAML → OBML YAML")
    s2o.add_argument("-i", "--input", required=True, metavar="FILE", help="Path to OSI YAML")
    s2o.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="Path for output OBML YAML"
    )
    s2o.add_argument(
        "--database", default="ANALYTICS", metavar="NAME", help="Default database for OBML output"
    )
    s2o.add_argument(
        "--schema", default="PUBLIC", metavar="NAME", help="Default schema for OBML output"
    )
    s2o.add_argument("--no-validate", action="store_true", help="Skip output validation")

    args = parser.parse_args(argv)
    if args.command == "obml-to-osi":
        return _cmd_obml_to_osi(args)
    if args.command == "osi-to-obml":
        return _cmd_osi_to_obml(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
