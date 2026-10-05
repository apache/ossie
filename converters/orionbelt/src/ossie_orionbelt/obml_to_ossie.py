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

"""OBML → Ossie conversion (the :class:`OBMLtoOssie` direction).

Extracted verbatim from ``converter.py``; see that module for the package-level
docstring and the shared constants in :mod:`ossie_orionbelt._common`.
"""

from __future__ import annotations

import json
from typing import Any

from ossie_orionbelt._common import (
    _INTERNAL_VENDORS,
    _OSSIE_VENDOR_READ,
    _OSSIE_VERSION,
    _VENDOR_OBML,
    OBML_ABSTRACT_TO_OSSIE_DATATYPE,
    OBML_TO_OSSIE_TYPE,
    obml_datatype_to_ossie,
)
from ossie_orionbelt._portable import NotPortableError, PortableRenderer


class OBMLtoOssie:
    """Convert an OBML semantic model YAML to Ossie format."""

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
        # Measures and metrics with no faithful Ossie expression, by kind
        # ("measures" / "metrics"); they ride in the model-level extension.
        self.unexported: dict[str, dict[str, Any]] = {}

    def convert(self) -> dict:
        # Reset per-conversion state so a second convert() call on the same
        # instance does not duplicate warnings or left-out entities.
        self.warnings = []
        self.unexported = {}

        ossie: dict[str, Any] = {"version": _OSSIE_VERSION}

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

        # ── Metrics (OBML measures + metrics → Ossie metrics) ────────
        ossie_metrics = self._convert_measures_and_metrics(obml_measures, obml_metrics)

        # Re-emit Ossie metrics that OBML could not represent and that the import
        # path preserved verbatim (vendor Ossie, ``obml_unconverted_metrics``).
        # This closes the Ossie -> OBML -> Ossie roundtrip for non-SQL or
        # non-decomposable metrics.
        self._merge_restored_metrics(ossie_metrics)

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

        if ossie_metrics:
            sem_model["metrics"] = ossie_metrics

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
        # Preserve count-synthesis knobs (Ossie has no native equivalent). The
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
        if self.unexported:
            roundtrip_data["obml_unexported"] = self.unexported
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

        ossie.update(sem_model)

        # Dialects and vendors are represented on the expressions and extensions
        # that use them; root-level advertisement arrays are not supported.
        return ossie

    def _emit_foreign_extensions(self, obml_exts: list[dict] | None, ossie_exts: list[dict]) -> None:
        """Re-emit third-party OBML customExtensions as Ossie custom_extensions.

        Mirrors ``OssietoOBML._carry_foreign_extensions``: extensions from a
        vendor we do not handle internally are passed back to Ossie under their
        original vendor name, completing the roundtrip.
        """
        for ext in obml_exts or []:
            vendor = ext.get("vendor")
            if vendor and vendor not in _INTERNAL_VENDORS:
                ossie_exts.append({"vendor_name": vendor, "data": ext.get("data", "")})

    def _convert_data_object(
        self, do_name: str, do_obj: dict, obml_dimensions: dict
    ) -> tuple[dict, list]:
        """Convert an OBML dataObject to an Ossie dataset + relationships."""
        database = do_obj.get("database", "")
        schema = do_obj.get("schema", "")
        code = do_obj.get("code", "")
        source = f"{database}.{schema}.{code}" if database else code

        # Use the OBML display name as the Ossie dataset name so that
        # relationship references (joinTo) stay consistent in roundtrips
        ossie_name = do_name

        dataset: dict[str, Any] = {
            "name": ossie_name,
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
        # Use the physical ``code`` for each column when present — Ossie
        # field names mirror the physical column code (see _convert_column).
        if pk_columns:
            columns_map = do_obj.get("columns", {}) or {}
            dataset["primary_key"] = [
                columns_map[c].get("code", c.lower().replace(" ", "_")) for c in pk_columns
            ]

        # ── Unique keys (v0.2 first-class, lossless roundtrip via OBSL) ──
        # OBML doesn't model unique keys natively today; round-trip via the
        # ``OBSL``-vendor ``obml_unique_keys`` payload that originated from
        # a prior Ossie → OBML conversion (or hand-authored OBML).
        unique_keys_extra: list[list[str]] | None = None
        for ext in do_obj.get("customExtensions", []) or []:
            if ext.get("vendor") not in _OSSIE_VENDOR_READ:
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
            if ext.get("vendor") == "Ossie":
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
            rel = self._convert_join_to_relationship(ossie_name, do_name, do_obj, join, i)
            if rel:
                relationships.append(rel)

        # ── Preserve DataObject owner/comment + refresh in custom_extensions ──
        do_extras: dict[str, Any] = {}
        if do_obj.get("owner"):
            do_extras["obml_owner"] = do_obj["owner"]
        if do_obj.get("comment"):
            do_extras["obml_comment"] = do_obj["comment"]
        # OBML-only freshness contract — round-tripped through Ossie
        # custom_extensions since Ossie has no native equivalent. See
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
        """Convert an OBML column to an Ossie field."""
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

        # ── Field label (Ossie v0.2 first-class) ──
        # Surfaced from OBSL-vendor customExtensions ``obml_field_label`` —
        # round-trip path for Ossie → OBML → Ossie fidelity. OBML has no
        # native column ``label`` today.
        for ext in col_obj.get("customExtensions", []) or []:
            if ext.get("vendor") not in _OSSIE_VENDOR_READ:
                continue
            try:
                ext_label_data = json.loads(ext.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            if ext_label_data.get("obml_field_label"):
                field["label"] = ext_label_data["obml_field_label"]
                break

        # Restore ai_context from customExtensions (Ossie vendor) if present
        ai_ctx: dict[str, Any] = {}
        for ext in col_obj.get("customExtensions", []):
            if ext.get("vendor") == "Ossie":
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

        # Emit the spec `datatype` from the OBML abstractType so exported fields
        # carry a portable logical type...
        abstract_type = col_obj.get("abstractType", "string")
        field["datatype"] = OBML_ABSTRACT_TO_OSSIE_DATATYPE.get(abstract_type, "String")
        # ...and stash the exact abstractType in custom_extensions so the return
        # trip restores it verbatim, lossless through the narrowing map.
        ossie_type = OBML_TO_OSSIE_TYPE.get(abstract_type, "string")
        ext_data: dict[str, Any] = {
            "data_type": ossie_type,
            "obml_abstract_type": abstract_type,
        }
        # The Ossie field name is the physical code; keep the OBML column name
        # so the reverse trip restores it (measure filters and model filters
        # refer to columns by that name).
        if col_name != code:
            ext_data["obml_column_name"] = col_name
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
        # Preserve OBML-only dimension properties (timeGrain, format, resultType, etc.)
        matched_dim: dict[str, Any] | None = None
        for _dim_name, dim_obj in obml_dimensions.items():
            if dim_obj.get("dataObject") == do_name and dim_obj.get("column") == col_name:
                matched_dim = dim_obj
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
                break
        field["custom_extensions"] = [
            {
                "vendor_name": _VENDOR_OBML,
                "data": json.dumps(ext_data),
            }
        ]

        # Re-emit third-party vendor extensions verbatim. Ossie has no separate
        # dimension entity, so a matched dimension's foreign extensions surface
        # on the field too (they re-import onto the column).
        self._emit_foreign_extensions(col_obj.get("customExtensions"), field["custom_extensions"])
        if matched_dim is not None:
            self._emit_foreign_extensions(
                matched_dim.get("customExtensions"), field["custom_extensions"]
            )

        return field

    def _convert_join_to_relationship(
        self, ossie_from_name: str, _obml_from_name: str, from_do: dict, join: dict, index: int
    ) -> dict | None:
        """Convert an OBML join to an Ossie relationship."""
        join_to_display = join.get("joinTo", "")
        # Use the OBML display name as the Ossie target name (consistent with
        # _convert_data_object which uses display name as Ossie dataset name)
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
            rel_name = f"{ossie_from_name}_to_{to_name}_{path_name}"
        else:
            rel_name = f"{ossie_from_name}_to_{to_name}"
            if index > 0:
                rel_name += f"_{index}"

        rel: dict[str, Any] = {
            "name": rel_name,
            "from": ossie_from_name,
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
        """Recover Ossie metrics preserved verbatim during Ossie -> OBML import.

        The import path stashes metrics OBML can't represent under an Ossie-vendor
        model-level customExtension (``obml_unconverted_metrics``). Re-emit them
        unchanged so a full Ossie -> OBML -> Ossie roundtrip keeps them.
        """
        restored: list[dict] = []
        for ext in self.obml.get("customExtensions", []) or []:
            if ext.get("vendor") not in _OSSIE_VENDOR_READ:
                continue
            try:
                data = json.loads(ext.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            preserved = data.get("obml_unconverted_metrics")
            if isinstance(preserved, list):
                restored.extend(m for m in preserved if isinstance(m, dict))
        return restored

    def _merge_restored_metrics(self, ossie_metrics: list[dict]) -> None:
        """Append preserved (unconverted) Ossie metrics to the converted ones.

        Name-collision guard: if a queryable OBML measure/metric now owns a name
        a stale preserved metric also uses, skip the preserved copy (the real
        metric wins) so the Ossie output has no duplicate metric names and passes
        semantic validation. Each appended metric carries its own dialects
        (``expression.dialects[]``) and vendors (``custom_extensions``)
        verbatim, which are the schema-valid homes for that metadata.
        """
        existing = {m.get("name") for m in ossie_metrics if isinstance(m, dict)}
        for restored in self._restore_unconverted_metrics():
            name = restored.get("name")
            if name in existing:
                self.warnings.append(
                    f"Preserved Ossie metric '{name}' dropped on export: a converted "
                    f"OBML metric now uses that name."
                )
                continue
            existing.add(name)
            ossie_metrics.append(restored)

    def _convert_measures_and_metrics(self, obml_measures: dict, obml_metrics: dict) -> list:
        """Convert OBML measures and metrics to Ossie metrics.

        The expression is what other Ossie consumers read, so it comes from
        :class:`PortableRenderer` and computes what OrionBelt computes. A measure
        or metric with no faithful expression is left out of the document and
        kept whole in ``self.unexported`` for the model-level extension; the
        reverse conversion restores it from there.
        """
        renderer = PortableRenderer(self.obml)
        ossie_metrics = []

        for name, measure in obml_measures.items():
            if str(measure.get("aggregation", "")).lower() == "measure":
                ossie_metric = self._convert_delegated_measure(name, measure)
            else:
                try:
                    sql = renderer.measure(name)
                except NotPortableError as exc:
                    self._leave_out("measures", name, measure, str(exc))
                    continue
                ossie_metric = self._convert_measure(name, measure, sql)
            self._finish_ossie_metric("measure", measure, ossie_metric)
            ossie_metrics.append(ossie_metric)

        for name, metric in obml_metrics.items():
            try:
                sql = renderer.metric(name)
            except NotPortableError as exc:
                self._leave_out("metrics", name, metric, str(exc))
                continue
            ossie_metric = self._convert_metric(name, metric, sql)
            self._finish_ossie_metric("metric", metric, ossie_metric)
            ossie_metrics.append(ossie_metric)

        return ossie_metrics

    def _leave_out(self, kind: str, name: str, definition: dict, reason: str) -> None:
        """Keep a non-portable measure or metric for the model-level extension."""
        self.unexported.setdefault(kind, {})[name] = definition
        self.warnings.append(
            f"{kind[:-1].capitalize()} '{name}' is not exported as an Ossie metric "
            f"because {reason}. It is kept in the {_VENDOR_OBML} extension for the reverse "
            f"conversion."
        )

    def _finish_ossie_metric(self, kind: str, obml_obj: dict, ossie_metric: dict) -> None:
        """Attach what every exported measure or metric carries besides its SQL."""
        definition = {k: v for k, v in obml_obj.items() if k != "customExtensions"}
        self._merge_obml_extension(ossie_metric, "obml_definition", definition)
        self._merge_obml_extension(ossie_metric, "obml_definition_kind", kind)
        self._carry_foreign_to_ossie_metric(obml_obj, ossie_metric)
        self._emit_ossie_metric_datatype(obml_obj, ossie_metric)

    @staticmethod
    def _merge_obml_extension(ossie_metric: dict, key: str, value: Any) -> None:
        """Set *key* in the metric's single OBML-vendor extension, creating it if absent.

        The reverse direction reads only the first OBML-vendor extension it
        finds, so every value goes into that one payload.
        """
        exts = ossie_metric.setdefault("custom_extensions", [])
        for ext in exts:
            if ext.get("vendor_name") == _VENDOR_OBML:
                data = json.loads(ext.get("data") or "{}")
                data[key] = value
                ext["data"] = json.dumps(data)
                return
        exts.append({"vendor_name": _VENDOR_OBML, "data": json.dumps({key: value})})

    def _carry_foreign_to_ossie_metric(self, obml_obj: dict, ossie_metric: dict) -> None:
        """Re-emit third-party vendor extensions on an OBML measure/metric to
        the Ossie metric, dropping the key again if nothing foreign was added."""
        self._emit_foreign_extensions(
            obml_obj.get("customExtensions"), ossie_metric.setdefault("custom_extensions", [])
        )
        if not ossie_metric["custom_extensions"]:
            del ossie_metric["custom_extensions"]

    def _emit_ossie_metric_datatype(self, obml_obj: dict, ossie_metric: dict) -> None:
        """Emit the spec `datatype` from an explicit OBML measure/metric `dataType`.

        Only fires when the OBML object declares an exact `dataType`, so plain
        measures (whose type is only the defaulted `resultType`) stay untouched
        and round trips stay idempotent. The exact `dataType` also round-trips via
        `obml_data_type` in `custom_extensions`; this adds the portable field.
        """
        ossie_dt = obml_datatype_to_ossie(obml_obj.get("dataType"))
        if ossie_dt:
            ossie_metric["datatype"] = ossie_dt

    @staticmethod
    def _ossie_metric(name: str, obml_obj: dict, sql: str, dialect: str = "ANSI_SQL") -> dict:
        """The Ossie metric shell shared by every measure and metric."""
        return {
            "name": name,
            "expression": {"dialects": [{"dialect": dialect, "expression": sql}]},
            "description": obml_obj.get("description", name),
        }

    def _convert_measure(self, name: str, measure: dict, sql: str) -> dict:
        """Convert an OBML measure to an Ossie metric with its portable *sql*."""
        result = self._ossie_metric(name, measure, sql)
        synonyms = [name] + [s for s in measure.get("synonyms", []) if s != name]
        result["ai_context"] = {"synonyms": synonyms}
        self._add_obml_measure_extras(result, measure)
        return result

    def _convert_delegated_measure(self, name: str, measure: dict) -> dict:
        """Convert an ``aggregation: measure`` measure, resolved by a Databricks Metric View.

        The expression is the Databricks ``MEASURE("<label>")`` call, so it is
        tagged with the ``DATABRICKS`` dialect; the extras carry the OBML
        signal back.
        """
        result = self._ossie_metric(name, measure, f'MEASURE("{name}")', dialect="DATABRICKS")
        synonyms = [name] + [s for s in measure.get("synonyms", []) if s != name]
        result["ai_context"] = {"synonyms": synonyms}
        self._add_obml_measure_extras(result, {**measure, "_extra_obml_aggregation": "measure"})
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

    def _convert_metric(self, name: str, metric: dict, sql: str) -> dict:
        """Convert an OBML metric to an Ossie metric with its portable *sql*.

        Cumulative and window metrics also keep their configuration under the
        ``obml_*`` keys, which older readers reconstruct them from.
        """
        result = self._ossie_metric(name, metric, sql)
        synonyms = [s for s in metric.get("synonyms", []) if s != name]
        if synonyms:
            result["ai_context"] = {"synonyms": synonyms}

        kind = metric.get("type")
        ext_data: dict[str, Any] = {}
        if kind == "cumulative":
            ext_data = {
                "obml_metric_type": "cumulative",
                "obml_cumulative_measure": metric.get("measure", ""),
                "obml_cumulative_time_dimension": metric.get("timeDimension", ""),
                "obml_cumulative_type": metric.get("cumulativeType", "sum"),
            }
            if metric.get("window") is not None:
                ext_data["obml_cumulative_window"] = metric["window"]
            if metric.get("grainToDate"):
                ext_data["obml_cumulative_grain_to_date"] = metric["grainToDate"]
        elif kind == "window":
            ext_data = {
                "obml_metric_type": "window",
                "obml_window_function": str(metric.get("windowFunction", "")).lower(),
                "obml_order_direction": metric.get("orderDirection", "desc"),
            }
            optional = {
                "measure": "obml_window_measure",
                "timeDimension": "obml_window_time_dimension",
                "offset": "obml_window_offset",
                "buckets": "obml_window_buckets",
                "defaultValue": "obml_window_default_value",
            }
            for obml_key, ext_key in optional.items():
                if metric.get(obml_key) is not None:
                    ext_data[ext_key] = metric[obml_key]
        if kind in ("cumulative", "window") and metric.get("partitionBy"):
            ext_data["obml_partition_by"] = list(metric["partitionBy"])
        for obml_key, ext_key in (
            ("format", "obml_format"),
            ("dataType", "obml_data_type"),
            ("owner", "obml_owner"),
        ):
            if metric.get(obml_key):
                ext_data[ext_key] = metric[obml_key]
        if ext_data:
            result["custom_extensions"] = [
                {"vendor_name": _VENDOR_OBML, "data": json.dumps(ext_data)}
            ]
        return result
