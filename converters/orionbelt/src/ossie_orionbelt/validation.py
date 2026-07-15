"""Validation helpers for OBML / OSI / OSI-ontology documents.

Extracted verbatim from ``converter.py``. Schema files are vendored beside the
package under ``schemas/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCHEMAS_DIR = _SCRIPT_DIR / "schemas"

# The OBML schema is OrionBelt's own format, always vendored beside the package
# and kept in sync with the repo-root schema/obml-schema.json by a drift-guard
# test.
_OBML_SCHEMA_PATH = _SCHEMAS_DIR / "obml-schema.json"


def _osi_schema_path(filename: str) -> Path:
    """Resolve an OSI core-spec schema without forcing a duplicate copy where a
    canonical one already exists.

    Resolution order:
    1. A copy vendored beside this package (``schemas/<filename>``). The
       standalone PyPI wheel and the OrionBelt product API rely on this, so the
       package validates OSI documents self-contained and offline.
    2. Otherwise ``core-spec/<filename>`` in an enclosing Ossie monorepo
       checkout, found by walking up from this file. This lets the in-tree
       converter drop its vendored copy and link the single canonical schema
       rather than duplicating it.

    Returns the vendored path unchanged when neither exists, so downstream
    ``.exists()`` checks degrade to skip-with-warning rather than raising.
    """
    vendored = _SCHEMAS_DIR / filename
    if vendored.exists():
        return vendored
    for parent in _SCRIPT_DIR.parents:
        candidate = parent / "core-spec" / filename
        if candidate.exists():
            return candidate
    return vendored


_OSI_SCHEMA_PATH = _osi_schema_path("osi-schema.json")


class ValidationResult:
    """Collects schema errors, semantic errors, and warnings."""

    def __init__(self, format_name: str = "OBML") -> None:
        self.format_name = format_name
        self.schema_errors: list[str] = []
        self.semantic_errors: list[str] = []
        self.semantic_warnings: list[str] = []

    @property
    def valid(self) -> bool:
        return not self.schema_errors and not self.semantic_errors

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.schema_errors:
            lines.append(f"  JSON Schema: {len(self.schema_errors)} error(s)")
            for e in self.schema_errors:
                lines.append(f"    - {e}")
        else:
            lines.append("  JSON Schema: ✓ valid")
        if self.semantic_errors:
            lines.append(f"  Semantic:    {len(self.semantic_errors)} error(s)")
            for e in self.semantic_errors:
                lines.append(f"    - {e}")
        else:
            lines.append("  Semantic:    ✓ valid")
        if self.semantic_warnings:
            lines.append(f"  Warnings:    {len(self.semantic_warnings)}")
            for w in self.semantic_warnings:
                lines.append(f"    - {w}")
        return lines


def _validate_json_schema(
    data: dict[str, Any],
    schema_path: Path,
    result: ValidationResult,
    draft: str = "draft7",
    registry: Any | None = None,
) -> None:
    """Run JSON Schema validation, appending errors to *result*.

    *registry* is an optional ``referencing.Registry`` used to resolve external
    ``$ref`` URIs against local resources (avoids network fetches).
    """
    try:
        import jsonschema
    except ImportError:
        result.semantic_warnings.append(
            "jsonschema package not installed — skipping JSON Schema validation"
        )
        return

    if not schema_path.exists():
        result.semantic_warnings.append(
            f"Schema file not found at {schema_path} — skipping JSON Schema validation"
        )
        return

    with open(schema_path) as f:
        schema = json.load(f)

    validator_cls = (
        jsonschema.Draft202012Validator if draft == "draft2020" else jsonschema.Draft7Validator
    )
    validator = validator_cls(schema, registry=registry) if registry else validator_cls(schema)
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        result.schema_errors.append(f"[{path}] {error.message}")


# ── OBML Validation ──────────────────────────────────────────────────────


def validate_obml(obml_dict: dict[str, Any], schema_path: Path | None = None) -> ValidationResult:
    """Validate an OBML dict against JSON Schema and semantic rules.

    Runs two layers of validation:
    1. **JSON Schema** — structural correctness (types, required fields,
       allowed properties) against ``schema/obml-schema.json``
    2. **Semantic** — reference integrity, cycle detection, duplicate
       identifiers via OrionBelt's ``ReferenceResolver`` + ``SemanticValidator``

    Both layers are optional — if ``jsonschema`` or ``orionbelt`` packages are
    not installed the corresponding checks are skipped with a warning.
    """
    result = ValidationResult("OBML")

    # 1. JSON Schema validation
    _validate_json_schema(obml_dict, schema_path or _OBML_SCHEMA_PATH, result, draft="draft7")

    # 2. Semantic validation (ReferenceResolver + SemanticValidator)
    try:
        from orionbelt.parser.resolver import ReferenceResolver
        from orionbelt.parser.validator import SemanticValidator
    except ImportError:
        result.semantic_warnings.append(
            "orionbelt package not installed — skipping semantic validation"
        )
    else:
        resolver = ReferenceResolver()
        model, resolve_result = resolver.resolve(obml_dict)
        if not resolve_result.valid:
            for err in resolve_result.errors:
                path_info = f" (at {err.path})" if err.path else ""
                suggestions = ""
                if err.suggestions:
                    suggestions = f" Did you mean: {', '.join(err.suggestions)}?"
                result.semantic_errors.append(f"[{err.code}] {err.message}{path_info}{suggestions}")
        for warn in resolve_result.warnings:
            result.semantic_warnings.append(f"[{warn.code}] {warn.message}")

        # Run SemanticValidator only if resolution produced a usable model
        if resolve_result.valid:
            sem_validator = SemanticValidator()
            sem_errors = sem_validator.validate(model)
            for err in sem_errors:
                path_info = f" (at {err.path})" if err.path else ""
                result.semantic_errors.append(f"[{err.code}] {err.message}{path_info}")

    return result


# ── OSI Validation ───────────────────────────────────────────────────────


def validate_osi(osi_dict: dict[str, Any], schema_path: Path | None = None) -> ValidationResult:
    """Validate an OSI dict against JSON Schema and semantic rules.

    Runs three layers of validation (mirroring OSI's own ``validate.py``):
    1. **JSON Schema** — structural correctness against ``osi-schema.json``
       (Draft 2020-12)
    2. **Unique names** — datasets, fields, metrics, relationships
    3. **References** — relationship from/to reference existing datasets
    """
    result = ValidationResult("OSI")

    # 1. JSON Schema validation (OSI uses Draft 2020-12)
    _validate_json_schema(osi_dict, schema_path or _OSI_SCHEMA_PATH, result, draft="draft2020")

    # 2. Unique name checks
    for model in osi_dict.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        # Unique dataset names
        dataset_names: list[str] = []
        for ds in model.get("datasets", []):
            name = ds.get("name", "")
            if name in dataset_names:
                result.semantic_errors.append(
                    f"[DUPLICATE_DATASET] Duplicate dataset name '{name}' in model '{model_name}'"
                )
            dataset_names.append(name)

        # Unique field names within each dataset
        for ds in model.get("datasets", []):
            ds_name = ds.get("name", "<unnamed>")
            field_names: list[str] = []
            for field in ds.get("fields", []):
                fname = field.get("name", "")
                if fname in field_names:
                    result.semantic_errors.append(
                        f"[DUPLICATE_FIELD] Duplicate field name '{fname}' in dataset '{ds_name}'"
                    )
                field_names.append(fname)

        # Unique metric names
        metric_names: list[str] = []
        for m in model.get("metrics", []):
            mname = m.get("name", "")
            if mname in metric_names:
                result.semantic_errors.append(
                    f"[DUPLICATE_METRIC] Duplicate metric name '{mname}' in model '{model_name}'"
                )
            metric_names.append(mname)

        # Unique relationship names
        rel_names: list[str] = []
        for r in model.get("relationships", []):
            rname = r.get("name", "")
            if rname in rel_names:
                result.semantic_errors.append(
                    f"[DUPLICATE_RELATIONSHIP] Duplicate relationship name "
                    f"'{rname}' in model '{model_name}'"
                )
            rel_names.append(rname)

    # 3. Reference checks — relationships reference existing datasets
    for model in osi_dict.get("semantic_model", []):
        ds_name_set = {ds.get("name") for ds in model.get("datasets", []) if ds.get("name")}
        for rel in model.get("relationships", []):
            rel_name = rel.get("name", "<unnamed>")
            from_ds = rel.get("from")
            to_ds = rel.get("to")
            if from_ds and from_ds not in ds_name_set:
                result.semantic_errors.append(
                    f"[UNKNOWN_DATASET_REF] Relationship '{rel_name}' "
                    f"references unknown dataset '{from_ds}'"
                )
            if to_ds and to_ds not in ds_name_set:
                result.semantic_errors.append(
                    f"[UNKNOWN_DATASET_REF] Relationship '{rel_name}' "
                    f"references unknown dataset '{to_ds}'"
                )

    return result


def validate_osi_ontology(onto_dict: dict[str, Any]) -> ValidationResult:
    """Validate an OSI ontology dict against semantic rules.

    Ossie ships no OSI ontology schema (the converter emits ontology documents
    but never consumes them), so JSON-Schema conformance is not checked here.
    Runs the converter-owned semantic checks:
    1. **Unique concept names** across the ``ontology`` components.
    2. **Reference integrity** — relationship roles and concept_mappings
       reference concepts defined in the ontology.
    """
    result = ValidationResult("OSI-ONTOLOGY")

    # 1. Unique concept names + collect the defined set.
    defined: set[str] = set()
    for comp in onto_dict.get("ontology", []):
        name = comp.get("concept", {}).get("name", "")
        if name in defined:
            result.semantic_errors.append(f"[DUPLICATE_CONCEPT] Duplicate concept name '{name}'")
        defined.add(name)

    # 2. Reference integrity — roles reference defined concepts.
    for comp in onto_dict.get("ontology", []):
        for rel in comp.get("relationships", []):
            rel_name = rel.get("name", "<unnamed>")
            for role in rel.get("roles", []):
                rc = role.get("concept")
                if rc and rc not in defined:
                    result.semantic_errors.append(
                        f"[UNKNOWN_CONCEPT_REF] Relationship '{rel_name}' role "
                        f"references unknown concept '{rc}'"
                    )

    # concept_mappings reference defined concepts.
    for omap in onto_dict.get("ontology_mappings", []):
        for cm in omap.get("concept_mappings", []):
            cc = cm.get("concept")
            if cc and cc not in defined:
                result.semantic_errors.append(
                    f"[UNKNOWN_CONCEPT_REF] Concept mapping references unknown concept '{cc}'"
                )

    return result
