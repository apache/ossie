#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema>=4.26.0"]
# ///
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""Check captured MCP tool declarations and results against the Ossie profile."""

import argparse
import base64
import binascii
import csv
import datetime
import io
import json
import math
import re
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

SCHEMA_DIR = Path(__file__).with_name("mcp")
VERSION = "0.4-draft"
METADATA = "org.apache.ossie/execute_query"
UNCHECKED = [
    "query expression/name/path/grain semantics and cross-engine answers",
    "authorization, read-only enforcement and absence of hidden execution",
    "live transport, discovery, Tasks lifecycle and deployment limits",
    "Markdown meaning and optional binary file format fidelity",
]


def _no_retrieval(uri):
    raise NoSuchResource(ref=uri)


def _schemas():
    schemas = {}
    for path in SCHEMA_DIR.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[schema["$id"]] = schema
    registry = Registry(retrieve=_no_retrieval).with_resources(
        (uri, Resource.from_contents(schema)) for uri, schema in schemas.items()
    )
    return schemas, registry


def _pointer(parts):
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _canonical(schema):
    """Recognize the published contract; do not attempt arbitrary schema equivalence."""
    annotations = {"$schema", "$id", "$comment", "title", "description", "examples", "default", "$defs"}
    budget = 50000

    def visit(node, depth=0, context="schema"):
        nonlocal budget
        budget -= 1
        if depth > 64 or budget < 0:
            raise ValueError("Schema normalization budget exceeded")
        if isinstance(node, dict):
            if context == "schema" and depth > 0 and "$id" in node:
                raise ValueError("Nested reference scopes are not recognized")
            if context == "schema" and "$ref" in node:
                ref = node["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    raise ValueError("Only local JSON Pointer references are recognized")
                target = schema
                for part in ref[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
                expanded = visit(target, depth + 1)
                siblings = visit({key: value for key, value in node.items() if key != "$ref"}, depth + 1)
                return {"allOf": [expanded, siblings]} if siblings else expanded
            result = {}
            for key, value in node.items():
                if context == "schema" and key in annotations:
                    continue
                if context == "schema" and key in {"$dynamicRef", "$recursiveRef"}:
                    raise ValueError("Dynamic references are not recognized")
                child_context = "schema"
                if context == "schema" and key in {"properties", "patternProperties", "dependentSchemas"}:
                    child_context = "mapping"
                elif context == "schema" and key in {"const", "enum"}:
                    child_context = "data"
                elif context == "data":
                    child_context = "data"
                normalized = visit(value, depth + 1, child_context)
                if context == "schema" and key in {"required", "enum", "allOf", "anyOf", "oneOf"}:
                    normalized = sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
                result[key] = normalized
            return result
        if isinstance(node, list):
            return [visit(item, depth + 1, context) for item in node]
        return node

    return visit(schema)


def _decode_csv(value):
    if value == r"\N":
        return None
    if value.startswith("\\"):
        if not value.startswith("\\\\"):
            raise ValueError("Non-null strings beginning with backslash must be escaped")
        return value[1:]
    return value


def _has_quoted_null(text):
    # csv.reader discards quoting; the profile's null token must be unquoted.
    in_quotes, field_start, body = False, True, False
    index = 0
    while index < len(text):
        char = text[index]
        if in_quotes:
            if char == '"':
                if text[index:index + 2] == '""':
                    index += 2
                    continue
                in_quotes = False
        elif char == '"' and field_start:
            after = text[index + 4:index + 5]
            if body and text[index:index + 4] == r'"\N"' and after in {"", ",", "\r", "\n"}:
                return True
            in_quotes, field_start = True, False
        elif char in {",", "\r", "\n"}:
            field_start = True
            if char != ",":
                body = True
        else:
            field_start = False
        index += 1
    return False


def _cell(value, datatype, from_csv=False):
    if value is None:
        return None
    if datatype == "String" or datatype is None or datatype == "Opaque":
        if not isinstance(value, str):
            raise ValueError("Non-text opaque encodings cannot be verified by this checker")
        return value
    if datatype in {"Integer", "Decimal"}:
        pattern = r"[+-]?\d+" if datatype == "Integer" else r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
        if not isinstance(value, str) or re.fullmatch(pattern, value, re.ASCII) is None:
            raise ValueError(f"{datatype} must be an exact locale-neutral string")
        return value
    if datatype == "Boolean":
        if from_csv and value in {"true", "false"}:
            return value == "true"
        if not from_csv and isinstance(value, bool):
            return value
        raise ValueError("Boolean must be a JSON boolean / CSV true or false")
    if datatype == "Float":
        if from_csv:
            if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value, re.ASCII) is None:
                raise ValueError("Float must be locale-neutral numeric text")
            value = float(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Float must be a finite JSON number")
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"{datatype} must be an ISO 8601 string")
    if datatype == "Date":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, re.ASCII) is None:
            raise ValueError("Date must use YYYY-MM-DD")
        datetime.date.fromisoformat(value)
    elif datatype == "Time":
        parsed = datetime.time.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise ValueError("Time must be timezone-naive")
    else:
        if "T" not in value:
            raise ValueError("DateTime must include the ISO T separator")
        parsed = datetime.datetime.fromisoformat(value)
        if (parsed.tzinfo is not None) != (datatype == "DateTimeTz"):
            raise ValueError("DateTime and DateTimeTz must preserve timezone distinction")
    return value


def validate_capture(capture):
    """Return evidence about captured contract conformance, never engine certification."""
    schemas, registry = _schemas()
    findings, unverified = [], []

    def issue(code, path, message):
        findings.append({"code": code, "path": path, "message": message})

    def validate(value, schema, path):
        errors = list(Draft202012Validator(
            schema, registry=registry, format_checker=FormatChecker()
        ).iter_errors(value))
        for error in errors:
            issue("CONTRACT_SCHEMA", path + _pointer(error.absolute_path), error.message)
        return not errors

    def report(count):
        return {
            "profile": VERSION,
            "scope": "captured MCP endpoint contract",
            "status": "failed" if findings else ("unverified" if unverified else "passed"),
            "cases_checked": count,
            "findings": findings,
            "unverified": unverified,
            "unchecked": UNCHECKED,
        }

    def schema_for(name):
        return schemas[f"urn:apache:ossie:execute-query:{name}:{VERSION}"]

    if not validate(capture, schema_for("capture"), ""):
        return report(0)
    tool = capture["tool"]
    for key, name in (("inputSchema", "input"), ("outputSchema", "output")):
        if key not in tool:
            continue
        try:
            Draft202012Validator.check_schema(tool[key])
        except SchemaError as error:
            issue("INVALID_DECLARATION", f"/tool/{key}", error.message)
            continue
        try:
            recognized = _canonical(tool[key]) == _canonical(schema_for(name))
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            unverified.append({"path": f"/tool/{key}", "reason": str(error)})
        else:
            if not recognized:
                unverified.append({
                    "path": f"/tool/{key}",
                    "reason": "Declaration is not recognized as the published schema; equivalence is unproven",
                })
    if tool.get("annotations", {}).get("readOnlyHint") is False:
        issue("READ_ONLY_DECLARATION", "/tool/annotations/readOnlyHint", "Profile declares read-only execution")

    models = {}
    for index, case in enumerate(capture["cases"]):
        path = f"/cases/{index}/result"
        arguments, result = case["arguments"], case["result"]
        payload = result.get("structuredContent")
        input_valid = Draft202012Validator(schema_for("input")).is_valid(arguments)
        if not input_valid and not result["isError"]:
            issue("INVALID_INPUT_ACCEPTED", path, "Arguments outside the contract must not return success")
        if payload is None:
            if "outputSchema" in tool:
                issue("MISSING_STRUCTURED_OUTPUT", path, "Tool advertised outputSchema but returned no structuredContent")
            else:
                unverified.append({"path": path, "reason": "Markdown-only interpretation requires manual review"})
            continue
        if result["isError"] != (payload["status"] == "error"):
            issue("STATUS_MISMATCH", path, "isError and structured status disagree")
        if "data_source_id" in payload and payload["data_source_id"] != arguments.get("data_source_id"):
            issue("SOURCE_MISMATCH", path, "Result source must match supplied source")
        if "model" in payload and "data_source_id" in payload:
            model, source = payload["model"], payload["data_source_id"]
            identity = (model["id"], model["revision"])
            if source in models and models[source] != identity:
                issue("MODEL_REBOUND", path + "/model", "One binding returned different model revisions")
            models[source] = identity
        embedded = {}
        for block_index, block in enumerate(result["content"]):
            if block["type"] != "resource":
                continue
            resource = block["resource"]
            resource_path = path + f"/content/{block_index}/resource"
            uri = resource["uri"]
            if uri in embedded:
                issue("DUPLICATE_RESOURCE", resource_path, "Embedded resource URIs must be distinct")
            embedded[uri] = resource
            if "blob" in resource:
                try:
                    base64.b64decode(resource["blob"], validate=True)
                except (ValueError, binascii.Error):
                    issue("INVALID_BASE64", resource_path + "/blob", "Binary resource is not valid base64")
        if payload["status"] == "error":
            continue
        descriptors = payload["result"]["resources"]
        descriptor_uris = [descriptor["uri"] for descriptor in descriptors]
        if len(set(descriptor_uris)) != len(descriptor_uris):
            issue("DUPLICATE_RESOURCE", path + "/structuredContent/result/resources", "Descriptor URIs must be distinct")
        if any(resource["mimeType"] == "text/csv" and uri not in descriptor_uris for uri, resource in embedded.items()):
            issue("UNDESCRIBED_CSV", path, "Every embedded CSV requires a result descriptor")
        for descriptor in descriptors:
            resource = embedded.get(descriptor["uri"])
            if resource is None:
                issue("MISSING_EMBEDDED_RESOURCE", path, "Resource descriptor has no embedded bytes")
                continue
            if resource["mimeType"] != descriptor["mime_type"]:
                issue("RESOURCE_MISMATCH", path, "Descriptor MIME type differs from embedded resource")
            expected_mime = {
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "parquet": "application/vnd.apache.parquet",
            }.get(descriptor["format"])
            if expected_mime is not None and descriptor["mime_type"] != expected_mime:
                issue("RESOURCE_MISMATCH", path, "Export format requires its declared standard MIME type")
            if descriptor["format"] in {"xlsx", "parquet"} and "blob" not in resource:
                issue("BINARY_NOT_BLOB", path, "XLSX/Parquet must use base64 blob content")
            if descriptor["format"] != "csv":
                continue
            if "text" not in resource:
                issue("CSV_NOT_TEXT", path, "CSV must be embedded as text")
                continue
            metadata = resource.get("_meta", {}).get(METADATA)
            preview_schema = schema_for("output")["properties"]["preview"]
            metadata_schema = {
                "type": "object",
                "required": ["columns", "row_count", "completeness", "csv_null_value", "csv_escape_prefix"],
                "properties": {
                    "columns": preview_schema["properties"]["columns"],
                    "row_count": {"type": "integer", "minimum": 0},
                    "completeness": {"enum": ["complete", "truncated", "unknown"]},
                    "csv_null_value": {"const": r"\N"},
                    "csv_escape_prefix": {"const": "\\"},
                },
            }
            if not validate(metadata, metadata_schema, path + "/csv_metadata"):
                continue
            if any(metadata[key] != payload["result"][key] for key in ("row_count", "completeness")):
                issue("CSV_METADATA_MISMATCH", path, "CSV counts/completeness differ from the result")
            columns = metadata["columns"]
            if _has_quoted_null(resource["text"]):
                issue("VALUE_ENCODING", path, "CSV null marker must be unquoted; literal backslashes must be escaped")
            try:
                rows = list(csv.reader(io.StringIO(resource["text"], newline=""), strict=True))
            except csv.Error as error:
                if str(error).startswith("field larger than field limit"):
                    unverified.append({"path": path, "reason": "CSV parser field-size budget exceeded"})
                else:
                    issue("INVALID_CSV", path, "CSV cannot be parsed")
                continue
            if not rows or rows[0] != [column["name"] for column in columns]:
                issue("CSV_HEADER", path, "CSV header differs from ordered column metadata")
                continue
            rows = rows[1:]
            if len(rows) != payload["result"]["row_count"]:
                issue("CSV_ROW_COUNT", path, "CSV row count differs from materialized count")
            if any(len(row) != len(columns) for row in rows):
                issue("CSV_ARITY", path, "CSV row width differs from column metadata")
                continue
            preview = payload.get("preview")
            try:
                values = [
                    tuple(_cell(_decode_csv(cell), column.get("datatype"), True) for cell, column in zip(row, columns))
                    for row in rows
                ]
                if preview is None:
                    continue
                if preview["columns"] != columns:
                    issue("PREVIEW_COLUMNS", path, "Preview and CSV column descriptors differ")
                if preview["row_count"] != len(preview["rows"]):
                    issue("PREVIEW_ROW_COUNT", path, "Preview row_count differs from its rows")
                if preview["has_more"] != (payload["result"]["row_count"] > preview["row_count"]):
                    issue("PREVIEW_COMPLETENESS", path, "Preview has_more differs from materialized count")
                if any(len(row) != len(columns) for row in preview["rows"]):
                    issue("PREVIEW_ARITY", path, "Preview row width differs from column metadata")
                    continue
                displayed = [
                    tuple(_cell(cell, column.get("datatype")) for cell, column in zip(row, columns))
                    for row in preview["rows"]
                ]
                value_keys = [json.dumps(row, allow_nan=False) for row in values]
                display_keys = [json.dumps(row, allow_nan=False) for row in displayed]
                if Counter(display_keys) - Counter(value_keys):
                    issue("PREVIEW_VALUES", path, "Preview contains values or repetitions absent from CSV")
                if preview.get("selection") == "first_rows" and display_keys != value_keys[:len(display_keys)]:
                    issue("PREVIEW_SELECTION", path, "First-row preview differs from CSV order")
            except (ValueError, OverflowError) as error:
                if str(error).startswith("Non-text opaque"):
                    unverified.append({"path": path, "reason": str(error)})
                else:
                    issue("VALUE_ENCODING", path, str(error))
    return report(len(capture["cases"]))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Capture JSON; see validation/mcp/example-capture.json")
    args = parser.parse_args(argv)
    try:
        if args.capture.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Capture exceeds the checker's 16 MiB input bound")
        capture = json.loads(
            args.capture.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "failed", "findings": [{"code": "CAPTURE_INPUT", "message": str(error)}]}))
        return 1
    report = validate_capture(capture)
    print(json.dumps(report, indent=2, allow_nan=False))
    return {"passed": 0, "failed": 1, "unverified": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
