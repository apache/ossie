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

"""OBML → OSI conversion (the :class:`OBMLtoOSI` direction).

Extracted verbatim from ``converter.py``; see that module for the package-level
docstring and the shared constants in :mod:`ossie_orionbelt._common`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ossie_orionbelt._common import (
    _INTERNAL_VENDORS,
    _OSI_VENDOR_READ,
    _OSI_VERSION,
    _VENDOR_OBML,
    OBML_TO_OSI_TYPE,
)


class OBMLtoOSI:
    """Convert an OBML semantic model YAML to OSI format."""

    def __init__(
        self,
        obml: dict,
        model_name: str = "semantic_model",
        model_description: str = "",
        ai_instructions: str = "",
    ):
        self.obml = obml
        self.model_name = model_name
        self.model_description = model_description
        self.ai_instructions = ai_instructions
        self.warnings: list[str] = []

    def convert(self) -> dict:
        # Reset warnings so a second convert() call on the same instance does
        # not duplicate them.
        self.warnings = []

        osi: dict[str, Any] = {"version": _OSI_VERSION}

        data_objects = self.obml.get("dataObjects", {})
        obml_dimensions = self.obml.get("dimensions", {})
        obml_measures = self.obml.get("measures", {})
        obml_metrics = self.obml.get("metrics", {})

        # ── Datasets ────────────────────────────────────────────────
        datasets = []
        all_relationships = []

        for do_name, do_obj in data_objects.items():
            dataset, rels = self._convert_data_object(do_name, do_obj, obml_dimensions)
            datasets.append(dataset)
            all_relationships.extend(rels)

        # ── Metrics (OBML measures + metrics → OSI metrics) ────────
        osi_metrics = self._convert_measures_and_metrics(obml_measures, obml_metrics, data_objects)

        # Re-emit OSI metrics that OBML could not represent and that the import
        # path preserved verbatim (vendor OSI, ``obml_unconverted_metrics``).
        # This closes the OSI -> OBML -> OSI roundtrip for non-SQL or
        # non-decomposable metrics.
        self._merge_restored_metrics(osi_metrics)

        # ── Build semantic model ────────────────────────────────────
        sem_model: dict[str, Any] = {"name": self.model_name}
        # Prefer OBML model-level description, fall back to constructor param
        obml_description = self.obml.get("description", "")
        model_desc = obml_description or self.model_description
        if model_desc:
            sem_model["description"] = model_desc
        if self.ai_instructions:
            sem_model["ai_context"] = {"instructions": self.ai_instructions}

        sem_model["datasets"] = datasets

        if all_relationships:
            sem_model["relationships"] = all_relationships

        if osi_metrics:
            sem_model["metrics"] = osi_metrics

        # Add OBML as custom extension for lossless roundtrip info
        roundtrip_data: dict[str, Any] = {
            "source_format": "OBML",
            "source_version": str(self.obml.get("version", "1.0")),
            "converter": "ossie-orionbelt",
        }
        # Preserve model-level static filters for roundtrip
        obml_filters = self.obml.get("filters", [])
        if obml_filters:
            roundtrip_data["obml_filters"] = obml_filters
        # Preserve model settings for roundtrip
        obml_settings = self.obml.get("settings")
        if obml_settings:
            roundtrip_data["obml_settings"] = obml_settings
        # Preserve model owner for roundtrip
        obml_owner = self.obml.get("owner")
        if obml_owner:
            roundtrip_data["obml_owner"] = obml_owner
        # Preserve count-synthesis knobs (OSI has no native equivalent). The
        # synthesized ``<object>.count`` measures themselves are NOT emitted —
        # they are derived and regenerate on load — but the knobs must survive
        # a roundtrip. ``is not None`` so an explicit ``exposeCounts: false`` is
        # preserved (``False`` is falsy).
        expose_counts = self.obml.get("exposeCounts")
        if expose_counts is not None:
            roundtrip_data["obml_expose_counts"] = expose_counts
        count_label_pattern = self.obml.get("countLabelPattern")
        if count_label_pattern is not None:
            roundtrip_data["obml_count_label_pattern"] = count_label_pattern
        sem_model["custom_extensions"] = [
            {
                "vendor_name": _VENDOR_OBML,
                "data": json.dumps(roundtrip_data),
            }
        ]
        # Re-emit third-party model-level vendor extensions verbatim
        self._emit_foreign_extensions(
            self.obml.get("customExtensions"), sem_model["custom_extensions"]
        )

        osi["semantic_model"] = [sem_model]

        # The published OSI core schema forbids root-level ``dialects`` /
        # ``vendors`` (root is additionalProperties:false, only ``version`` +
        # ``semantic_model``). Dialects live per-expression in
        # ``expression.dialects[]`` and vendors per-entity in
        # ``custom_extensions[].vendor_name`` — the schema-valid homes — so the
        # document stays fully conformant without root advertisement arrays.
        # See OSI PR #148 (and the single-document-dialect direction in #52).
        return osi

    def _emit_foreign_extensions(self, obml_exts: list[dict] | None, osi_exts: list[dict]) -> None:
        """Re-emit third-party OBML customExtensions as OSI custom_extensions.

        Mirrors ``OSItoOBML._carry_foreign_extensions``: extensions from a
        vendor we do not handle internally are passed back to OSI under their
        original vendor name, completing the roundtrip.
        """
        for ext in obml_exts or []:
            vendor = ext.get("vendor")
            if vendor and vendor not in _INTERNAL_VENDORS:
                osi_exts.append({"vendor_name": vendor, "data": ext.get("data", "")})

    def _convert_data_object(
        self, do_name: str, do_obj: dict, obml_dimensions: dict
    ) -> tuple[dict, list]:
        """Convert an OBML dataObject to an OSI dataset + relationships."""
        database = do_obj.get("database", "")
        schema = do_obj.get("schema", "")
        code = do_obj.get("code", "")
        source = f"{database}.{schema}.{code}" if database else code

        # Use the OBML display name as the OSI dataset name so that
        # relationship references (joinTo) stay consistent in roundtrips
        osi_name = do_name

        dataset: dict[str, Any] = {
            "name": osi_name,
            "source": source,
        }

        # ── Primary key (v0.2 first-class) ──────────────────────────
        # Collect columns flagged with ``primaryKey: true`` in OBML order
        # (TrackedLoader / Python dict preserves declaration order, which
        # is significant for composite PKs).
        pk_columns = [
            col_name
            for col_name, col in (do_obj.get("columns", {}) or {}).items()
            if col.get("primaryKey")
        ]
        # Use the physical ``code`` for each column when present — OSI
        # field names mirror the physical column code (see _convert_column).
        if pk_columns:
            columns_map = do_obj.get("columns", {}) or {}
            dataset["primary_key"] = [
                columns_map[c].get("code", c.lower().replace(" ", "_")) for c in pk_columns
            ]

        # ── Unique keys (v0.2 first-class, lossless roundtrip via OBSL) ──
        # OBML doesn't model unique keys natively today; round-trip via the
        # ``OBSL``-vendor ``obml_unique_keys`` payload that originated from
        # a prior OSI → OBML conversion (or hand-authored OBML).
        unique_keys_extra: list[list[str]] | None = None
        for ext in do_obj.get("customExtensions", []) or []:
            if ext.get("vendor") not in _OSI_VENDOR_READ:
                continue
            try:
                data = json.loads(ext.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            uk = data.get("obml_unique_keys")
            if isinstance(uk, list) and all(isinstance(g, list) for g in uk):
                unique_keys_extra = [list(g) for g in uk]
                break
        if unique_keys_extra:
            dataset["unique_keys"] = unique_keys_extra

        if do_obj.get("description"):
            dataset["description"] = do_obj["description"]
        elif do_obj.get("comment"):
            dataset["description"] = do_obj["comment"]

        # ── Rebuild ai_context: native synonyms + remaining from customExtensions
        ai_ctx: dict[str, Any] = {}
        for ext in do_obj.get("customExtensions", []):
            if ext.get("vendor") == "OSI":
                try:
                    ai_data = json.loads(ext.get("data", "{}"))
                    if ai_data:
                        ai_ctx.update(ai_data)
                except (json.JSONDecodeError, TypeError):
                    pass
        # Merge native OBML synonyms into ai_context.synonyms
        obml_synonyms = do_obj.get("synonyms", [])
        if obml_synonyms:
            existing = ai_ctx.get("synonyms", [])
            merged = list(existing) + [s for s in obml_synonyms if s not in existing]
            ai_ctx["synonyms"] = merged
        if ai_ctx:
            dataset["ai_context"] = ai_ctx

        # ── Fields ──────────────────────────────────────────────────
        fields = []
        columns = do_obj.get("columns", {})
        for col_name, col_obj in columns.items():
            field = self._convert_column(col_name, col_obj, do_name, obml_dimensions)
            fields.append(field)

        if fields:
            dataset["fields"] = fields

        # ── Relationships (from OBML joins) ─────────────────────────
        relationships = []
        joins = do_obj.get("joins", [])
        for i, join in enumerate(joins):
            rel = self._convert_join_to_relationship(osi_name, do_name, do_obj, join, i)
            if rel:
                relationships.append(rel)

        # ── Preserve DataObject owner/comment + refresh in custom_extensions ──
        do_extras: dict[str, Any] = {}
        if do_obj.get("owner"):
            do_extras["obml_owner"] = do_obj["owner"]
        if do_obj.get("comment"):
            do_extras["obml_comment"] = do_obj["comment"]
        # OBML-only freshness contract — round-tripped through OSI
        # custom_extensions since OSI has no native equivalent. See
        # design/PLAN_freshness_driven_cache.md §5.
        if do_obj.get("refresh"):
            do_extras["obml_refresh"] = do_obj["refresh"]
        # Count-synthesis knobs (``is not None`` so ``countable: false`` survives).
        if do_obj.get("countable") is not None:
            do_extras["obml_countable"] = do_obj["countable"]
        if do_obj.get("countLabel") is not None:
            do_extras["obml_count_label"] = do_obj["countLabel"]
        if do_extras:
            ds_exts = dataset.setdefault("custom_extensions", [])
            ds_exts.append(
                {
                    "vendor_name": _VENDOR_OBML,
                    "data": json.dumps(do_extras),
                }
            )

        # Re-emit third-party vendor extensions verbatim
        self._emit_foreign_extensions(
            do_obj.get("customExtensions"), dataset.setdefault("custom_extensions", [])
        )
        if not dataset["custom_extensions"]:
            del dataset["custom_extensions"]

        return dataset, relationships

    def _convert_column(
        self, col_name: str, col_obj: dict, do_name: str, obml_dimensions: dict
    ) -> dict:
        """Convert an OBML column to an OSI field."""
        code = col_obj.get("code", col_name.lower().replace(" ", "_"))

        field: dict[str, Any] = {
            "name": code,
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": code,
                    }
                ]
            },
        }

        # Check if this column is used as a dimension
        is_dimension = False
        is_time = False
        synonyms = []

        for dim_name, dim_obj in obml_dimensions.items():
            if dim_obj.get("dataObject") == do_name and dim_obj.get("column") == col_name:
                is_dimension = True
                if dim_obj.get("resultType") in ("date", "time", "timestamp", "timestamp_tz"):
                    is_time = True
                # The dimension display name is a synonym
                if dim_name != col_name:
                    synonyms.append(dim_name)
                break

        abstract_type = col_obj.get("abstractType", "string")
        if abstract_type in ("date", "timestamp", "timestamp_tz"):
            is_time = True

        if is_dimension or is_time:
            field["dimension"] = {"is_time": is_time}

        if col_obj.get("description"):
            field["description"] = col_obj["description"]
        elif col_obj.get("comment"):
            field["description"] = col_obj["comment"]
        else:
            field["description"] = col_name  # Use display name as description

        # ── Field label (OSI v0.2 first-class) ──
        # Surfaced from OBSL-vendor customExtensions ``obml_field_label`` —
        # round-trip path for OSI → OBML → OSI fidelity. OBML has no
        # native column ``label`` today.
        for ext in col_obj.get("customExtensions", []) or []:
            if ext.get("vendor") not in _OSI_VENDOR_READ:
                continue
            try:
                ext_label_data = json.loads(ext.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            if ext_label_data.get("obml_field_label"):
                field["label"] = ext_label_data["obml_field_label"]
                break

        # Restore ai_context from customExtensions (OSI vendor) if present
        ai_ctx: dict[str, Any] = {}
        for ext in col_obj.get("customExtensions", []):
            if ext.get("vendor") == "OSI":
                try:
                    ai_data = json.loads(ext.get("data", "{}"))
                    if ai_data:
                        ai_ctx.update(ai_data)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Merge native OBML column synonyms into ai_context
        obml_col_synonyms = col_obj.get("synonyms", [])
        if obml_col_synonyms:
            existing = ai_ctx.get("synonyms", [])
            merged = list(existing) + [s for s in obml_col_synonyms if s not in existing]
            ai_ctx["synonyms"] = merged

        # Build ai_context with synonyms from display name
        display_synonym = col_name.lower()
        code_clean = code.lower()
        if display_synonym != code_clean:
            synonyms.insert(0, col_name)
        if synonyms:
            ai_ctx.setdefault("synonyms", []).extend(
                s for s in synonyms if s not in ai_ctx.get("synonyms", [])
            )
        if ai_ctx:
            field["ai_context"] = ai_ctx

        # Preserve OBML type info in custom_extensions for roundtrip fidelity
        abstract_type = col_obj.get("abstractType", "string")
        osi_type = OBML_TO_OSI_TYPE.get(abstract_type, "string")
        ext_data: dict[str, Any] = {
            "data_type": osi_type,
            "obml_abstract_type": abstract_type,
        }
        # Preserve OBML-only column properties
        if col_obj.get("sqlType"):
            ext_data["obml_sql_type"] = col_obj["sqlType"]
        if col_obj.get("sqlPrecision") is not None:
            ext_data["obml_sql_precision"] = col_obj["sqlPrecision"]
        if col_obj.get("sqlScale") is not None:
            ext_data["obml_sql_scale"] = col_obj["sqlScale"]
        if col_obj.get("numClass"):
            ext_data["obml_num_class"] = col_obj["numClass"]
        if col_obj.get("comment"):
            ext_data["obml_comment"] = col_obj["comment"]
        if col_obj.get("owner"):
            ext_data["obml_owner"] = col_obj["owner"]
        # Preserve OBML-only dimension properties (timeGrain, format, resultType, etc.).
        # OBML allows N dimensions over one column (grain variants, role-playing
        # via ``via``); OSI is field-centric (one dimension per field). The first
        # match is the primary dimension carried on the field; any extras are
        # preserved as descriptors so the reverse trip can rebuild them instead of
        # dropping them silently.
        matched_dim: dict[str, Any] | None = None
        extra_dims: list[dict[str, Any]] = []
        for _dim_name, dim_obj in obml_dimensions.items():
            if dim_obj.get("dataObject") != do_name or dim_obj.get("column") != col_name:
                continue
            if matched_dim is None:
                matched_dim = dim_obj
                # Preserve the dimension's OBML name explicitly. The OSI field
                # name is the physical code, so without this the round-trip
                # would rename the dimension to its column code. Authoritative
                # (do not rely on synonyms, which mix names and user aliases).
                ext_data["obml_dimension_name"] = _dim_name
                if dim_obj.get("timeGrain"):
                    ext_data["obml_time_grain"] = dim_obj["timeGrain"]
                if dim_obj.get("format"):
                    ext_data["obml_dimension_format"] = dim_obj["format"]
                if dim_obj.get("resultType"):
                    ext_data["obml_dimension_result_type"] = dim_obj["resultType"]
                if dim_obj.get("description"):
                    ext_data["obml_dimension_description"] = dim_obj["description"]
                if dim_obj.get("owner"):
                    ext_data["obml_dimension_owner"] = dim_obj["owner"]
                if dim_obj.get("via"):
                    ext_data["obml_dimension_via"] = dim_obj["via"]
                # The dimension's own synonyms and vendor extensions have no
                # native OSI slot (OSI has no dimension entity), so preserve them
                # authoritatively here for the reverse trip. The customExtensions
                # are also emitted as field foreign extensions below for OSI-tool
                # visibility; that path lands them on the column on re-import,
                # while this one restores them to the dimension.
                if dim_obj.get("synonyms"):
                    ext_data["obml_dimension_synonyms"] = dim_obj["synonyms"]
                if dim_obj.get("customExtensions"):
                    ext_data["obml_dimension_custom_extensions"] = dim_obj["customExtensions"]
            else:
                descriptor: dict[str, Any] = {"name": _dim_name}
                for prop in ("resultType", "timeGrain", "format", "description", "owner", "via"):
                    if dim_obj.get(prop):
                        descriptor[prop] = dim_obj[prop]
                # Carry the extra dimension's own synonyms and vendor extensions
                # too, so it round-trips with the same fidelity as the primary.
                if dim_obj.get("synonyms"):
                    descriptor["synonyms"] = dim_obj["synonyms"]
                if dim_obj.get("customExtensions"):
                    descriptor["customExtensions"] = dim_obj["customExtensions"]
                extra_dims.append(descriptor)
        if extra_dims:
            ext_data["obml_extra_dimensions"] = extra_dims
            extra_names = ", ".join(d["name"] for d in extra_dims)
            self.warnings.append(
                f"Column '{do_name}.{col_name}' backs {len(extra_dims) + 1} OBML "
                f"dimensions; OSI represents one dimension per field, so the "
                f"{len(extra_dims)} additional dimension(s) ({extra_names}) are "
                f"preserved via an OBSL extension for the reverse conversion but "
                f"are not natively visible to other OSI tools."
            )
        field["custom_extensions"] = [
            {
                "vendor_name": _VENDOR_OBML,
                "data": json.dumps(ext_data),
            }
        ]

        # Re-emit third-party vendor extensions verbatim. OSI has no separate
        # dimension entity, so a matched dimension's foreign extensions surface
        # on the field too (they re-import onto the column).
        self._emit_foreign_extensions(col_obj.get("customExtensions"), field["custom_extensions"])
        if matched_dim is not None:
            self._emit_foreign_extensions(
                matched_dim.get("customExtensions"), field["custom_extensions"]
            )

        return field

    def _convert_join_to_relationship(
        self, osi_from_name: str, _obml_from_name: str, from_do: dict, join: dict, index: int
    ) -> dict | None:
        """Convert an OBML join to an OSI relationship."""
        join_to_display = join.get("joinTo", "")
        # Use the OBML display name as the OSI target name (consistent with
        # _convert_data_object which uses display name as OSI dataset name)
        to_name = join_to_display
        target_do = self.obml.get("dataObjects", {}).get(join_to_display, {})

        # Map column display names to codes
        from_columns_display = join.get("columnsFrom", [])
        to_columns_display = join.get("columnsTo", [])

        from_cols = self._resolve_column_codes(from_do, from_columns_display)
        to_cols = self._resolve_column_codes(target_do, to_columns_display)

        # Generate relationship name
        path_name = join.get("pathName", "")
        if path_name:
            rel_name = f"{osi_from_name}_to_{to_name}_{path_name}"
        else:
            rel_name = f"{osi_from_name}_to_{to_name}"
            if index > 0:
                rel_name += f"_{index}"

        rel: dict[str, Any] = {
            "name": rel_name,
            "from": osi_from_name,
            "to": to_name,
            "from_columns": from_cols,
            "to_columns": to_cols,
        }

        # Preserve secondary join info in ai_context
        if join.get("secondary"):
            rel["ai_context"] = {
                "instructions": (
                    f"Secondary/alternative join path"
                    f"{(' named: ' + path_name) if path_name else ''}. "
                    f"Use only when explicitly needed."
                )
            }

        return rel

    def _resolve_column_codes(self, do_obj: dict, col_display_names: list) -> list:
        """Resolve OBML column display names to their code values."""
        columns = do_obj.get("columns", {})
        codes = []
        for display in col_display_names:
            col = columns.get(display, {})
            codes.append(col.get("code", display.lower().replace(" ", "_")))
        return codes

    def _restore_unconverted_metrics(self) -> list[dict]:
        """Recover OSI metrics preserved verbatim during OSI -> OBML import.

        The import path stashes metrics OBML can't represent under an OSI-vendor
        model-level customExtension (``obml_unconverted_metrics``). Re-emit them
        unchanged so a full OSI -> OBML -> OSI roundtrip keeps them.
        """
        restored: list[dict] = []
        for ext in self.obml.get("customExtensions", []) or []:
            if ext.get("vendor") not in _OSI_VENDOR_READ:
                continue
            try:
                data = json.loads(ext.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            preserved = data.get("obml_unconverted_metrics")
            if isinstance(preserved, list):
                restored.extend(m for m in preserved if isinstance(m, dict))
        return restored

    def _merge_restored_metrics(self, osi_metrics: list[dict]) -> None:
        """Append preserved (unconverted) OSI metrics to the converted ones.

        Name-collision guard: if a queryable OBML measure/metric now owns a name
        a stale preserved metric also uses, skip the preserved copy (the real
        metric wins) so the OSI output has no duplicate metric names and passes
        semantic validation. Each appended metric carries its own dialects
        (``expression.dialects[]``) and vendors (``custom_extensions``)
        verbatim, which are the schema-valid homes for that metadata.
        """
        existing = {m.get("name") for m in osi_metrics if isinstance(m, dict)}
        for restored in self._restore_unconverted_metrics():
            name = restored.get("name")
            if name in existing:
                self.warnings.append(
                    f"Preserved OSI metric '{name}' dropped on export: a converted "
                    f"OBML metric now uses that name."
                )
                continue
            existing.add(name)
            osi_metrics.append(restored)

    def _convert_measures_and_metrics(
        self, obml_measures: dict, obml_metrics: dict, data_objects: dict
    ) -> list:
        """Convert OBML measures and metrics to OSI metrics."""
        osi_metrics = []

        # Convert each OBML measure to an OSI metric
        for measure_name, measure_obj in obml_measures.items():
            osi_metric = self._convert_measure(measure_name, measure_obj, data_objects)
            if osi_metric:
                self._carry_foreign_to_osi_metric(measure_obj, osi_metric)
                osi_metrics.append(osi_metric)

        # Convert OBML metrics (which reference measures) to OSI metrics
        for metric_name, metric_obj in obml_metrics.items():
            if metric_obj.get("type") == "cumulative":
                osi_metric = self._convert_obml_cumulative_metric(
                    metric_name, metric_obj, obml_measures, data_objects
                )
            elif metric_obj.get("type") == "period_over_period":
                osi_metric = self._convert_obml_pop_metric(
                    metric_name, metric_obj, obml_measures, data_objects
                )
            elif metric_obj.get("type") == "window":
                osi_metric = self._convert_obml_window_metric(
                    metric_name, metric_obj, obml_measures, data_objects
                )
            else:
                osi_metric = self._convert_obml_metric(
                    metric_name, metric_obj, obml_measures, data_objects
                )
            if osi_metric:
                self._carry_foreign_to_osi_metric(metric_obj, osi_metric)
                osi_metrics.append(osi_metric)

        return osi_metrics

    def _carry_foreign_to_osi_metric(self, obml_obj: dict, osi_metric: dict) -> None:
        """Re-emit third-party vendor extensions on an OBML measure/metric to
        the OSI metric, dropping the key again if nothing foreign was added."""
        self._emit_foreign_extensions(
            obml_obj.get("customExtensions"), osi_metric.setdefault("custom_extensions", [])
        )
        if not osi_metric["custom_extensions"]:
            del osi_metric["custom_extensions"]

    def _convert_measure(self, name: str, measure: dict, data_objects: dict) -> dict | None:
        """Convert an OBML measure to an OSI metric."""

        columns = measure.get("columns", [])
        agg = measure.get("aggregation", "sum").upper()
        distinct = measure.get("distinct", False)
        obml_synonyms = measure.get("synonyms", [])

        # Build ai_context with synonyms (name + native OBML synonyms)
        ai_synonyms = [name] + [s for s in obml_synonyms if s != name]
        ai_ctx: dict[str, Any] = {"synonyms": ai_synonyms} if ai_synonyms else {}

        # ``aggregation: measure`` delegates resolution to the engine
        # (Databricks Metric View) — there is no source column to read
        # and no ANSI_SQL expression to emit. OSI has no first-class
        # concept for engine-delegated aggregation, so we serialize the
        # measure as an OSI metric whose expression is the literal
        # ``MEASURE("<label>")`` call and merge the OBML signal into
        # the standard extras-blob so the reverse direction restores
        # ``aggregation: measure`` in a single round-trip.
        if agg == "MEASURE":
            expr = f'MEASURE("{name}")'
            measure_metric: dict[str, Any] = {
                "name": name,
                "expression": {
                    "dialects": [
                        {
                            "dialect": "ANSI_SQL",
                            "expression": expr,
                        }
                    ]
                },
                "description": measure.get("description", name),
            }
            if ai_ctx:
                measure_metric["ai_context"] = ai_ctx
            self._add_obml_measure_extras(
                measure_metric, {**measure, "_extra_obml_aggregation": "measure"}
            )
            return measure_metric

        if not columns:
            obml_expr = measure.get("expression", "")
            if obml_expr:
                # Expression-based measure: convert {[DO].[Col]} refs to SQL
                sql_inner = self._obml_refs_to_sql(obml_expr, data_objects)
                distinct_kw = "DISTINCT " if distinct else ""
                expr = f"{agg}({distinct_kw}{sql_inner})"
                result: dict[str, Any] = {
                    "name": name,
                    "expression": {
                        "dialects": [
                            {
                                "dialect": "ANSI_SQL",
                                "expression": expr,
                            }
                        ]
                    },
                    "description": measure.get("description", name),
                }
                if ai_ctx:
                    result["ai_context"] = ai_ctx
                self._add_obml_measure_extras(result, measure)
                return result
            self.warnings.append(f"Measure '{name}' has no columns; skipped.")
            return None

        # Build SQL expression from one-or-more column references.
        # Multi-column aggregations (CORR, COVAR_*, REGR_*) emit
        # ``AGG(col_a, col_b)`` with arguments in declaration order.
        def _col_to_sql(col_ref: dict) -> str:
            do_name_local = col_ref.get("dataObject", "")
            col_name_local = col_ref.get("column", "")
            do_obj_local = data_objects.get(do_name_local, {})
            col_code_local = (
                do_obj_local.get("columns", {})
                .get(col_name_local, {})
                .get("code", col_name_local.lower().replace(" ", "_"))
            )
            do_code_local = do_obj_local.get("code", do_name_local.lower().replace(" ", "_"))
            return f"{do_code_local}.{col_code_local}"

        distinct_kw = "DISTINCT " if distinct else ""
        col_sql = ", ".join(_col_to_sql(c) for c in columns)
        expr = f"{agg}({distinct_kw}{col_sql})"

        result = {
            "name": name,
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": expr,
                    }
                ]
            },
            "description": measure.get("description", name),
        }
        if ai_ctx:
            result["ai_context"] = ai_ctx
        self._add_obml_measure_extras(result, measure)
        return result

    @staticmethod
    def _add_obml_measure_extras(result: dict, measure: dict) -> None:
        """Preserve OBML-only measure properties in custom_extensions for roundtrip."""
        extras: dict[str, Any] = {}
        if measure.get("filters"):
            extras["obml_filters"] = measure["filters"]
        if measure.get("total"):
            extras["obml_total"] = True
        if measure.get("allowFanOut"):
            extras["obml_allow_fan_out"] = True
        if measure.get("format"):
            extras["obml_format"] = measure["format"]
        if measure.get("delimiter"):
            extras["obml_delimiter"] = measure["delimiter"]
        if measure.get("withinGroup"):
            extras["obml_within_group"] = measure["withinGroup"]
        if measure.get("dataType"):
            extras["obml_data_type"] = measure["dataType"]
        if measure.get("owner"):
            extras["obml_owner"] = measure["owner"]
        if measure.get("grain"):
            extras["obml_grain"] = measure["grain"]
        if measure.get("filterContext"):
            extras["obml_filter_context"] = measure["filterContext"]
        # Internal pass-through marker for callers that need to inject an
        # extra obml_* key without growing the parameter surface (e.g.
        # ``aggregation: measure`` round-trips ``obml_aggregation``).
        if measure.get("_extra_obml_aggregation"):
            extras["obml_aggregation"] = measure["_extra_obml_aggregation"]
        if extras:
            exts = result.setdefault("custom_extensions", [])
            exts.append(
                {
                    "vendor_name": _VENDOR_OBML,
                    "data": json.dumps(extras),
                }
            )

    def _obml_refs_to_sql(self, obml_expr: str, data_objects: dict) -> str:
        """Convert OBML {[DataObject].[Column]} references to SQL dataset.column."""
        import re

        def replace_ref(match: re.Match) -> str:
            do_name = match.group(1)
            col_name = match.group(2)
            do_obj = data_objects.get(do_name, {})
            do_code = do_obj.get("code", do_name.lower().replace(" ", "_"))
            col_code = (
                do_obj.get("columns", {})
                .get(col_name, {})
                .get("code", col_name.lower().replace(" ", "_"))
            )
            return f"{do_code}.{col_code}"

        return re.sub(r"\{\[([^\]]+)\]\.\[([^\]]+)\]\}", replace_ref, obml_expr)

    def _convert_obml_metric(
        self, name: str, metric: dict, obml_measures: dict, data_objects: dict
    ) -> dict | None:
        """
        Convert an OBML metric (expression referencing measures) to OSI metric.
        OBML metric expression: "{[Total Sales]} / {[Sales Count]}"
        → needs to be expanded to SQL using the measure definitions.
        """

        expr_template = metric.get("expression", "")
        if not expr_template:
            return None

        sql_expr = expr_template

        # Replace {[Measure Name]} references with SQL expressions
        pattern = r"\{\[([^\]]+)\]\}"
        for match in re.finditer(pattern, expr_template):
            measure_name = match.group(1)
            measure_def = obml_measures.get(measure_name, {})

            if not measure_def:
                self.warnings.append(
                    f"Metric '{name}' references unknown measure '{measure_name}'."
                )
                continue

            sql_part = self._measure_to_sql(measure_def, data_objects)
            if sql_part:
                sql_expr = sql_expr.replace(match.group(0), sql_part)
            else:
                self.warnings.append(
                    f"Metric '{name}': could not convert measure '{measure_name}' to SQL."
                )

        result: dict[str, Any] = {
            "name": name,
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": sql_expr,
                    }
                ]
            },
            "description": metric.get("description", name),
        }
        # Include OBML synonyms in ai_context
        obml_synonyms = metric.get("synonyms", [])
        ai_synonyms = [s for s in obml_synonyms if s != name]
        if ai_synonyms:
            result["ai_context"] = {"synonyms": ai_synonyms}
        # Preserve OBML-only metric properties in custom_extensions
        metric_extras: dict[str, Any] = {}
        if metric.get("format"):
            metric_extras["obml_format"] = metric["format"]
        if metric.get("dataType"):
            metric_extras["obml_data_type"] = metric["dataType"]
        if metric.get("owner"):
            metric_extras["obml_owner"] = metric["owner"]
        if metric_extras:
            exts = result.setdefault("custom_extensions", [])
            exts.append(
                {
                    "vendor_name": _VENDOR_OBML,
                    "data": json.dumps(metric_extras),
                }
            )
        return result

    def _convert_obml_cumulative_metric(
        self,
        name: str,
        metric: dict,
        obml_measures: dict,
        data_objects: dict,
    ) -> dict | None:
        """Convert an OBML cumulative metric to an OSI metric.

        Cumulative metrics have no direct OSI equivalent.  We generate an
        approximate SQL expression for readability and store the full OBML
        cumulative configuration in ``custom_extensions`` (vendor ``COMMON``)
        so the OSI → OBML direction can reconstruct it losslessly.
        """
        measure_name = metric.get("measure", "")
        time_dim = metric.get("timeDimension", "")
        cum_type = metric.get("cumulativeType", "sum").upper()
        window_size = metric.get("window")
        grain = metric.get("grainToDate")

        if not measure_name:
            self.warnings.append(f"Cumulative metric '{name}' has no measure reference; skipped.")
            return None

        # Build an approximate SQL expression for the OSI metric
        measure_def = obml_measures.get(measure_name, {})
        inner_sql = self._measure_to_sql(measure_def, data_objects) or measure_name

        if window_size is not None:
            frame = (
                f' OVER (ORDER BY "{time_dim}" '
                f"ROWS BETWEEN {window_size - 1} PRECEDING AND CURRENT ROW)"
            )
        elif grain:
            frame = (
                f' OVER (PARTITION BY DATE_TRUNC(\'{grain}\', "{time_dim}") ORDER BY "{time_dim}")'
            )
        else:
            # Running total (unbounded)
            frame = f' OVER (ORDER BY "{time_dim}" ROWS UNBOUNDED PRECEDING)'

        sql_expr = f"{cum_type}({inner_sql}){frame}"

        result: dict[str, Any] = {
            "name": name,
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": sql_expr,
                    }
                ]
            },
            "description": metric.get("description", name),
        }

        obml_synonyms = metric.get("synonyms", [])
        ai_synonyms = [s for s in obml_synonyms if s != name]
        if ai_synonyms:
            result["ai_context"] = {"synonyms": ai_synonyms}

        # Store full cumulative config in custom_extensions for roundtrip
        ext_data: dict[str, Any] = {
            "obml_metric_type": "cumulative",
            "obml_cumulative_measure": measure_name,
            "obml_cumulative_time_dimension": time_dim,
            "obml_cumulative_type": metric.get("cumulativeType", "sum"),
        }
        if window_size is not None:
            ext_data["obml_cumulative_window"] = window_size
        if grain:
            ext_data["obml_cumulative_grain_to_date"] = grain
        if metric.get("partitionBy"):
            ext_data["obml_partition_by"] = list(metric["partitionBy"])
        if metric.get("format"):
            ext_data["obml_format"] = metric["format"]
        if metric.get("dataType"):
            ext_data["obml_data_type"] = metric["dataType"]
        if metric.get("owner"):
            ext_data["obml_owner"] = metric["owner"]

        result["custom_extensions"] = [
            {
                "vendor_name": _VENDOR_OBML,
                "data": json.dumps(ext_data),
            }
        ]

        return result

    def _convert_obml_pop_metric(
        self,
        name: str,
        metric: dict,
        obml_measures: dict,
        data_objects: dict,
    ) -> dict | None:
        """Convert an OBML period-over-period metric to an OSI metric.

        PoP metrics have no direct OSI equivalent.  We generate an
        approximate SQL expression for readability and store the full OBML
        PoP configuration in ``custom_extensions`` (vendor ``COMMON``)
        so the OSI → OBML direction can reconstruct it losslessly.
        """
        pop_config = metric.get("periodOverPeriod", {})
        expr_template = metric.get("expression", "")
        if not pop_config or not expr_template:
            self.warnings.append(
                f"Period-over-period metric '{name}' missing configuration; skipped."
            )
            return None

        time_dim = pop_config.get("timeDimension", "")
        grain = pop_config.get("grain", "month")
        offset = pop_config.get("offset", -1)
        offset_grain = pop_config.get("offsetGrain", "year")
        comparison = pop_config.get("comparison", "percentChange")

        # Resolve the base measure SQL for an approximate expression
        pattern = r"\{\[([^\]]+)\]\}"
        measure_names = re.findall(pattern, expr_template)
        base_measure = measure_names[0] if measure_names else "measure"
        measure_def = obml_measures.get(base_measure, {})
        inner_sql = self._measure_to_sql(measure_def, data_objects) or base_measure

        # Build approximate SQL comment-style expression
        comparison_map = {
            "percentChange": f"({inner_sql} / NULLIF(prev.value, 0)) - 1",
            "ratio": f"{inner_sql} / NULLIF(prev.value, 0)",
            "difference": f"{inner_sql} - prev.value",
            "previousValue": "prev.value",
        }
        sql_expr = comparison_map.get(comparison, f"{inner_sql} -- PoP({comparison})")

        result: dict[str, Any] = {
            "name": name,
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": sql_expr,
                    }
                ]
            },
            "description": metric.get("description", name),
        }

        obml_synonyms = metric.get("synonyms", [])
        ai_synonyms = [s for s in obml_synonyms if s != name]
        if ai_synonyms:
            result["ai_context"] = {"synonyms": ai_synonyms}

        # Store full PoP config in custom_extensions for roundtrip
        ext_data: dict[str, Any] = {
            "obml_metric_type": "period_over_period",
            "obml_pop_expression": expr_template,
            "obml_pop_time_dimension": time_dim,
            "obml_pop_grain": grain,
            "obml_pop_offset": offset,
            "obml_pop_offset_grain": offset_grain,
            "obml_pop_comparison": comparison,
        }
        if metric.get("format"):
            ext_data["obml_format"] = metric["format"]
        if metric.get("dataType"):
            ext_data["obml_data_type"] = metric["dataType"]
        if metric.get("owner"):
            ext_data["obml_owner"] = metric["owner"]

        result["custom_extensions"] = [
            {
                "vendor_name": _VENDOR_OBML,
                "data": json.dumps(ext_data),
            }
        ]

        return result

    def _convert_obml_window_metric(
        self,
        name: str,
        metric: dict,
        obml_measures: dict,
        data_objects: dict,
    ) -> dict | None:
        """Convert an OBML window metric (rank/lag/lead/ntile/...) to an OSI metric.

        Window metrics have no direct OSI equivalent. We generate an
        approximate ANSI SQL expression for readability and persist the
        full OBML window configuration in ``custom_extensions`` (vendor
        ``COMMON``) for lossless OSI → OBML reconstruction.
        """
        window_fn = (metric.get("windowFunction") or "").upper()
        if not window_fn:
            self.warnings.append(f"Window metric '{name}' has no windowFunction; skipped.")
            return None

        measure_name = metric.get("measure")
        time_dim = metric.get("timeDimension")
        order_dir = metric.get("orderDirection", "desc").upper()
        offset = metric.get("offset")
        buckets = metric.get("buckets")
        default_value = metric.get("defaultValue")
        partition_by = metric.get("partitionBy", []) or []

        measure_def = obml_measures.get(measure_name, {}) if measure_name else {}
        inner_sql = (
            self._measure_to_sql(measure_def, data_objects) if measure_def else (measure_name or "")
        )

        # Build approximate ANSI SQL expression
        args: list[str] = []
        order_expr: str | None = None
        if window_fn in {"LAG", "LEAD"} and inner_sql:
            args.append(inner_sql)
            if offset is not None:
                args.append(str(offset))
            if default_value is not None:
                args.append(
                    f"'{default_value}'" if isinstance(default_value, str) else str(default_value)
                )
        elif window_fn == "NTILE" and buckets is not None:
            args.append(str(buckets))
        elif window_fn in {"FIRST_VALUE", "LAST_VALUE"} and inner_sql:
            args.append(inner_sql)
        # RANK / DENSE_RANK / ROW_NUMBER take no positional args

        if window_fn in {"RANK", "DENSE_RANK", "ROW_NUMBER", "NTILE"} and inner_sql:
            order_expr = f"{inner_sql} {order_dir}"
        elif window_fn in {"LAG", "LEAD"} and time_dim:
            order_expr = f'"{time_dim}"'
        elif window_fn in {"FIRST_VALUE", "LAST_VALUE"} and time_dim:
            order_expr = f'"{time_dim}" {order_dir}'

        partition_sql = (
            "PARTITION BY " + ", ".join(f'"{p}"' for p in partition_by) if partition_by else ""
        )
        order_sql = f"ORDER BY {order_expr}" if order_expr else ""
        over_inner = " ".join(p for p in (partition_sql, order_sql) if p)
        sql_expr = f"{window_fn}({', '.join(args)}) OVER ({over_inner})"

        result: dict[str, Any] = {
            "name": name,
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": sql_expr,
                    }
                ]
            },
            "description": metric.get("description", name),
        }

        obml_synonyms = metric.get("synonyms", [])
        ai_synonyms = [s for s in obml_synonyms if s != name]
        if ai_synonyms:
            result["ai_context"] = {"synonyms": ai_synonyms}

        ext_data: dict[str, Any] = {
            "obml_metric_type": "window",
            "obml_window_function": window_fn.lower(),
            "obml_order_direction": metric.get("orderDirection", "desc"),
        }
        if measure_name:
            ext_data["obml_window_measure"] = measure_name
        if time_dim:
            ext_data["obml_window_time_dimension"] = time_dim
        if offset is not None:
            ext_data["obml_window_offset"] = offset
        if buckets is not None:
            ext_data["obml_window_buckets"] = buckets
        if default_value is not None:
            ext_data["obml_window_default_value"] = default_value
        if partition_by:
            ext_data["obml_partition_by"] = list(partition_by)
        if metric.get("format"):
            ext_data["obml_format"] = metric["format"]
        if metric.get("dataType"):
            ext_data["obml_data_type"] = metric["dataType"]
        if metric.get("owner"):
            ext_data["obml_owner"] = metric["owner"]

        result["custom_extensions"] = [
            {
                "vendor_name": _VENDOR_OBML,
                "data": json.dumps(ext_data),
            }
        ]

        return result

    def _measure_to_sql(self, measure: dict, data_objects: dict) -> str | None:
        """Convert an OBML measure definition to a SQL expression string.

        Multi-column measures (two-column statistical aggregates like
        ``corr(a, b)``, or count-distinct over a composite key) emit
        every column as a comma-separated argument list. Single-column
        aggregates collapse to the same shape with one argument.
        """
        agg = measure.get("aggregation", "sum").upper()
        distinct = measure.get("distinct", False)
        distinct_kw = "DISTINCT " if distinct else ""

        columns = measure.get("columns", [])
        if columns:
            args: list[str] = []
            for col_ref in columns:
                do_name = col_ref.get("dataObject", "")
                col_name = col_ref.get("column", "")
                do_obj = data_objects.get(do_name, {})
                col_code = (
                    do_obj.get("columns", {})
                    .get(col_name, {})
                    .get("code", col_name.lower().replace(" ", "_"))
                )
                do_code = do_obj.get("code", do_name.lower().replace(" ", "_"))
                args.append(f"{do_code}.{col_code}")
            return f"{agg}({distinct_kw}{', '.join(args)})"

        obml_expr = measure.get("expression", "")
        if obml_expr:
            sql_inner = self._obml_refs_to_sql(obml_expr, data_objects)
            return f"{agg}({distinct_kw}{sql_inner})"

        return None
