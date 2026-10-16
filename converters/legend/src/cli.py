#!/usr/bin/env python3
"""Command-line interface for OSI to Legend converter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from legend_osi import osi_to_legend_dict, osi_to_legend_pure, OsiToLegendConversionError


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Convert OSI semantic models to FINOS Legend representation"
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        type=Path,
        help="Input OSI YAML file",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="Output Legend file (JSON or Pure DSL based on extension)",
    )
    parser.add_argument(
        "-p",
        "--package",
        default="org.finos.osi.generated",
        help="Package namespace for Legend database (default: org.finos.osi.generated)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "pure", "auto"],
        default="auto",
        help="Output format: json, pure, or auto (detect from file extension)",
    )

    args = parser.parse_args()

    try:
        # Load OSI YAML
        with open(args.input, "r") as f:
            osi_data = yaml.safe_load(f)

        if not isinstance(osi_data, dict):
            print(f"Error: OSI input must be a YAML mapping, got {type(osi_data)}", file=sys.stderr)
            sys.exit(1)

        # Determine output format
        output_format = args.format
        if output_format == "auto":
            if args.output.suffix == ".pure":
                output_format = "pure"
            else:
                output_format = "json"

        # Convert to Legend
        if output_format == "pure":
            legend_output = osi_to_legend_pure(osi_data, database_package=args.package)
        else:
            legend_dict = osi_to_legend_dict(osi_data, database_package=args.package)
            legend_output = json.dumps(legend_dict, indent=2)

        # Write output
        with open(args.output, "w") as f:
            f.write(legend_output)

        # Report results
        if output_format == "pure":
            print(f"✓ Converted {args.input} → {args.output} (Pure DSL)")
        else:
            print(f"✓ Converted {args.input} → {args.output} (JSON)")
            legend_dict = json.loads(legend_output)
            db = legend_dict["databases"][0]
            print(f"  Database: {db['name']}")
            print(f"  Tables: {len(db['tables'])}")
            print(f"  Relations: {len(db['relations'])}")
            print(f"  Joins: {len(db.get('joins', []))}")

    except FileNotFoundError as e:
        print(f"Error: File not found: {e}", file=sys.stderr)
        sys.exit(1)
    except OsiToLegendConversionError as e:
        print(f"Error: Conversion failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
