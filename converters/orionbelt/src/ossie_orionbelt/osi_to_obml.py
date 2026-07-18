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

"""OSI → OBML conversion (the :class:`OSItoOBML` direction).

Extracted verbatim from ``converter.py``; see that module for the package-level
docstring and the shared constants in :mod:`ossie_orionbelt._common`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ossie_orionbelt._common import (
    _COLUMN_REF_RE,
    _INTERNAL_VENDORS,
    _OBML_VENDOR_READ,
    _OSI_VERSION,
    _SQL_PARSEABLE_DIALECTS,
    _VENDOR_OSI,
    OSI_TO_OBML_TYPE,
)

# A dataset/column identifier in a resolved metric expression: either a bare SQL
# word or a bracket-quoted token. ``_resolve_column_refs`` bracket-quotes any
# canonical name that is not a bare word (e.g. a display name with spaces) so the
# downstream ``dataset.column`` parsers below never split it on whitespace.
_RESOLVED_IDENT = r"(?:\w+|\[[^\]]+\])"


class OSItoOBML:
    """Convert an OSI semantic model YAML to OBML format."""

    def __init__(
        self, osi: dict, default_database: str = "ANALYTICS", default_schema: str = "PUBLIC"
    ):
        self.osi = osi
        self.default_database = default_database
        self.default_schema = default_schema
        self.warnings: list[str] = []
        # OSI metrics that have no OBML representation (non-SQL dialect only,
        # or an expression our parser cannot decompose). Preserved verbatim
        # rather than dropped — see ``_preserve_unconverted_metric``.
        self._unconverted_metrics: list[dict] = []

    def _normalize_legacy_v01(self) -> None:
        """Promote OSI v0.1.x payloads to the v0.2 shape, in place.

        The v0.2 spec promotes ``primary_key`` and ``unique_keys`` to
        first-class dataset fields. v0.1.x serializers (including ours
        pre-bump) stash both under ``custom_extensions`` with vendor
        ``OBSL`` and keys ``obml_primary_key`` / ``obml_unique_keys``.
        This shim runs before parsing so the rest of the converter can
        assume v0.2 shape regardless of input version.

        No-op for documents that already declare ``version`` >= 0.2 or
        that have nothing to migrate.
        """
        version = str(self.osi.get("version", ""))
        if version and not version.startswith(("0.1", "0.0")):
            return  # already v0.2+ (or future) — nothing to do

        models = self.osi.get("semantic_model", [])
        if not isinstance(models, list):
            return

        for model in models:
            for ds in model.get("datasets", []) or []:
                # Promote legacy primary_key / unique_keys from OBSL extras
                # only if the dataset doesn't already declare them.
                legacy = self._extract_obml_extras(ds)
                if not legacy:
                    continue
                if "primary_key" not in ds and legacy.get("obml_primary_key"):
                    pk = legacy["obml_primary_key"]
                    if isinstance(pk, list) and all(isinstance(c, str) for c in pk):
                        ds["primary_key"] = list(pk)
                if "unique_keys" not in ds and legacy.get("obml_unique_keys"):
                    uk = legacy["obml_unique_keys"]
                    if isinstance(uk, list) and all(
                        isinstance(g, list) and all(isinstance(c, str) for c in g) for g in uk
                    ):
                        ds["unique_keys"] = [list(g) for g in uk]

        if version.startswith(("0.0", "0.1")):
            self.warnings.append(
                f"OSI input declares version '{version}'; legacy v0.1.x "
                f"compatibility shim applied. Output target is v{_OSI_VERSION}."
            )

    def convert(self) -> dict:
        # Reset per-conversion accumulators so calling convert() twice on the
        # same instance is idempotent (no duplicated warnings or preserved
        # metrics). Both are populated as a side effect of conversion below.
        self.warnings = []
        self._unconverted_metrics = []

        # v0.1.x inputs need the legacy shim to promote pre-v0.2
        # custom_extensions into v0.2 first-class fields before we parse.
        self._normalize_legacy_v01()

        models = self.osi.get("semantic_model", [])
        if not models:
            raise ValueError("No semantic_model found in OSI input")

        # Take the first semantic model (OBML is a single-model format)
        model = models[0]
        if len(models) > 1:
            self.warnings.append(
                f"OSI contains {len(models)} semantic models; "
                f"only the first ('{model.get('name')}') is converted."
            )

        obml: dict[str, Any] = {"version": 1.0}

        # ── Model description ─────────────────────────────────────
        if model.get("description"):
            obml["description"] = model["description"]

        # ── DataObjects ─────────────────────────────────────────────
        datasets = model.get("datasets", [])
        relationships = model.get("relationships", [])

        # Build lookup: dataset_name → dataset
        ds_map = {ds["name"]: ds for ds in datasets}

        # Build relationship index: from_dataset → [relationship, ...]
        rel_by_from: dict[str, list] = {}
        for rel in relationships:
            rel_by_from.setdefault(rel["from"], []).append(rel)

        # Collect join key columns: (dataset_name, field_name) pairs
        # These should NOT become dimensions (they are FK/PK join keys)
        self._join_key_columns: set[tuple[str, str]] = set()
        for rel in relationships:
            for col in rel.get("from_columns", []):
                self._join_key_columns.add((rel["from"], col))
            for col in rel.get("to_columns", []):
                self._join_key_columns.add((rel["to"], col))

        data_objects: dict[str, Any] = {}
        for ds in datasets:
            do_name, do_obj = self._convert_dataset(ds, rel_by_from)
            data_objects[do_name] = do_obj

        obml["dataObjects"] = data_objects

        # ── Dimensions (extracted from OSI fields with dimension metadata) ──
        dimensions = self._extract_dimensions(datasets)
        if dimensions:
            obml["dimensions"] = dimensions

        # ── Measures & Metrics ──────────────────────────────────────
        osi_metrics = model.get("metrics", [])
        measures, metrics = self._convert_metrics(osi_metrics, ds_map)
        if measures:
            obml["measures"] = measures
        if metrics:
            obml["metrics"] = metrics

        # Metrics that have no OBML representation are not dropped: stash the
        # original OSI metric verbatim under the OSI vendor so the reverse
        # (OBML -> OSI) direction re-emits them and a full OSI -> OBML -> OSI
        # roundtrip stays lossless. They are not queryable in OBML; a LOSSY
        # warning was already recorded per metric.
        if self._unconverted_metrics:
            obml.setdefault("customExtensions", []).append(
                {
                    "vendor": _VENDOR_OSI,
                    "data": json.dumps({"obml_unconverted_metrics": self._unconverted_metrics}),
                }
            )

        # ── Restore model-level properties from custom_extensions ────
        for ext in model.get("custom_extensions", []):
            if ext.get("vendor_name") in _OBML_VENDOR_READ:
                try:
                    ext_data = json.loads(ext.get("data", "{}"))
                    if ext_data.get("obml_filters"):
                        obml["filters"] = ext_data["obml_filters"]
                    if ext_data.get("obml_settings"):
                        obml["settings"] = ext_data["obml_settings"]
                    if ext_data.get("obml_owner"):
                        obml["owner"] = ext_data["obml_owner"]
                    if ext_data.get("obml_expose_counts") is not None:
                        obml["exposeCounts"] = ext_data["obml_expose_counts"]
                    if ext_data.get("obml_count_label_pattern") is not None:
                        obml["countLabelPattern"] = ext_data["obml_count_label_pattern"]
                except (json.JSONDecodeError, TypeError):
                    pass
                break

        # Preserve third-party vendor extensions verbatim
        self._carry_foreign_extensions(model.get("custom_extensions"), obml)

        return obml

    @staticmethod
    def _carry_foreign_extensions(osi_exts: list[dict] | None, obml_target: dict[str, Any]) -> None:
        """Carry third-party OSI custom_extensions verbatim into OBML.

        Our own payloads and OSI-native stashes are reconstructed elsewhere;
        any other vendor's extension is preserved unchanged on the OBML side
        so a full OSI -> OBML -> OSI roundtrip keeps the original vendor.
        """
        for ext in osi_exts or []:
            vendor = ext.get("vendor_name")
            if vendor and vendor not in _INTERNAL_VENDORS:
                obml_target.setdefault("customExtensions", []).append(
                    {"vendor": vendor, "data": ext.get("data", "")}
                )

    def _parse_source(self, source: str) -> tuple[str, str, str]:
        """Parse 'database.schema.table' into parts."""
        parts = source.split(".")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            return self.default_database, parts[0], parts[1]
        else:
            return self.default_database, self.default_schema, parts[0]

    def _convert_dataset(self, ds: dict, rel_by_from: dict) -> tuple[str, dict]:
        """Convert an OSI dataset to an OBML dataObject.

        Uses the exact OSI dataset name as the OBML data object key.
        """
        name = ds["name"]

        source = ds.get("source", name)
        database, schema, table = self._parse_source(source)

        do: dict[str, Any] = {
            "code": table,
            "database": database,
            "schema": schema,
        }

        # ── Columns ─────────────────────────────────────────────────
        columns: dict[str, Any] = {}
        fields = ds.get("fields", [])
        for field in fields:
            col_name, col_obj = self._convert_field(field)
            columns[col_name] = col_obj

        # ── Primary key flag propagation (OSI v0.2 first-class) ──
        # ``primary_key`` lists physical column codes; mark every matching
        # column with ``primaryKey: true``. Unknown PK columns surface as
        # a warning (the spec couples PK to relationship cardinality, so
        # silently dropping is unsafe).
        pk_codes = ds.get("primary_key") or []
        if pk_codes:
            code_to_col = {col.get("code"): (cname, col) for cname, col in columns.items()}
            unknown_pks: list[str] = []
            for pk_code in pk_codes:
                hit = code_to_col.get(pk_code)
                if hit is None:
                    unknown_pks.append(pk_code)
                    continue
                _, col = hit
                col["primaryKey"] = True
            if unknown_pks:
                self.warnings.append(
                    f"Dataset '{name}' primary_key references unknown columns: "
                    f"{unknown_pks}. Ignored."
                )

        if columns:
            do["columns"] = columns
        else:
            self.warnings.append(f"Dataset '{name}' has no fields; adding placeholder column.")
            do["columns"] = {f"{name}_id": {"code": f"{table}_id", "abstractType": "string"}}

        # ── Joins (from relationships where this dataset is on 'from' side) ──
        joins = []
        for rel in rel_by_from.get(name, []):
            join_obj = self._convert_relationship_to_join(rel)
            joins.append(join_obj)

        if joins:
            do["joins"] = joins

        # ── Description (semantic, from OSI) ─────────────────────────
        if ds.get("description"):
            do["description"] = ds["description"]

        # ── Extract ai_context: synonyms → native, rest → customExtensions ─
        ai_ctx = ds.get("ai_context")
        if ai_ctx:
            ai_data = ai_ctx if isinstance(ai_ctx, dict) else {"instructions": ai_ctx}
            # Extract synonyms directly into OBML synonyms property
            if "synonyms" in ai_data:
                do["synonyms"] = list(ai_data["synonyms"])
            # Store remaining ai_context keys in customExtensions
            remaining = {k: v for k, v in ai_data.items() if k != "synonyms"}
            if remaining:
                do["customExtensions"] = [
                    {
                        "vendor": "OSI",
                        "data": json.dumps(remaining),
                    }
                ]

        # Restore DataObject owner / comment / refresh from custom_extensions
        for ext in ds.get("custom_extensions", []):
            if ext.get("vendor_name") in _OBML_VENDOR_READ:
                try:
                    ext_data = json.loads(ext.get("data", "{}"))
                    if ext_data.get("obml_owner"):
                        do["owner"] = ext_data["obml_owner"]
                    if ext_data.get("obml_comment"):
                        do["comment"] = ext_data["obml_comment"]
                    if ext_data.get("obml_refresh"):
                        do["refresh"] = ext_data["obml_refresh"]
                    if ext_data.get("obml_countable") is not None:
                        do["countable"] = ext_data["obml_countable"]
                    if ext_data.get("obml_count_label") is not None:
                        do["countLabel"] = ext_data["obml_count_label"]
                except (json.JSONDecodeError, TypeError):
                    pass
                break

        # ── Unique keys roundtrip (OBML has no native concept) ──
        # Persist the OSI ``unique_keys`` array into the OBSL-vendor
        # customExtensions so the OBML → OSI direction can emit it back.
        unique_keys = ds.get("unique_keys") or []
        if unique_keys:
            do.setdefault("customExtensions", []).append(
                {
                    "vendor": _VENDOR_OSI,
                    "data": json.dumps({"obml_unique_keys": [list(g) for g in unique_keys]}),
                }
            )

        # Preserve third-party vendor extensions verbatim
        self._carry_foreign_extensions(ds.get("custom_extensions"), do)

        return name, do

    def _convert_field(self, field: dict) -> tuple[str, dict]:
        """Convert an OSI field to an OBML column.

        Uses the exact OSI field name as the OBML column key.
        """
        name = field["name"]

        # Get expression (prefer ANSI_SQL dialect)
        expr_obj = field.get("expression", {})
        code = name  # fallback
        if isinstance(expr_obj, dict):
            dialects = expr_obj.get("dialects", [])
            for d in dialects:
                if d.get("dialect") == "ANSI_SQL":
                    code = d.get("expression", name)
                    break
            if not dialects:
                code = name
            elif code == name and dialects:
                code = dialects[0].get("expression", name)

        # Determine abstract type: prefer explicit data_type, fall back to heuristic
        osi_type = field.get("data_type", "")
        if osi_type and osi_type in OSI_TO_OBML_TYPE:
            abstract_type = OSI_TO_OBML_TYPE[osi_type]
        else:
            abstract_type = self._infer_obml_type(field)

        col: dict[str, Any] = {
            "code": code,
            "abstractType": abstract_type,
        }

        if field.get("description"):
            col["description"] = field["description"]

        # Extract field-level ai_context: synonyms → native, rest → customExtensions
        ai_ctx = field.get("ai_context")
        if ai_ctx:
            ai_data = ai_ctx if isinstance(ai_ctx, dict) else {"instructions": ai_ctx}
            # Extract synonyms directly into OBML synonyms property
            if "synonyms" in ai_data:
                col["synonyms"] = list(ai_data["synonyms"])
            # Store remaining ai_context keys in customExtensions
            remaining = {k: v for k, v in ai_data.items() if k != "synonyms"}
            if remaining:
                col["customExtensions"] = [
                    {
                        "vendor": "OSI",
                        "data": json.dumps(remaining),
                    }
                ]

        # Restore OBML-only column properties from custom_extensions
        for ext in field.get("custom_extensions", []):
            if ext.get("vendor_name") in _OBML_VENDOR_READ:
                try:
                    ext_data = json.loads(ext.get("data", "{}"))
                    if ext_data.get("obml_sql_type"):
                        col["sqlType"] = ext_data["obml_sql_type"]
                    if ext_data.get("obml_sql_precision") is not None:
                        col["sqlPrecision"] = ext_data["obml_sql_precision"]
                    if ext_data.get("obml_sql_scale") is not None:
                        col["sqlScale"] = ext_data["obml_sql_scale"]
                    if ext_data.get("obml_num_class"):
                        col["numClass"] = ext_data["obml_num_class"]
                    if ext_data.get("obml_comment"):
                        col["comment"] = ext_data["obml_comment"]
                    if ext_data.get("obml_owner"):
                        col["owner"] = ext_data["obml_owner"]
                except (json.JSONDecodeError, TypeError):
                    pass
                break

        # ── Field label roundtrip (OSI v0.2 first-class) ──
        # OBML has no native column label today; preserve via OBSL-vendor
        # customExtensions so the reverse direction can emit it back.
        if field.get("label"):
            col.setdefault("customExtensions", []).append(
                {
                    "vendor": _VENDOR_OSI,
                    "data": json.dumps({"obml_field_label": field["label"]}),
                }
            )

        # Preserve third-party vendor extensions verbatim
        self._carry_foreign_extensions(field.get("custom_extensions"), col)

        return name, col

    def _infer_obml_type(self, field: dict) -> str:
        """Infer OBML abstractType from OSI field metadata."""

        dim = field.get("dimension", {})
        if isinstance(dim, dict) and dim.get("is_time"):
            return "date"

        name_lower = field.get("name", "").lower()

        # Helper: match keywords at word boundaries to avoid false positives
        # (e.g. "country" should NOT match "count")
        def _has_keyword(keywords: tuple[str, ...]) -> bool:
            for kw in keywords:
                if kw.startswith("_") or kw.endswith("_"):
                    # Substring match for prefix/suffix patterns like "_sk", "is_"
                    if kw in name_lower:
                        return True
                else:
                    # Word-boundary match for standalone keywords
                    if re.search(r"(?:^|_)" + re.escape(kw) + r"(?:$|_)", name_lower):
                        return True
            return False

        if _has_keyword(
            (
                "_sk",
                "_id",
                "_key",
                "name",
                "desc",
                "email",
                "address",
                "city",
                "state",
                "zip",
                "phone",
                "status",
                "type",
                "category",
                "class",
            )
        ):
            return "string"
        if _has_keyword(
            (
                "price",
                "cost",
                "amount",
                "sales",
                "profit",
                "revenue",
                "tax",
                "discount",
                "rate",
                "percent",
                "ratio",
                "margin",
            )
        ):
            return "float"
        if _has_keyword(("qty", "quantity", "count", "num", "number", "cnt")):
            return "int"
        if _has_keyword(("date", "time", "year", "month", "day", "week")):
            return "date"
        if _has_keyword(("flag", "is_", "has_")):
            return "boolean"

        return "string"

    # OSI relationship type → OBML joinType mapping
    _REL_TYPE_MAP: dict[str, str] = {
        "many_to_one": "many-to-one",
        "many-to-one": "many-to-one",
        "one_to_many": "one-to-many",
        "one-to-many": "one-to-many",
        "one_to_one": "one-to-one",
        "one-to-one": "one-to-one",
        "many_to_many": "many-to-many",
        "many-to-many": "many-to-many",
    }

    def _convert_relationship_to_join(self, rel: dict) -> dict:
        """Convert an OSI relationship to an OBML join.

        Uses exact OSI names for joinTo and column references.
        Maps OSI relationship 'type' to OBML joinType if present,
        defaults to many-to-one with a warning otherwise.
        """
        rel_type = rel.get("type", "")
        join_type = self._REL_TYPE_MAP.get(rel_type.lower(), "") if rel_type else ""
        if not join_type:
            join_type = "many-to-one"
            if rel_type:
                self.warnings.append(
                    f"Relationship '{rel.get('name', '?')}': unknown type "
                    f"'{rel_type}', defaulting to many-to-one."
                )
            else:
                self.warnings.append(
                    f"Relationship '{rel.get('name', '?')}': no type specified, "
                    f"defaulting to many-to-one."
                )

        join: dict[str, Any] = {
            "joinType": join_type,
            "joinTo": rel["to"],
            "columnsFrom": list(rel["from_columns"]),
            "columnsTo": list(rel["to_columns"]),
        }
        return join

    def _extract_dimensions(self, datasets: list) -> dict:
        """Extract dimension definitions from OSI fields marked as dimensions.

        Skips fields that are join keys (FK/PK columns used in relationships),
        since those are structural and not analytical dimensions.
        """
        dimensions: dict[str, Any] = {}
        for ds in datasets:
            ds_name = ds["name"]
            for field in ds.get("fields", []):
                dim = field.get("dimension")
                if dim is None:
                    continue
                field_name = field["name"]
                # Skip join key columns — they are FK/PK, not analytical dims
                if (ds_name, field_name) in self._join_key_columns:
                    continue
                abstract_type = self._infer_obml_type(field)
                dim_def: dict[str, Any] = {
                    "dataObject": ds_name,
                    "column": field_name,
                    "resultType": abstract_type,
                }
                # Extract synonyms from field-level ai_context
                ai_ctx = field.get("ai_context")
                if isinstance(ai_ctx, dict) and ai_ctx.get("synonyms"):
                    dim_def["synonyms"] = list(ai_ctx["synonyms"])
                # Restore OBML-only dimension properties from custom_extensions
                for ext in field.get("custom_extensions", []):
                    if ext.get("vendor_name") in _OBML_VENDOR_READ:
                        try:
                            ext_data = json.loads(ext.get("data", "{}"))
                            if ext_data.get("obml_time_grain"):
                                dim_def["timeGrain"] = ext_data["obml_time_grain"]
                            if ext_data.get("obml_dimension_format"):
                                dim_def["format"] = ext_data["obml_dimension_format"]
                            if ext_data.get("obml_dimension_result_type"):
                                dim_def["resultType"] = ext_data["obml_dimension_result_type"]
                            if ext_data.get("obml_dimension_description"):
                                dim_def["description"] = ext_data["obml_dimension_description"]
                            if ext_data.get("obml_dimension_owner"):
                                dim_def["owner"] = ext_data["obml_dimension_owner"]
                            if ext_data.get("obml_dimension_via"):
                                dim_def["via"] = ext_data["obml_dimension_via"]
                        except (json.JSONDecodeError, TypeError):
                            pass
                        break
                # Dimension names must be unique across the model. When the same
                # field name occurs in more than one data object (e.g. Orders.date
                # and Invoices.date), qualify the later one with its data object
                # instead of silently overwriting the earlier dimension.
                key = field_name
                if key in dimensions and dimensions[key].get("dataObject") != ds_name:
                    key = f"{ds_name} {field_name}"
                    suffix = 2
                    while key in dimensions:
                        key = f"{ds_name} {field_name} {suffix}"
                        suffix += 1
                    self.warnings.append(
                        f"Dimension name '{field_name}' occurs in multiple data "
                        f"objects; emitted '{ds_name}.{field_name}' as dimension "
                        f"'{key}' to avoid a collision."
                    )
                dimensions[key] = dim_def
        return dimensions

    def _convert_metrics(self, osi_metrics: list, ds_map: dict) -> tuple[dict, dict]:
        """
        Convert OSI metrics to OBML measures and metrics.

        OSI has a single 'metrics' concept with SQL expressions.
        OBML separates 'measures' (simple aggregations on single columns)
        from 'metrics' (cross-fact expressions referencing measures).

        Strategy:
        - Simple single-aggregation metrics → OBML measures
        - Aggregation over expression (e.g. SUM(a.x * a.y)) → expression measure
        - Complex/multi-aggregation metrics → OBML metrics referencing auto-measures
        """

        measures: dict[str, Any] = {}
        metrics: dict[str, Any] = {}

        # Case-insensitive dataset/field index for resolving SQL identifiers
        # back to their canonical OSI names (Snowflake/Databricks expressions
        # commonly upper-case or quote them). Identifiers resolve by BOTH the
        # OSI name and the physical code (source-table code / bare-identifier
        # field expression): our own OBML -> OSI emitter writes metric SQL
        # against the physical code (e.g. SUM(fact_orders.amount)), so resolving
        # names only would drop such metrics on the return trip. Names take
        # precedence over codes on any collision.
        ds_lc: dict[str, str] = {}
        for ds_name in ds_map:
            ds_lc.setdefault(ds_name.lower(), ds_name)
        for ds_name, ds in ds_map.items():
            # Unquote so a quoted source table (Snowflake/Databricks style,
            # e.g. WH.PUBLIC."fact_orders") is indexed by its bare code.
            table_code = self._unquote_identifier(self._parse_source(ds.get("source", ds_name))[2])
            if table_code:
                ds_lc.setdefault(table_code.lower(), ds_name)

        fields_lc: dict[str, dict[str, str]] = {}
        for ds_name, ds in ds_map.items():
            fmap: dict[str, str] = {}
            osi_fields = ds.get("fields", []) or []
            for f in osi_fields:
                if isinstance(f, dict) and f.get("name"):
                    fmap.setdefault(f["name"].lower(), f["name"])
            for f in osi_fields:
                if isinstance(f, dict) and f.get("name"):
                    code = self._field_expr_identifier(f)
                    if code:
                        fmap.setdefault(code.lower(), f["name"])
            fields_lc[ds_name] = fmap

        for m in osi_metrics:
            name = m["name"]

            osi_description = m.get("description")

            # Extract synonyms from OSI ai_context
            osi_ai_ctx = m.get("ai_context")
            osi_synonyms: list[str] = []
            if isinstance(osi_ai_ctx, dict) and osi_ai_ctx.get("synonyms"):
                osi_synonyms = list(osi_ai_ctx["synonyms"])

            # Restore OBML-only properties from custom_extensions
            obml_extras = self._extract_obml_extras(m)

            # Check for cumulative metric stored in custom_extensions
            if obml_extras.get("obml_metric_type") == "cumulative":
                cum_metric = self._reconstruct_cumulative_metric(
                    name, obml_extras, osi_description, osi_synonyms
                )
                metrics[name] = cum_metric
                continue

            # Check for period-over-period metric stored in custom_extensions
            if obml_extras.get("obml_metric_type") == "period_over_period":
                pop_metric = self._reconstruct_pop_metric(
                    name, obml_extras, osi_description, osi_synonyms
                )
                metrics[name] = pop_metric
                continue

            # Check for window metric (rank/lag/lead/ntile/first_value/last_value)
            if obml_extras.get("obml_metric_type") == "window":
                window_metric = self._reconstruct_window_metric(
                    name, obml_extras, osi_description, osi_synonyms
                )
                metrics[name] = window_metric
                continue

            # Engine-delegated aggregation (Databricks Metric View). Round-trip
            # marker comes from the OBML → OSI direction; on input we restore
            # ``aggregation: measure`` without touching the OSI expression
            # (which is a literal ``MEASURE("<label>")`` with no source column
            # to parse).
            if obml_extras.get("obml_aggregation") == "measure":
                delegated: dict[str, Any] = {"aggregation": "measure"}
                if osi_description:
                    delegated["description"] = osi_description
                if osi_synonyms:
                    delegated["synonyms"] = osi_synonyms
                self._apply_obml_measure_extras(delegated, obml_extras)
                # ``measure`` aggregation forbids columns / expression /
                # filters / total at the model level, so strip anything
                # the extras decoder may have copied across.
                for forbidden in ("filters", "total", "expression"):
                    delegated.pop(forbidden, None)
                measures[name] = delegated
                continue

            # Prefer ANSI_SQL, but also read SNOWFLAKE / DATABRICKS expressions
            # (SQL engines OrionBelt targets) — their aggregations are
            # syntactically ANSI-compatible. Non-SQL dialects (MDX/TABLEAU/MAQL)
            # are not parsed as SQL.
            expr_text, _expr_dialect = self._select_sql_expression(m.get("expression", {}))
            if not expr_text:
                self._preserve_unconverted_metric(
                    m, "no SQL-parseable dialect (ANSI_SQL / SNOWFLAKE / DATABRICKS) expression"
                )
                continue

            # Resolve `dataset.column` references to canonical OSI names
            # (case-insensitive, quote-stripped) before parsing. Copying raw
            # Snowflake/Databricks identifiers verbatim would yield OBML refs to
            # non-existent dataObjects/columns; when a reference cannot be
            # mapped the metric is preserved rather than emitted dangling.
            resolved_expr = self._resolve_column_refs(expr_text, ds_lc, fields_lc)
            if resolved_expr is None:
                self._preserve_unconverted_metric(
                    m, "expression references columns not found in the model"
                )
                continue
            expr_text = resolved_expr

            # Try simple: AGG(dataset.column) or AGG(DISTINCT dataset.column)
            parsed = self._parse_simple_agg(expr_text)
            if parsed:
                agg, dataset, column, is_distinct = parsed
                measure_def: dict[str, Any] = {
                    "columns": [{"dataObject": dataset, "column": column}],
                    "resultType": "float" if agg.upper() in ("SUM", "AVG") else "int",
                    "aggregation": agg.lower(),
                }
                if is_distinct:
                    measure_def["distinct"] = True
                if osi_description:
                    measure_def["description"] = osi_description
                if osi_synonyms:
                    measure_def["synonyms"] = osi_synonyms
                self._apply_obml_measure_extras(measure_def, obml_extras)
                measures[name] = measure_def
                continue

            # Try expression-based: AGG(expr with dataset.column refs)
            parsed_expr = self._parse_expr_agg(expr_text)
            if parsed_expr:
                agg, inner_expr = parsed_expr
                obml_expr = self._sql_refs_to_obml(inner_expr)
                measure_def = {
                    "expression": obml_expr,
                    "resultType": "float" if agg.upper() in ("SUM", "AVG") else "int",
                    "aggregation": agg.lower(),
                }
                if osi_description:
                    measure_def["description"] = osi_description
                if osi_synonyms:
                    measure_def["synonyms"] = osi_synonyms
                self._apply_obml_measure_extras(measure_def, obml_extras)
                measures[name] = measure_def
                continue

            # Complex: multiple aggregations → decompose into measures + metric
            obml_expr, auto_measures = self._decompose_complex_metric(name, expr_text)
            if auto_measures:
                # Deduplicate: if an auto-measure is equivalent to an existing
                # named measure, reuse the named measure in the metric expression
                for auto_key, auto_def in list(auto_measures.items()):
                    for existing_name, existing_def in measures.items():
                        if self._measures_equivalent(auto_def, existing_def):
                            obml_expr = obml_expr.replace(
                                "{[" + auto_key + "]}", "{[" + existing_name + "]}"
                            )
                            del auto_measures[auto_key]
                            break
                measures.update(auto_measures)
                metric_def: dict[str, Any] = {"expression": obml_expr}
                if osi_description:
                    metric_def["description"] = osi_description
                if osi_synonyms:
                    metric_def["synonyms"] = osi_synonyms
                # Restore OBML-only properties for complex metrics
                if obml_extras.get("obml_format"):
                    metric_def["format"] = obml_extras["obml_format"]
                if obml_extras.get("obml_data_type"):
                    metric_def["dataType"] = obml_extras["obml_data_type"]
                if obml_extras.get("obml_owner"):
                    metric_def["owner"] = obml_extras["obml_owner"]
                metrics[name] = metric_def
            else:
                # Expression matched none of simple-agg / expr-agg /
                # complex-decompose. Preserve verbatim instead of dropping so
                # the metric survives an OSI -> OBML -> OSI roundtrip.
                self._preserve_unconverted_metric(
                    m, f"expression not decomposable into OBML measures/metrics: {expr_text!r}"
                )

        # Preserve third-party vendor extensions, carrying them into whichever
        # OBML entity (measure or metric) the OSI metric became.
        for m in osi_metrics:
            target = metrics.get(m["name"]) or measures.get(m["name"])
            if target is not None:
                self._carry_foreign_extensions(m.get("custom_extensions"), target)

        return measures, metrics

    @staticmethod
    def _extract_obml_extras(osi_metric: dict) -> dict:
        """Extract OBML-only properties from OSI metric custom_extensions."""
        for ext in osi_metric.get("custom_extensions", []):
            if ext.get("vendor_name") in _OBML_VENDOR_READ:
                try:
                    data = json.loads(ext.get("data", "{}"))
                    # Check for any obml_ prefixed keys
                    if any(k.startswith("obml_") for k in data):
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
        return {}

    @staticmethod
    def _reconstruct_cumulative_metric(
        name: str,
        extras: dict,
        description: str | None,
        synonyms: list[str],
    ) -> dict:
        """Reconstruct an OBML cumulative metric from custom_extensions data."""
        metric_def: dict[str, Any] = {
            "type": "cumulative",
            "measure": extras["obml_cumulative_measure"],
            "timeDimension": extras["obml_cumulative_time_dimension"],
        }
        cum_type = extras.get("obml_cumulative_type", "sum")
        if cum_type != "sum":
            metric_def["cumulativeType"] = cum_type
        if extras.get("obml_cumulative_window") is not None:
            metric_def["window"] = extras["obml_cumulative_window"]
        if extras.get("obml_cumulative_grain_to_date"):
            metric_def["grainToDate"] = extras["obml_cumulative_grain_to_date"]
        if extras.get("obml_partition_by"):
            metric_def["partitionBy"] = list(extras["obml_partition_by"])
        if description:
            metric_def["description"] = description
        if extras.get("obml_format"):
            metric_def["format"] = extras["obml_format"]
        if extras.get("obml_data_type"):
            metric_def["dataType"] = extras["obml_data_type"]
        if extras.get("obml_owner"):
            metric_def["owner"] = extras["obml_owner"]
        if synonyms:
            metric_def["synonyms"] = synonyms
        return metric_def

    @staticmethod
    def _reconstruct_window_metric(
        name: str,
        extras: dict,
        description: str | None,
        synonyms: list[str],
    ) -> dict:
        """Reconstruct an OBML window metric from custom_extensions data."""
        metric_def: dict[str, Any] = {
            "type": "window",
            "windowFunction": extras["obml_window_function"],
        }
        if extras.get("obml_window_measure"):
            metric_def["measure"] = extras["obml_window_measure"]
        if extras.get("obml_window_time_dimension"):
            metric_def["timeDimension"] = extras["obml_window_time_dimension"]
        if extras.get("obml_window_offset") is not None:
            metric_def["offset"] = extras["obml_window_offset"]
        if extras.get("obml_window_buckets") is not None:
            metric_def["buckets"] = extras["obml_window_buckets"]
        order_dir = extras.get("obml_order_direction", "desc")
        if order_dir != "desc":
            metric_def["orderDirection"] = order_dir
        if extras.get("obml_window_default_value") is not None:
            metric_def["defaultValue"] = extras["obml_window_default_value"]
        if extras.get("obml_partition_by"):
            metric_def["partitionBy"] = list(extras["obml_partition_by"])
        if description:
            metric_def["description"] = description
        if extras.get("obml_format"):
            metric_def["format"] = extras["obml_format"]
        if extras.get("obml_data_type"):
            metric_def["dataType"] = extras["obml_data_type"]
        if extras.get("obml_owner"):
            metric_def["owner"] = extras["obml_owner"]
        if synonyms:
            metric_def["synonyms"] = synonyms
        return metric_def

    @staticmethod
    def _reconstruct_pop_metric(
        name: str,
        extras: dict,
        description: str | None,
        synonyms: list[str],
    ) -> dict:
        """Reconstruct an OBML period-over-period metric from custom_extensions data."""
        pop_config: dict[str, Any] = {
            "timeDimension": extras["obml_pop_time_dimension"],
            "grain": extras["obml_pop_grain"],
            "offsetGrain": extras["obml_pop_offset_grain"],
        }
        offset = extras.get("obml_pop_offset", -1)
        if offset != -1:
            pop_config["offset"] = offset
        comparison = extras.get("obml_pop_comparison", "percentChange")
        if comparison != "percentChange":
            pop_config["comparison"] = comparison

        metric_def: dict[str, Any] = {
            "type": "period_over_period",
            "expression": extras.get("obml_pop_expression", ""),
            "periodOverPeriod": pop_config,
        }
        if description:
            metric_def["description"] = description
        if extras.get("obml_format"):
            metric_def["format"] = extras["obml_format"]
        if extras.get("obml_data_type"):
            metric_def["dataType"] = extras["obml_data_type"]
        if extras.get("obml_owner"):
            metric_def["owner"] = extras["obml_owner"]
        if synonyms:
            metric_def["synonyms"] = synonyms
        return metric_def

    @staticmethod
    def _apply_obml_measure_extras(measure_def: dict, extras: dict) -> None:
        """Restore OBML-only measure properties from extracted extras."""
        if extras.get("obml_filters"):
            measure_def["filters"] = extras["obml_filters"]
        if extras.get("obml_total"):
            measure_def["total"] = True
        if extras.get("obml_allow_fan_out"):
            measure_def["allowFanOut"] = True
        if extras.get("obml_format"):
            measure_def["format"] = extras["obml_format"]
        if extras.get("obml_delimiter"):
            measure_def["delimiter"] = extras["obml_delimiter"]
        if extras.get("obml_within_group"):
            measure_def["withinGroup"] = extras["obml_within_group"]
        if extras.get("obml_data_type"):
            measure_def["dataType"] = extras["obml_data_type"]
        if extras.get("obml_owner"):
            measure_def["owner"] = extras["obml_owner"]
        if extras.get("obml_grain"):
            measure_def["grain"] = extras["obml_grain"]
        if extras.get("obml_filter_context"):
            measure_def["filterContext"] = extras["obml_filter_context"]

    @staticmethod
    def _measures_equivalent(a: dict, b: dict) -> bool:
        """Check if two measure definitions are functionally equivalent."""
        if a.get("aggregation") != b.get("aggregation"):
            return False
        if a.get("distinct", False) != b.get("distinct", False):
            return False
        # Compare column-based measures
        if a.get("columns") and b.get("columns"):
            return a["columns"] == b["columns"]
        # Compare expression-based measures
        if a.get("expression") and b.get("expression"):
            return a["expression"] == b["expression"]
        return False

    def _select_sql_expression(self, expr_obj: dict) -> tuple[str, str]:
        """Pick a SQL-parseable expression from an OSI ``expression`` object.

        Returns ``(expression, dialect)`` for the most preferred SQL dialect
        present (ANSI_SQL > SNOWFLAKE > DATABRICKS), or ``("", "")`` when the
        metric only carries non-SQL dialects (MDX / TABLEAU / MAQL) or no usable
        expression. Catching SNOWFLAKE / DATABRICKS lets third-party models
        whose authors omitted ANSI_SQL still convert, since their aggregation
        syntax is ANSI-compatible.
        """
        if not isinstance(expr_obj, dict):
            return "", ""
        dialects = expr_obj.get("dialects", [])
        by_name = {
            d.get("dialect"): d.get("expression", "")
            for d in dialects
            if isinstance(d, dict) and d.get("expression")
        }
        for dialect in _SQL_PARSEABLE_DIALECTS:
            expr = by_name.get(dialect)
            if expr:
                return expr, dialect
        return "", ""

    @staticmethod
    def _unquote_identifier(ident: str) -> str:
        """Strip SQL identifier quoting (double quotes, backticks, or brackets)."""
        ident = ident.strip()
        if len(ident) >= 2 and (
            (ident[0] == '"' and ident[-1] == '"')
            or (ident[0] == "`" and ident[-1] == "`")
            or (ident[0] == "[" and ident[-1] == "]")
        ):
            return ident[1:-1]
        return ident

    @staticmethod
    def _q_ident(name: str) -> str:
        """Bracket-quote a canonical name that is not a bare SQL word, so the
        downstream ``dataset.column`` parsers (which split on ``\\w+``) do not
        break on display names containing spaces or other punctuation."""
        return name if re.fullmatch(r"\w+", name) else f"[{name}]"

    @staticmethod
    def _field_expr_identifier(field: dict) -> str | None:
        """Physical column code of a field when its expression is a single
        (optionally quoted) identifier, so code-based metric references (e.g.
        ``fact_orders.amount`` or a Snowflake ``"net_amount"``) resolve back to
        the field. Returns ``None`` for computed expressions with no single
        column code.
        """
        expr = field.get("expression")
        if not isinstance(expr, dict):
            return None
        for dialect in expr.get("dialects", []) or []:
            if isinstance(dialect, dict):
                text = dialect.get("expression")
                if isinstance(text, str):
                    candidate = OSItoOBML._unquote_identifier(text)
                    if re.fullmatch(r"[A-Za-z_]\w*", candidate):
                        return candidate
        return None

    def _resolve_column_refs(
        self, expr: str, ds_lc: dict[str, str], fields_lc: dict[str, dict[str, str]]
    ) -> str | None:
        """Rewrite ``dataset.column`` references to canonical OSI names.

        Identifiers are matched case-insensitively and with SQL quoting
        stripped, so Snowflake/Databricks forms such as ``SUM(ORDERS.AMOUNT)``
        or ``SUM("Orders"."amount")`` resolve to the real ``Orders.amount``.
        Returns the rewritten expression, or ``None`` if any reference cannot be
        mapped (the caller then preserves the metric verbatim rather than emit a
        dangling reference that would fail query resolution).
        """
        unresolved = False

        def repl(match: re.Match[str]) -> str:
            nonlocal unresolved
            ds_real = ds_lc.get(self._unquote_identifier(match.group("ds")).lower())
            if ds_real is None:
                unresolved = True
                return match.group(0)
            col_real = fields_lc.get(ds_real, {}).get(
                self._unquote_identifier(match.group("col")).lower()
            )
            if col_real is None:
                unresolved = True
                return match.group(0)
            # Bracket-quote names that are not bare words so the downstream
            # dataset.column parsers keep multi-word display names intact.
            return f"{self._q_ident(ds_real)}.{self._q_ident(col_real)}"

        rewritten = _COLUMN_REF_RE.sub(repl, expr)
        return None if unresolved else rewritten

    def _preserve_unconverted_metric(self, osi_metric: dict, reason: str) -> None:
        """Preserve an OSI metric that has no OBML representation.

        OBML cannot express the metric, but dropping it silently would break
        the README's roundtrip promise. Instead the original OSI metric is kept
        verbatim (re-emitted on OBML -> OSI) and a loud LOSSY warning is raised:
        the metric is preserved but NOT queryable through OBML.
        """
        self._unconverted_metrics.append(osi_metric)
        self.warnings.append(
            f"LOSSY: OSI metric '{osi_metric.get('name', '?')}' has no OBML representation "
            f"({reason}); preserved verbatim for OSI -> OBML -> OSI roundtrip but it is "
            f"NOT queryable in OBML."
        )

    def _parse_simple_agg(self, expr: str) -> tuple | None:
        """
        Parse simple aggregation: AGG(DISTINCT? dataset.column)
        Returns (agg, dataset, column, is_distinct) or None.
        """
        import re

        expr = expr.strip()
        pattern = rf"^(\w+)\(\s*(DISTINCT\s+)?({_RESOLVED_IDENT})\.({_RESOLVED_IDENT})\s*\)$"
        match = re.match(pattern, expr, re.IGNORECASE)
        if match:
            agg = match.group(1)
            is_distinct = match.group(2) is not None
            dataset = self._unquote_identifier(match.group(3))
            column = self._unquote_identifier(match.group(4))
            return agg, dataset, column, is_distinct
        return None

    def _parse_expr_agg(self, expr: str) -> tuple | None:
        """
        Parse expression-based aggregation: AGG(expr containing dataset.column refs)
        E.g. SUM(orders.price * orders.quantity)
        Returns (agg, inner_expression) or None.
        """

        agg_funcs = {"SUM", "COUNT", "AVG", "MIN", "MAX", "ANY_VALUE", "MEDIAN", "MODE", "LISTAGG"}

        expr = expr.strip()
        # Match AGG(...) — must use balanced parentheses
        pattern = r"^(\w+)\(\s*(.+)\s*\)$"
        match = re.match(pattern, expr, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        agg = match.group(1).upper()
        if agg not in agg_funcs:
            return None
        inner = match.group(2).strip()

        # Check balanced parens: the inner must not have unmatched parens
        depth = 0
        for ch in inner:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                return None  # Unmatched close paren → not a single AGG(...)
        if depth != 0:
            return None  # Unmatched open paren

        # Must contain dataset.column references
        if not re.search(rf"{_RESOLVED_IDENT}\.{_RESOLVED_IDENT}", inner):
            return None
        # Must NOT be a simple dataset.column (already handled by _parse_simple_agg)
        if re.match(rf"^(DISTINCT\s+)?{_RESOLVED_IDENT}\.{_RESOLVED_IDENT}$", inner, re.IGNORECASE):
            return None
        # Must NOT contain nested aggregation calls (those are complex metrics)
        if re.search(r"\b(" + "|".join(agg_funcs) + r")\s*\(", inner, re.IGNORECASE):
            return None
        return agg.lower(), inner

    def _sql_refs_to_obml(self, sql_expr: str) -> str:
        """Convert dataset.column references in SQL to OBML {[dataset].[column]} syntax.

        Uses the shared identifier matcher so decimal literals (``1.23``) are not
        mistaken for references; only identifiers that start with a letter or
        underscore are rewritten. By this point references are already canonical
        (resolved upstream), so the bare-identifier branch is what fires.
        """
        return _COLUMN_REF_RE.sub(
            lambda m: (
                "{["
                + self._unquote_identifier(m.group("ds"))
                + "].["
                + self._unquote_identifier(m.group("col"))
                + "]}"
            ),
            sql_expr,
        )

    def _decompose_complex_metric(self, name: str, expr: str) -> tuple[str, dict]:
        """
        Decompose a complex OSI metric expression (multiple aggregations)
        into OBML auto-measures + a metric expression string.

        Handles both simple AGG(dataset.column) and expression-based
        AGG(dataset.col1 * dataset.col2) patterns.

        E.g. SUM(orders.price * orders.quantity) / COUNT(DISTINCT customers.id)
        → auto-measures, metric referencing them via {[name]}
        """

        agg_funcs = {"SUM", "COUNT", "AVG", "MIN", "MAX", "ANY_VALUE", "MEDIAN", "MODE", "LISTAGG"}

        auto_measures: dict[str, Any] = {}
        obml_expr = expr

        # Find all AGG(...) calls with balanced parentheses
        matches = []
        i = 0
        while i < len(expr):
            # Look for WORD( pattern
            m = re.match(r"(\w+)\s*\(", expr[i:])
            if m and m.group(1).upper() in agg_funcs:
                agg = m.group(1)
                start = i
                paren_start = i + m.end() - 1  # position of '('
                # Find matching close paren
                depth = 1
                j = paren_start + 1
                while j < len(expr) and depth > 0:
                    if expr[j] == "(":
                        depth += 1
                    elif expr[j] == ")":
                        depth -= 1
                    j += 1
                if depth == 0:
                    full = expr[start:j]
                    inner = expr[paren_start + 1 : j - 1].strip()
                    matches.append((full, agg, inner))
                    i = j
                    continue
            i += 1

        for full_match, agg, inner in matches:
            # Check for DISTINCT keyword
            is_distinct = False
            inner_clean = inner
            dm = re.match(r"^DISTINCT\s+", inner, re.IGNORECASE)
            if dm:
                is_distinct = True
                inner_clean = inner[dm.end() :].strip()

            # Is it a simple dataset.column?
            simple = re.match(rf"^({_RESOLVED_IDENT})\.({_RESOLVED_IDENT})$", inner_clean)
            if simple:
                dataset = self._unquote_identifier(simple.group(1))
                column = self._unquote_identifier(simple.group(2))
                suffix = "_distinct" if is_distinct else ""
                key_stub = re.sub(r"\W+", "_", f"{dataset}_{column}")
                measure_key = f"_{key_stub}_{agg.lower()}{suffix}"
                measure_def: dict[str, Any] = {
                    "columns": [{"dataObject": dataset, "column": column}],
                    "resultType": "float",
                    "aggregation": agg.lower(),
                }
                if is_distinct:
                    measure_def["distinct"] = True
                auto_measures[measure_key] = measure_def
            else:
                # Expression-based: convert dataset.column refs to OBML syntax
                obml_inner = self._sql_refs_to_obml(inner_clean)
                # Generate a key from the aggregation + hash of expression
                key_suffix = "_distinct" if is_distinct else ""
                # Use a short deterministic key from the expression
                expr_slug = re.sub(r"[^a-zA-Z0-9]", "_", inner_clean)[:40]
                measure_key = f"_{agg.lower()}_{expr_slug}{key_suffix}"
                measure_def = {
                    "expression": obml_inner,
                    "resultType": "float",
                    "aggregation": agg.lower(),
                }
                if is_distinct:
                    measure_def["distinct"] = True
                auto_measures[measure_key] = measure_def

            obml_expr = obml_expr.replace(full_match, "{[" + measure_key + "]}", 1)

        return obml_expr, auto_measures
