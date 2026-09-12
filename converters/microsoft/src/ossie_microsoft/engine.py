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

"""Optional, engine-backed validation of an exported ``model.bim``.

:mod:`ossie_microsoft.tom` validates *structure* offline: it parses TMSL,
resolves cross-object references and checks cardinality. It does not parse DAX
at all -- a measure reading ``TOTALLYFAKE(1)`` deserializes and validates
clean, as does one with an unbalanced parenthesis.

This module closes that gap by putting the model in front of the real Analysis
Services engine:

1. rewrite every partition as an inline M literal holding generated sample
   rows, so the model refreshes with no gateway, lakehouse or credential;
2. publish it to a Fabric workspace;
3. refresh it, which is what actually compiles the DAX;
4. evaluate every measure and report the engine's own diagnostics;
5. delete the model again.

Unlike the rest of this package it is **not** an offline transform: it needs a
Fabric workspace, it creates and deletes a real item in it, and it consumes
capacity. Nothing here runs unless a caller supplies a workspace id.
"""

import base64
import json
import time
import typing
import urllib.error
import urllib.request
from dataclasses import dataclass

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com"
POWERBI_API = "https://api.powerbi.com/v1.0/myorg"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api"

# TMSL dataType -> (M ascribed type, sample values). The values are deliberately
# boring: the point is to make the engine compile and run the DAX, not to model a
# realistic distribution. Every column gets a null so that BLANK-sensitive
# aggregates behave the way they would on real data.
_SAMPLES = {
    "int64": ("Int64.Type", [1, 2, 2, 3, None]),
    "double": ("type number", [10.5, 20.25, 20.25, 30.0, None]),
    "decimal": ("type number", [10.5, 20.25, 20.25, 30.0, None]),
    "string": ("type text", ['"a"', '"b"', '"b"', '"c"', None]),
    "boolean": ("type logical", ["true", "false", "true", "false", None]),
    "dateTime": (
        "type datetime",
        [
            "#datetime(2024,1,1,0,0,0)",
            "#datetime(2024,2,1,0,0,0)",
            "#datetime(2024,2,1,0,0,0)",
            "#datetime(2024,3,1,0,0,0)",
            None,
        ],
    ),
}
_DEFAULT_SAMPLE = _SAMPLES["string"]
_ROWS = 5

# A calculation group's single column is engine-supplied, and its partition uses
# a dedicated source type rather than a data source.
_CALC_GROUP = "calculationGroup"


class EngineUnavailableError(RuntimeError):
    """Raised when no credential or workspace is available for live validation."""


class EngineValidationError(ValueError):
    """Raised by :meth:`EngineValidationResult.raise_for_errors` for a bad model."""


@dataclass(frozen=True)
class EngineFinding:
    """One object the engine was asked about, and what it said."""

    kind: str
    object: str
    error: str | None = None
    value: typing.Any = None
    expression: str | None = None

    @property
    def ok(self):
        return self.error is None


@dataclass(frozen=True)
class EngineValidationResult:
    """Every finding the engine reported for one deployed model."""

    findings: tuple[EngineFinding, ...] = ()
    stage: str = "evaluate"
    error: str | None = None

    @property
    def failures(self):
        return tuple(f for f in self.findings if not f.ok)

    @property
    def is_valid(self):
        return self.error is None and not self.failures

    def raise_for_errors(self):
        if self.is_valid:
            return
        if self.error:
            raise EngineValidationError(f"{self.stage}: {self.error}")
        raise EngineValidationError(
            "\n".join(f"{f.object}: {f.error}" for f in self.failures)
        )


def _request(method, url, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")[:2000], dict(exc.headers)


def _engine_message(body):
    """Pull the engine's own diagnostic out of a Power BI REST error envelope."""

    try:
        payload = json.loads(body) if isinstance(body, str) else body
        error = payload.get("error", {})
        for detail in error.get("pbi.error", {}).get("details") or []:
            value = detail.get("detail", {}).get("value")
            if value:
                return value.replace("<oii>", "").replace("</oii>", "")
        return error.get("message") or str(payload)[:400]
    except (ValueError, AttributeError):
        return str(body)[:400]


def _m_literal(value):
    return "null" if value is None else str(value)


def _distinct_values(datatype, count):
    """Distinct, non-null values for key and relationship-target columns."""

    if datatype == "int64":
        return list(range(1, count + 1))
    if datatype in ("double", "decimal"):
        return [float(i) + 0.5 for i in range(1, count + 1)]
    if datatype == "boolean":
        return ["true", "false"] * ((count + 1) // 2)
    if datatype == "dateTime":
        return [f"#datetime(2024,{(i % 12) + 1},1,0,0,0)" for i in range(count)]
    return [f'"k{i}"' for i in range(1, count + 1)]


def _inline_partition(table, distinct_columns=frozenset()):
    """Replace a table's partitions with one inline M literal of sample rows.

    The generated source has no external dependency, so a refresh needs no
    gateway, no lakehouse and no stored credential. ``distinct_columns`` names
    the columns that must hold distinct, non-null values -- primary keys and the
    one-side of any relationship -- because the engine rejects a refresh that
    would violate the cardinality declared in the model.
    """

    columns = [
        column
        for column in table.get("columns", [])
        # Calculated columns are evaluated by the engine and rowNumber columns
        # are generated by it. Neither is bound to the partition source.
        if column.get("type") not in ("calculated", "rowNumber")
    ] or [{"name": "Dummy", "dataType": "int64"}]

    names = ", ".join(f'"{c.get("sourceColumn", c["name"])}"' for c in columns)

    value_lists = []
    types = []
    for column in columns:
        datatype = column.get("dataType", "string")
        mtype = _SAMPLES.get(datatype, _DEFAULT_SAMPLE)[0]
        if column["name"] in distinct_columns or column.get("isKey") or column.get("isUnique"):
            value_lists.append(_distinct_values(datatype, _ROWS))
        else:
            value_lists.append(_SAMPLES.get(datatype, _DEFAULT_SAMPLE)[1])
        types.append(mtype)

    rows = [
        "{" + ", ".join(_m_literal(values[i % len(values)]) for values in value_lists) + "}"
        for i in range(_ROWS)
    ]
    transforms = ", ".join(
        f'{{"{c.get("sourceColumn", c["name"])}", {t}}}'
        for c, t in zip(columns, types, strict=True)
    )
    expression = (
        f"let Source = #table({{{names}}}, {{{', '.join(rows)}}}), "
        f"Typed = Table.TransformColumnTypes(Source, {{{transforms}}}) "
        "in Typed"
    )
    return {
        "name": f"{table['name']}-ossie-sample",
        "mode": "import",
        "source": {"type": "m", "expression": expression},
    }


def build_deployable(bim, name):
    """Turn a converter-produced TMSL document into one a workspace can refresh.

    The converter emits Direct Lake partitions pointing at a lakehouse that does
    not exist in a validation tenant. Swapping them for inline import partitions
    keeps every table, column, relationship and measure intact while removing the
    only thing that would block a refresh, so what the engine compiles is still
    the DAX the converter produced.
    """

    document = json.loads(json.dumps(bim))
    document["name"] = name
    # schemaName on entity partitions, and other recent TMSL, need a modern
    # compatibility level.
    document["compatibilityLevel"] = max(document.get("compatibilityLevel", 1567), 1604)

    model = document.setdefault("model", {})
    model.pop("dataSources", None)
    # Shared M expressions model the Direct Lake source. Inline partitions
    # replace them, and leaving them behind fails the refresh.
    remaining = [e for e in model.get("expressions", []) if e.get("kind") not in (None, "m")]
    if remaining:
        model["expressions"] = remaining
    else:
        model.pop("expressions", None)
    model.setdefault("culture", "en-US")
    model["defaultPowerBIDataSourceVersion"] = "powerBI_V3"
    if any(_CALC_GROUP in table for table in model.get("tables", [])):
        # The engine refuses to create a calculation group without this.
        model["discourageImplicitMeasures"] = True

    distinct = {}
    for relationship in model.get("relationships", []):
        if relationship.get("toCardinality", "one") == "one":
            distinct.setdefault(relationship["toTable"], set()).add(relationship["toColumn"])
        if relationship.get("fromCardinality") == "one":
            distinct.setdefault(relationship["fromTable"], set()).add(relationship["fromColumn"])

    for table in model.get("tables", []):
        if _CALC_GROUP in table:
            for column in table.get("columns", []):
                column["dataType"] = "string"
                column["sourceColumn"] = "Name"
            table["partitions"] = [
                {
                    "name": f"{table['name']}-calculation-group",
                    "mode": "import",
                    "source": {"type": _CALC_GROUP},
                }
            ]
        else:
            table["partitions"] = [
                _inline_partition(table, distinct.get(table["name"], frozenset()))
            ]

    return document


def _wait_for_operation(url, token, tries=40, delay=3):
    for _ in range(tries):
        time.sleep(delay)
        _status, body, _headers = _request("GET", url, token)
        if isinstance(body, dict) and body.get("status") in ("Succeeded", "Failed"):
            return body
    return {"status": "TimedOut"}


def deploy(document, workspace, name, token):
    """Publish a TMSL document as a semantic model. Returns ``(id, error)``."""

    def encode(value):
        return base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")

    payload = {
        "displayName": name,
        "type": "SemanticModel",
        "definition": {
            "format": "TMSL",
            "parts": [
                {
                    "path": "model.bim",
                    "payload": encode(document),
                    "payloadType": "InlineBase64",
                },
                {
                    "path": "definition.pbism",
                    "payload": encode({"version": "1.0", "settings": {}}),
                    "payloadType": "InlineBase64",
                },
            ],
        },
    }
    status, body, headers = _request(
        "POST", f"{FABRIC_API}/workspaces/{workspace}/items", token, payload
    )
    if status in (200, 201):
        return body["id"], None
    if status == 202:
        operation = headers.get("Location")
        result = _wait_for_operation(operation, token)
        if result.get("status") != "Succeeded":
            return None, json.dumps(result)[:4000]
        _status, created, _headers = _request("GET", f"{operation}/result", token)
        return created["id"], None
    return None, f"HTTP {status}: {_engine_message(body)}"


def refresh(workspace, dataset, token, tries=60, delay=5):
    """Refresh a dataset, which is what compiles its DAX. Returns an error or None."""

    status, body, _headers = _request(
        "POST",
        f"{POWERBI_API}/groups/{workspace}/datasets/{dataset}/refreshes",
        token,
        {"notifyOption": "NoNotification"},
    )
    if status not in (200, 202):
        return f"HTTP {status}: {_engine_message(body)}"
    for _ in range(tries):
        time.sleep(delay)
        status, body, _headers = _request(
            "GET",
            f"{POWERBI_API}/groups/{workspace}/datasets/{dataset}/refreshes?$top=1",
            token,
        )
        if status == 200 and body.get("value"):
            state = body["value"][0]
            if state.get("status") == "Completed":
                return None
            if state.get("status") in ("Failed", "Disabled"):
                return json.dumps(state)[:600]
    return "refresh timed out"


def evaluate(workspace, dataset, token, query):
    """Run one DAX query. Returns ``(rows, error)``."""

    status, body, _headers = _request(
        "POST",
        f"{POWERBI_API}/groups/{workspace}/datasets/{dataset}/executeQueries",
        token,
        {"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}},
    )
    if status == 200:
        return body["results"][0]["tables"][0].get("rows", []), None
    return None, _engine_message(body)


def _expression_text(expression):
    if isinstance(expression, list):
        return "\n".join(str(line) for line in expression)
    return expression


def check_model(document, workspace, dataset, token):
    """Ask the engine to evaluate every table and every measure."""

    findings = []
    for table in document.get("model", {}).get("tables", []):
        _rows, error = evaluate(
            workspace, dataset, token, f"EVALUATE ROW(\"n\", COUNTROWS('{table['name']}'))"
        )
        findings.append(EngineFinding(kind="table", object=table["name"], error=error))

        for measure in table.get("measures", []):
            rows, error = evaluate(
                workspace, dataset, token, f'EVALUATE ROW("v", [{measure["name"]}])'
            )
            expression = _expression_text(measure.get("expression"))
            # A measure whose DAX failed to compile is dropped from the model, so
            # referencing it by name returns no rows rather than an error. That is
            # precisely the silent failure this validator exists to catch, so the
            # expression is re-evaluated inline to recover the real diagnostic.
            if error is None and not rows and expression:
                _retry_rows, retry_error = evaluate(
                    workspace, dataset, token, f'EVALUATE ROW("v", {expression})'
                )
                error = retry_error or (
                    f"measure '{measure['name']}' is not present in the deployed model; "
                    "the engine rejected it"
                )
            findings.append(
                EngineFinding(
                    kind="measure",
                    object=f"{table['name']}[{measure['name']}]",
                    error=error,
                    value=(rows[0].get("[v]") if rows else None) if not error else None,
                    expression=expression,
                )
            )
    return tuple(findings)


def validate_with_engine(bim, *, workspace, fabric_token, powerbi_token, name=None, keep=False):
    """Deploy, refresh and evaluate ``bim``, then delete it again.

    This is the only validation in this package that leaves the local machine.
    It creates a real semantic model in ``workspace`` and removes it afterwards
    unless ``keep`` is set.
    """

    if not workspace:
        raise EngineUnavailableError("a Fabric workspace id is required")
    if not fabric_token or not powerbi_token:
        raise EngineUnavailableError("both a Fabric and a Power BI token are required")

    name = name or f"ossie-validation-{int(time.time())}"
    document = build_deployable(bim, name)

    dataset, error = deploy(document, workspace, name, fabric_token)
    if error:
        return EngineValidationResult(stage="deploy", error=error)

    try:
        error = refresh(workspace, dataset, powerbi_token)
        if error:
            return EngineValidationResult(stage="refresh", error=error)
        findings = check_model(document, workspace, dataset, powerbi_token)
        return EngineValidationResult(findings=findings)
    finally:
        if not keep:
            _request("DELETE", f"{FABRIC_API}/workspaces/{workspace}/items/{dataset}", fabric_token)
