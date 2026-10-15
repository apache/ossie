#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run the Polaris CLI against a deterministic in-process Iceberg REST catalog."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parents[2]
CATALOG = "interop"


class CatalogState:
    def __init__(self, bundle: dict | None = None) -> None:
        self.bundle = bundle or {"catalog": CATALOG, "namespaces": []}

    def namespace(self, parts: list[str], *, create: bool = False) -> dict | None:
        for item in self.bundle.get("namespaces", []):
            if item.get("namespace") == parts:
                return item
        if not create:
            return None
        item = {"namespace": parts, "properties": {}, "tables": []}
        self.bundle.setdefault("namespaces", []).append(item)
        return item

    def table(self, parts: list[str], name: str) -> dict | None:
        namespace = self.namespace(parts)
        if not namespace:
            return None
        return next((table for table in namespace.get("tables", []) if table.get("name") == name), None)


def _segments(path: str) -> list[str]:
    return [unquote(part) for part in urlparse(path).path.split("/") if part]


def _namespace_parts(value: str) -> list[str]:
    return value.split("\x1f") if value else []


def _metadata_response(table: dict) -> dict:
    schema = table.get("schema") or {"type": "struct", "schema-id": 0, "fields": []}
    return {
        "metadata": {
            "current-schema-id": schema.get("schema-id", 0),
            "schemas": [schema],
            "properties": table.get("properties") or {},
        }
    }


def _handler(state: CatalogState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            parts = _segments(self.path)
            try:
                ns_index = parts.index("namespaces")
            except ValueError:
                self._send(404, {"error": "unknown endpoint"})
                return

            if len(parts) == ns_index + 1:
                namespace = payload.get("namespace") or []
                item = state.namespace(namespace, create=True)
                assert item is not None
                item["properties"] = payload.get("properties") or {}
                self._send(201, {})
                return

            if len(parts) == ns_index + 3 and parts[-1] == "tables":
                namespace = _namespace_parts(parts[ns_index + 1])
                item = state.namespace(namespace, create=True)
                assert item is not None
                tables = item.setdefault("tables", [])
                tables[:] = [table for table in tables if table.get("name") != payload.get("name")]
                tables.append(payload)
                self._send(201, {})
                return

            self._send(404, {"error": "unknown endpoint"})

        def do_GET(self) -> None:
            parts = _segments(self.path)
            try:
                ns_index = parts.index("namespaces")
            except ValueError:
                self._send(404, {"error": "unknown endpoint"})
                return

            if len(parts) == ns_index + 1:
                self._send(200, {"namespaces": [item["namespace"] for item in state.bundle.get("namespaces", [])]})
                return

            namespace = _namespace_parts(parts[ns_index + 1])
            if len(parts) == ns_index + 3 and parts[-1] == "tables":
                item = state.namespace(namespace)
                identifiers = [] if item is None else [
                    {"namespace": namespace, "name": table["name"]}
                    for table in item.get("tables", [])
                ]
                self._send(200, {"identifiers": identifiers})
                return

            if len(parts) == ns_index + 4 and parts[ns_index + 2] == "tables":
                table = state.table(namespace, parts[-1])
                if table is None:
                    self._send(404, {"error": "table not found"})
                else:
                    self._send(200, _metadata_response(table))
                return

            self._send(404, {"error": "unknown endpoint"})

    return Handler


def _java_command(build_root: Path) -> list[str]:
    classpath_file = build_root / "target" / "interop-classpath.txt"
    if not classpath_file.is_file():
        raise RuntimeError("Polaris classpath is missing; run the manifest prepare command")
    classpath = os.pathsep.join(
        [str(build_root / "target" / "classes"), classpath_file.read_text(encoding="utf-8").strip()]
    )
    return ["java", "-cp", classpath, "org.apache.ossie.converter.polaris.OssiePolarisConverter"]


def _run_cli(state: CatalogState, args: list[str], build_root: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        process = subprocess.run(
            [*_java_command(build_root), *args, "--url", url, "--catalog", CATALOG],
            cwd=build_root,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=__import__("sys").stderr)
    if process.returncode != 0:
        raise SystemExit(process.returncode)


def export_model(source: Path, output: Path, build_root: Path) -> None:
    state = CatalogState()
    _run_cli(state, ["export", str(source.resolve())], build_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state.bundle, indent=2) + "\n", encoding="utf-8")


def import_model(source: Path, output: Path, build_root: Path) -> None:
    bundle = json.loads(source.read_text(encoding="utf-8"))
    state = CatalogState(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_cli(state, ["import", "-o", str(output.resolve())], build_root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "import"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--build-root", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "export":
        export_model(args.input, args.output, args.build_root)
    else:
        import_model(args.input, args.output, args.build_root)


if __name__ == "__main__":
    main()
