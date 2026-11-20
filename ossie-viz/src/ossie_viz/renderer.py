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

from __future__ import annotations

import html
import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from ossie.models import OssieDocument


def load_validated_document(model_path: Path) -> OssieDocument:
    """Load and validate an .ossie.yaml file strictly via the core Ossie SDK."""
    raw_data = yaml.safe_load(model_path.read_text(encoding="utf-8"))
    doc = OssieDocument.model_validate(raw_data)

    # Delegate to core validator module if available so validator and visualizer never drift.
    try:
        from ossie import validate as ossie_validate  # type: ignore

        if hasattr(ossie_validate, "validate_document"):
            ossie_validate.validate_document(doc)
    except ImportError:
        pass

    return doc


def infer_column_type(fld: dict[str, Any]) -> str:
    if fld.get("data_type"):
        return str(fld["data_type"]).upper()
    if fld.get("type"):
        return str(fld["type"]).upper()

    dim = fld.get("dimension") or {}
    if isinstance(dim, dict) and dim.get("is_time"):
        return "TIMESTAMP"

    name = fld.get("name", "").lower()
    if name.endswith("_id") or name == "id":
        return "BIGINT"
    if any(k in name for k in ("date", "time", "_at")):
        return "TIMESTAMP"
    if any(k in name for k in ("amount", "price", "revenue", "cost", "total", "egp", "usd")):
        return "DECIMAL"
    if any(k in name for k in ("count", "qty", "quantity", "age", "gb")):
        return "INT"
    if name.startswith(("is_", "has_")):
        return "BOOLEAN"
    return "VARCHAR"


def estimate_card_height(ds: dict[str, Any]) -> int:
    header_h = 58
    fields_h = 0
    for fld in ds.get("fields", []):
        syns = (fld.get("ai_context") or {}).get("synonyms") or []
        fields_h += 52 if syns else 36
    return header_h + max(fields_h, 42)


def compute_fact_dim_layout(datasets: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> None:
    """Arrange datasets in a left-to-right Fact -> Bridge -> Dimension layout."""
    in_deg: dict[str, int] = {ds["name"]: 0 for ds in datasets}
    out_deg: dict[str, int] = {ds["name"]: 0 for ds in datasets}
    parents_of: dict[str, list[str]] = {ds["name"]: [] for ds in datasets}
    children_of: dict[str, list[str]] = {ds["name"]: [] for ds in datasets}

    for rel in relationships:
        src, dst = rel["from"], rel["to"]
        if src in out_deg and dst in in_deg and src != dst:
            out_deg[src] += 1
            in_deg[dst] += 1
            parents_of[src].append(dst)
            children_of[dst].append(src)

    facts = [d for d in datasets if out_deg[d["name"]] > 0 and in_deg[d["name"]] == 0]
    bridges = [d for d in datasets if out_deg[d["name"]] > 0 and in_deg[d["name"]] > 0]
    dims = [d for d in datasets if in_deg[d["name"]] > 0 and out_deg[d["name"]] == 0]
    isolated = [d for d in datasets if in_deg[d["name"]] == 0 and out_deg[d["name"]] == 0]

    facts.sort(key=lambda d: (-out_deg[d["name"]], d["name"]))

    columns: list[list[dict[str, Any]]] = []
    for group in (facts, bridges, dims, isolated):
        if group:
            columns.append(group)
    if not columns:
        columns = [datasets]

    col_spacing_x = 480
    start_x = 80
    start_y = 70
    row_gap_y = 48

    y_center_map: dict[str, float] = {}
    first_col = columns[0]
    cur_y = start_y
    for ds in first_col:
        h = estimate_card_height(ds)
        ds["x"] = start_x
        ds["y"] = cur_y
        y_center_map[ds["name"]] = cur_y + h / 2.0
        cur_y += h + row_gap_y

    first_col_h = max(0, cur_y - start_y - row_gap_y)

    for col_idx in range(1, len(columns)):
        col = columns[col_idx]

        def barycenter(ds: dict[str, Any]) -> tuple[float, int, str]:
            neighbors = children_of[ds["name"]] + parents_of[ds["name"]]
            known_ys = [y_center_map[n] for n in neighbors if n in y_center_map]
            avg_y = sum(known_ys) / len(known_ys) if known_ys else 1e9
            return (avg_y, -in_deg[ds["name"]], ds["name"])

        col.sort(key=barycenter)

        total_col_h = sum(estimate_card_height(d) for d in col) + row_gap_y * max(0, len(col) - 1)
        offset_y = max(start_y, start_y + int((first_col_h - total_col_h) / 2))

        y_cursor = offset_y
        for ds in col:
            h = estimate_card_height(ds)
            ds["x"] = start_x + col_idx * col_spacing_x
            ds["y"] = y_cursor
            y_center_map[ds["name"]] = y_cursor + h / 2.0
            y_cursor += h + row_gap_y


def _extract_referenced_datasets(dialects: dict[str, str], valid_dataset_names: set[str]) -> list[str]:
    """Identify dataset names referenced as `dataset.field` inside metric expressions."""
    found: set[str] = set()
    for expr in dialects.values():
        for candidate in re.findall(r"\b([a-zA-Z_]\w*)\.[a-zA-Z_]\w*\b", expr):
            if candidate in valid_dataset_names:
                found.add(candidate)
    return sorted(found)


def build_graph_payload(doc: OssieDocument) -> dict[str, Any]:
    """Transform a validated OssieDocument into a serializable graph payload."""
    raw = doc.model_dump(by_alias=True, exclude_none=True)

    discovered_dialects: list[str] = ["OSSIE_SQL_2026"]
    seen_dialects: set[str] = {"OSSIE_SQL_2026"}

    def register_dialect(name: str) -> None:
        upper = name.upper()
        if upper not in seen_dialects:
            seen_dialects.add(upper)
            discovered_dialects.append(upper)

    fk_map: dict[str, set[str]] = {}
    for rel in raw.get("relationships", []):
        fk_map.setdefault(rel["from"], set()).update(rel.get("from_columns", []))

    datasets: list[dict[str, Any]] = []
    for ds in raw.get("datasets", []):
        pks = set(ds.get("primary_key", []))
        fks = fk_map.get(ds["name"], set())
        fields = []
        for fld in ds.get("fields", []):
            dialects: dict[str, str] = {}
            for d in fld.get("expression", {}).get("dialects", []):
                d_name = d["dialect"].upper()
                register_dialect(d_name)
                dialects[d_name] = d["expression"]
            fields.append({
                "name": fld["name"],
                "col_type": infer_column_type(fld),
                "is_pk": fld["name"] in pks,
                "is_fk": fld["name"] in fks,
                "dialects": dialects,
                "ai_context": fld.get("ai_context", {}),
                "raw": fld,
            })
        datasets.append({
            "id": f"ds_{ds['name']}",
            "name": ds["name"],
            "source": ds.get("source", ""),
            "primary_key": ds.get("primary_key", []),
            "description": ds.get("description", ""),
            "ai_context": ds.get("ai_context", {}),
            "fields": fields,
            "x": 80,
            "y": 80,
            "raw": ds,
        })

    # Assign parallel lane indices per unordered (from, to) dataset pair so
    # role-playing dimensions / multi-key joins never collapse onto one line.
    pair_counts: dict[tuple[str, str], int] = {}
    relationships: list[dict[str, Any]] = []
    for idx, rel in enumerate(raw.get("relationships", [])):
        pair_key = tuple(sorted((rel["from"], rel["to"])))
        lane_idx = pair_counts.get(pair_key, 0)
        pair_counts[pair_key] = lane_idx + 1

        relationships.append({
            "id": f"rel_{idx}_{rel['name']}",
            "name": rel["name"],
            "from": rel["from"],
            "to": rel["to"],
            "from_columns": rel.get("from_columns", []),
            "to_columns": rel.get("to_columns", []),
            "lane_index": lane_idx,
            "is_self_join": rel["from"] == rel["to"],
            "raw": rel,
        })

    for rel in relationships:
        pair_key = tuple(sorted((rel["from"], rel["to"])))
        rel["pair_total"] = pair_counts[pair_key]

    compute_fact_dim_layout(datasets, relationships)

    dataset_names = {d["name"] for d in datasets}
    metrics: list[dict[str, Any]] = []
    for met in raw.get("metrics", []):
        dialects = {}
        for d in met.get("expression", {}).get("dialects", []):
            d_name = d["dialect"].upper()
            register_dialect(d_name)
            dialects[d_name] = d["expression"]
        metrics.append({
            "id": f"met_{met['name']}",
            "name": met["name"],
            "description": met.get("description", ""),
            "dialects": dialects,
            "referenced_datasets": _extract_referenced_datasets(dialects, dataset_names),
            "ai_context": met.get("ai_context", {}),
            "raw": met,
        })

    return {
        "name": raw.get("name", "ossie_model"),
        "version": raw.get("version", ""),
        "description": raw.get("description", ""),
        "ai_context": raw.get("ai_context", {}),
        "dialects": discovered_dialects,
        "datasets": datasets,
        "relationships": relationships,
        "metrics": metrics,
    }


def render_html(doc: OssieDocument) -> str:
    """Render a validated OssieDocument into a self-contained offline HTML page."""
    payload = build_graph_payload(doc)
    data_json = json.dumps(payload, indent=2).replace("</", "<\\/")
    safe_title = html.escape(payload["name"])
    safe_version = html.escape(payload["version"])
    instructions_raw = (payload.get("ai_context") or {}).get("instructions") or "No global instructions defined."
    safe_instructions = html.escape(instructions_raw)

    dialect_buttons_html = "\n      ".join(
        f'<button class="dialect-btn{" active" if i == 0 else ""}" data-dialect="{html.escape(d)}">{html.escape(d)}</button>'
        for i, d in enumerate(payload["dialects"])
    )

    template = files("ossie_viz.templates").joinpath("viewer.html").read_text(encoding="utf-8")
    return (
        template.replace("__TITLE__", safe_title)
        .replace("__VERSION__", safe_version)
        .replace("__INSTRUCTIONS__", safe_instructions)
        .replace("__DIALECT_BUTTONS__", dialect_buttons_html)
        .replace("__DEFAULT_DIALECT__", html.escape(payload["dialects"][0]))
        .replace("__MODEL_JSON__", data_json)
    )


def render_file(model_path: Path, out_path: Path) -> Path:
    doc = load_validated_document(model_path)
    out_path.write_text(render_html(doc), encoding="utf-8")
    return out_path
