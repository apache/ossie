#!/usr/bin/env python3
"""OSSIE I/O protocol adapter for the dbt converter.

Reads a JSON request from stdin:
    {"files": {"name": "<utf-8 content>", ...}}

Writes a JSON response to stdout:
    {"files": {"name": "<content>", ...}, "issues": [...]}

All debug output goes to stderr. Stdout is reserved for the response.
"""
import json
import sys


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("to-osi", "from-osi"):
        print(f"usage: {sys.argv[0]} <to-osi|from-osi>", file=sys.stderr)
        sys.exit(1)

    try:
        request = json.load(sys.stdin)
    except Exception as e:
        print(f"failed to parse request: {e}", file=sys.stderr)
        sys.exit(1)

    files = request.get("files", {})
    direction = sys.argv[1]

    if direction == "to-osi":
        response = to_osi(files)
    else:
        response = from_osi(files)

    json.dump(response, sys.stdout)


def to_osi(files: dict) -> dict:
    """Convert dbt semantic_manifest.json → OSI YAML."""
    manifest_content = next(
        (v for k, v in files.items() if k.endswith(".json")), None
    )
    if manifest_content is None:
        return _error("no .json input file found; expected a dbt semantic_manifest.json")

    try:
        from metricflow_semantics.model.dbt_manifest_parser import (
            parse_manifest_from_dbt_generated_manifest,
        )
        from ossie_dbt import MSIToOSIConverter

        manifest = parse_manifest_from_dbt_generated_manifest(manifest_content)
        result = MSIToOSIConverter().convert(manifest, osi_model_name="semantic_model")
    except Exception as e:
        return _error(str(e))

    return {
        "files": {"semantic_model.yaml": result.output.to_osi_yaml()},
        "issues": [_map_issue(i) for i in result.issues],
    }


def from_osi(files: dict) -> dict:
    """Convert OSI YAML → dbt semantic_manifest.json."""
    yaml_content = next(
        (v for k, v in files.items() if k.endswith((".yaml", ".yml"))), None
    )
    if yaml_content is None:
        return _error("no .yaml input file found; expected an OSI YAML document")

    try:
        import yaml
        from ossie import OSIDocument
        from ossie_dbt import OSIToMSIConverter

        raw = yaml.safe_load(yaml_content)
        doc = OSIDocument.model_validate(raw)
        result = OSIToMSIConverter().convert(doc)
    except Exception as e:
        return _error(str(e))

    # PydanticSemanticManifest uses pydantic v1 compat (.json(), not .model_dump_json()).
    manifest_json = result.output.json(by_alias=True, exclude_none=True, indent=2)
    return {
        "files": {"semantic_manifest.json": manifest_json},
        "issues": [_map_issue(i) for i in result.issues],
    }


def _map_issue(issue) -> dict:
    from ossie_dbt import ConverterIssueType

    severity = {
        ConverterIssueType.CONVERSION_METRIC_DROPPED: "warning",
        ConverterIssueType.PRIVATE_METRIC_DROPPED: "warning",
        ConverterIssueType.NATURAL_ENTITY_DROPPED: "warning",
        ConverterIssueType.CUMULATIVE_SEMANTICS_LOSS: "info",
    }.get(issue.issue_type, "warning")

    return {
        "severity": severity,
        "message": f"{issue.issue_type.value}: {issue.element_name}",
        "path": issue.element_name,
    }


def _error(message: str) -> dict:
    return {"files": {}, "issues": [{"severity": "error", "message": message}]}


if __name__ == "__main__":
    main()
