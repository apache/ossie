#!/usr/bin/env python3
"""
OSI ↔ OBML Bidirectional Converter
===================================
Converts between Open Semantic Interchange (OSI v0.2.0.dev0) YAML models
and OrionBelt Markup Language (OBML v1.0) YAML models.

OSI v0.1.1 inputs are still accepted on read — the legacy shim
``_normalize_legacy_v01`` promotes pre-v0.2 custom_extensions into the
v0.2 first-class fields before regular parsing runs.

Author: OrionBelt / RALFORION

This module is a thin **facade**. The converter implementation is split across
sibling modules to keep each file focused:

* :mod:`ossie_orionbelt._common` — shared constants and mapping tables
* :mod:`ossie_orionbelt.osi_to_obml` — :class:`OSItoOBML`
* :mod:`ossie_orionbelt.obml_to_osi` — :class:`OBMLtoOSI`
* :mod:`ossie_orionbelt.ontology` — :class:`OBMLtoOSIOntology`
* :mod:`ossie_orionbelt.validation` — :class:`ValidationResult` + ``validate_*``

Every public name is re-exported here so ``ossie_orionbelt.converter.<name>``
continues to work unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from ossie_orionbelt._common import (
    _COLUMN_REF_RE as _COLUMN_REF_RE,
)
from ossie_orionbelt._common import (
    _INTERNAL_VENDORS as _INTERNAL_VENDORS,
)
from ossie_orionbelt._common import (
    _OBML_VENDOR_READ as _OBML_VENDOR_READ,
)
from ossie_orionbelt._common import (
    _OSI_VENDOR_READ as _OSI_VENDOR_READ,
)
from ossie_orionbelt._common import (
    _OSI_VERSION as _OSI_VERSION,
)
from ossie_orionbelt._common import (
    _SQL_PARSEABLE_DIALECTS as _SQL_PARSEABLE_DIALECTS,
)
from ossie_orionbelt._common import (
    _VENDOR_OBML as _VENDOR_OBML,
)
from ossie_orionbelt._common import (
    _VENDOR_OSI as _VENDOR_OSI,
)

# Shared constants / mapping tables (re-exported for backwards compatibility).
# The ``X as X`` aliases mark these as intentional re-exports so historic
# ``from ossie_orionbelt.converter import <name>`` imports keep working.
from ossie_orionbelt._common import (
    OBML_TO_OSI_TYPE as OBML_TO_OSI_TYPE,
)
from ossie_orionbelt._common import (
    OSI_TO_OBML_TYPE as OSI_TO_OBML_TYPE,
)
from ossie_orionbelt.obml_to_osi import OBMLtoOSI as OBMLtoOSI
from ossie_orionbelt.ontology import OBMLtoOSIOntology as OBMLtoOSIOntology
from ossie_orionbelt.osi_to_obml import OSItoOBML as OSItoOBML
from ossie_orionbelt.validation import (
    _OBML_SCHEMA_PATH as _OBML_SCHEMA_PATH,
)
from ossie_orionbelt.validation import (
    _OSI_SCHEMA_PATH as _OSI_SCHEMA_PATH,
)
from ossie_orionbelt.validation import (
    _SCHEMAS_DIR as _SCHEMAS_DIR,
)
from ossie_orionbelt.validation import (
    _SCRIPT_DIR as _SCRIPT_DIR,
)
from ossie_orionbelt.validation import (
    ValidationResult as ValidationResult,
)
from ossie_orionbelt.validation import (
    _validate_json_schema as _validate_json_schema,
)
from ossie_orionbelt.validation import (
    validate_obml as validate_obml,
)
from ossie_orionbelt.validation import (
    validate_osi as validate_osi,
)
from ossie_orionbelt.validation import (
    validate_osi_ontology as validate_osi_ontology,
)

__all__ = [
    "OBML_TO_OSI_TYPE",
    "OSI_TO_OBML_TYPE",
    "OBMLtoOSI",
    "OBMLtoOSIOntology",
    "OSItoOBML",
    "ValidationResult",
    "validate_obml",
    "validate_osi",
    "validate_osi_ontology",
    "main",
]


# ═══════════════════════════════════════════════════════════════════════════
#  CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="OSI ↔ OBML Bidirectional Converter")
    parser.add_argument("direction", choices=["osi2obml", "obml2osi"], help="Conversion direction")
    parser.add_argument("input", nargs="?", help="Input YAML file")
    parser.add_argument("-o", "--output", help="Output YAML file")
    parser.add_argument(
        "--name", default="semantic_model", help="Model name for OBML→OSI conversion"
    )
    parser.add_argument(
        "--description", default="", help="Model description for OBML→OSI conversion"
    )
    parser.add_argument(
        "--ai-instructions", default="", help="AI instructions for OBML→OSI conversion"
    )
    parser.add_argument(
        "--database", default="ANALYTICS", help="Default database for OSI→OBML conversion"
    )
    parser.add_argument("--schema", default="PUBLIC", help="Default schema for OSI→OBML conversion")
    parser.add_argument(
        "--no-validate", action="store_true", help="Skip OBML validation after conversion"
    )

    args = parser.parse_args()

    if not args.input:
        parser.error("Input file is required for conversion")

    input_path = Path(args.input)
    with open(input_path) as f:
        data = yaml.safe_load(f)

    if args.direction == "osi2obml":
        converter = OSItoOBML(data, args.database, args.schema)
        result = converter.convert()
        warnings = converter.warnings
    else:
        converter = OBMLtoOSI(data, args.name, args.description, args.ai_instructions)
        result = converter.convert()
        warnings = converter.warnings

    # Output
    output_yaml = yaml.dump(
        result, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120
    )

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_yaml)
        print(f"✅ Converted to {args.output}")
    else:
        print(output_yaml)

    if warnings:
        print("\n⚠️  Conversion warnings:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

    # ── Validate output ────────────────────────────────────────────────
    if not args.no_validate:
        has_errors = False

        if args.direction == "osi2obml":
            # Validate OBML output
            print("\n🔍 Validating OBML output...", file=sys.stderr)
            vr = validate_obml(result)
            for line in vr.summary_lines():
                print(line, file=sys.stderr)
            if vr.valid:
                print("✅ OBML output is valid", file=sys.stderr)
            else:
                print("❌ OBML output has validation errors", file=sys.stderr)
                has_errors = True
        else:
            # Validate OBML input (source) and OSI output
            print("\n🔍 Validating OBML input...", file=sys.stderr)
            vr_obml = validate_obml(data)
            for line in vr_obml.summary_lines():
                print(line, file=sys.stderr)
            if vr_obml.valid:
                print("✅ OBML input is valid", file=sys.stderr)
            else:
                print("❌ OBML input has validation errors", file=sys.stderr)
                has_errors = True

            print("\n🔍 Validating OSI output...", file=sys.stderr)
            vr_osi = validate_osi(result)
            for line in vr_osi.summary_lines():
                print(line, file=sys.stderr)
            if vr_osi.valid:
                print("✅ OSI output is valid", file=sys.stderr)
            else:
                print("❌ OSI output has validation errors", file=sys.stderr)
                has_errors = True

        if has_errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
