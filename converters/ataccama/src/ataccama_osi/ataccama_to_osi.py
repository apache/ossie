"""Convert Ataccama ONE catalog metadata to an OSI semantic model.

Scope: the caller supplies one or more catalog items (as :class:`CatalogItemBundle`s);
each becomes an OSI dataset, and the collection becomes a single OSI semantic model.

Mapping summary (see README for the full table and known limitations):
  CatalogItem            -> dataset (name, source, description, ai_context, custom_extensions)
  CatalogAttribute       -> field (name, ANSI_SQL identifier expression, dimension.is_time,
                                    description, ai_context, custom_extensions)
  Term / termAssignments -> ai_context.synonyms + ATACCAMA custom_extension
"""

from __future__ import annotations

import json
from typing import Any

from ataccama_osi.models import CatalogAttribute, CatalogItem, CatalogItemBundle, Term

OSI_VERSION = "0.2.0.dev0"
VENDOR = "ATACCAMA"

# Ataccama semantic data types that represent points in time.
TIME_DATA_TYPES = {"DATE", "DATETIME", "TIMESTAMP", "TIME"}


# --- helpers -------------------------------------------------------------


def flatten_richtext(value: Any) -> str | None:
    """Flatten an Ataccama description into plain text.

    Descriptions arrive either as a plain string or as Slate rich-text JSON, e.g.
    ``[{"type": "paragraph", "children": [{"text": "..."}]}]`` (sometimes as a JSON
    string). Returns ``None`` when there is no usable text.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        # Rich text is occasionally delivered as a JSON-encoded string.
        if stripped[0] in "[{":
            try:
                return flatten_richtext(json.loads(stripped))
            except (ValueError, TypeError):
                return stripped
        return stripped

    def collect(node: Any, out: list[str]) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                out.append(text)
            for child in node.get("children", []) or []:
                collect(child, out)
        elif isinstance(node, list):
            for child in node:
                collect(child, out)

    parts: list[str] = []
    collect(value, parts)
    text = "".join(parts).strip()
    return text or None


def _unique_name(desired: str, used: set[str], *, fallback: str) -> str:
    """Return a name unique within ``used`` (OSI requires unique dataset/field names)."""
    base = desired.strip() or fallback
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def _sql_identifier(name: str) -> str:
    """Quote a column name as an ANSI SQL identifier (names may contain spaces/punctuation)."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def build_source(item: CatalogItem) -> str:
    """Best-effort physical source reference.

    The Catalog API does not expose ``database.schema.table``; it only gives a
    folder-name hierarchy (``locations``, leaf-first) plus the item name. We build a
    dotted namespace from the reversed locations followed by the item name. The
    authoritative connection/source URNs are preserved in custom_extensions.
    """
    parts = [loc.name for loc in reversed(item.locations) if loc.name]
    parts.append(item.name)
    return ".".join(parts) if parts else item.name


def _terms_ai_context(term_urns: list[str], terms: dict[str, Term]) -> dict[str, Any] | None:
    """Build an ai_context object from assigned business terms."""
    synonyms: list[str] = []
    instructions: list[str] = []
    for urn in term_urns:
        term = terms.get(urn)
        if not term:
            continue
        if term.name and term.name not in synonyms:
            synonyms.append(term.name)
        desc = flatten_richtext(term.description)
        if desc:
            instructions.append(f"{term.name}: {desc}" if term.name else desc)
    ctx: dict[str, Any] = {}
    if instructions:
        ctx["instructions"] = " ".join(instructions)
    if synonyms:
        ctx["synonyms"] = synonyms
    return ctx or None


def _ataccama_extension(data: dict[str, Any]) -> dict[str, str]:
    """Wrap Ataccama-specific metadata as an OSI custom_extension entry."""
    return {"vendor_name": VENDOR, "data": json.dumps(data, sort_keys=True)}


# --- attribute -> field --------------------------------------------------


def attribute_to_field(attr: CatalogAttribute, terms: dict[str, Term], used: set[str]) -> dict[str, Any]:
    name = _unique_name(attr.name, used, fallback="column")
    field_obj: dict[str, Any] = {
        "name": name,
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": _sql_identifier(attr.name or name)}]},
    }

    if attr.data_type and attr.data_type.upper() in TIME_DATA_TYPES:
        field_obj["dimension"] = {"is_time": True}

    description = flatten_richtext(attr.description) or flatten_richtext(attr.comment)
    if description:
        field_obj["description"] = description

    ai_context = _terms_ai_context([ta.term_urn for ta in attr.term_assignments], terms)
    if ai_context:
        field_obj["ai_context"] = ai_context

    # Preserve source-system typing and the attribute URN for round-tripping.
    ext_data: dict[str, Any] = {"attribute_urn": attr.urn}
    if attr.data_type:
        ext_data["data_type"] = attr.data_type
    if attr.column_type:
        ext_data["column_type"] = attr.column_type
    if attr.term_assignments:
        ext_data["term_urns"] = [ta.term_urn for ta in attr.term_assignments]
    field_obj["custom_extensions"] = [_ataccama_extension(ext_data)]

    return field_obj


# --- catalog item -> dataset ---------------------------------------------


def bundle_to_dataset(bundle: CatalogItemBundle, used_dataset_names: set[str]) -> dict[str, Any]:
    item = bundle.item
    name = _unique_name(item.name, used_dataset_names, fallback="dataset")

    dataset: dict[str, Any] = {"name": name, "source": build_source(item)}

    description = flatten_richtext(item.description)
    if description:
        dataset["description"] = description

    ai_context = _terms_ai_context([ta.term_urn for ta in item.term_assignments], bundle.terms)
    if ai_context:
        dataset["ai_context"] = ai_context

    used_field_names: set[str] = set()
    fields = [attribute_to_field(attr, bundle.terms, used_field_names) for attr in bundle.attributes]
    if fields:
        dataset["fields"] = fields

    # Preserve everything with no OSI-core home so the model round-trips.
    ext_data: dict[str, Any] = {"catalog_item_urn": item.urn}
    if item.connection_urn:
        ext_data["connection_urn"] = item.connection_urn
    if item.source_urn:
        ext_data["source_urn"] = item.source_urn
    if item.origin_path:
        ext_data["origin_path"] = item.origin_path
    if item.locations:
        ext_data["locations"] = [loc.name for loc in item.locations]
    if item.stewardship_group_urn:
        ext_data["stewardship_group_urn"] = item.stewardship_group_urn
    if item.primary_dq_monitor_urn:
        ext_data["primary_dq_monitor_urn"] = item.primary_dq_monitor_urn
    if item.term_assignments:
        ext_data["term_urns"] = [ta.term_urn for ta in item.term_assignments]
    if item.aliases:
        ext_data["aliases"] = item.aliases
    dataset["custom_extensions"] = [_ataccama_extension(ext_data)]

    return dataset


# --- top level -----------------------------------------------------------


def ataccama_to_osi(
    bundles: list[CatalogItemBundle],
    model_name: str = "ataccama_model",
    model_description: str | None = None,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Convert a list of catalog-item bundles into an OSI document dict (ready for YAML)."""
    used_dataset_names: set[str] = set()
    datasets = [bundle_to_dataset(b, used_dataset_names) for b in bundles]

    semantic_model: dict[str, Any] = {"name": model_name, "datasets": datasets}
    if model_description:
        semantic_model["description"] = model_description

    model_ext: dict[str, Any] = {"source": "ataccama-one-catalog"}
    if tenant:
        model_ext["tenant"] = tenant
    semantic_model["custom_extensions"] = [_ataccama_extension(model_ext)]

    return {"version": OSI_VERSION, "semantic_model": [semantic_model]}
