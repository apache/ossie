"""Data models for Ataccama ONE catalog metadata and the pieces of OSI we emit.

Ataccama Catalog API structure (public REST API, /catalog/v1):
  CatalogItem
    ├── urn, name, description (Slate rich-text JSON), originPath
    ├── connection {urn}
    ├── source {urn}
    ├── locations[] {name, description}   # folder-name hierarchy (leaf-first)
    ├── termAssignments[] {termUrn}
    ├── effectiveStewardship {groupUrn}
    ├── primaryDqMonitor {urn}
    └── aliases[] {type, value}
  CatalogAttribute
    ├── urn, catalogItemUrn, name
    ├── description (Slate rich-text JSON)
    ├── dataType (STRING, LONG, INTEGER, FLOAT, DOUBLE, BOOLEAN, DATE, DATETIME, ...)
    ├── columnType (raw source-system type, e.g. VARCHAR, R8)
    ├── comment
    └── termAssignments[] {termUrn}
  Term
    ├── urn, name, type (businessTerm, securityTerm)
    └── description (Slate rich-text JSON)

OSI semantic model structure (what we produce):
  version, semantic_model[]
    ├── name, description, ai_context, custom_extensions[]
    └── datasets[] {name, source, description, ai_context, fields[], custom_extensions[]}
        └── fields[] {name, expression{dialects[]}, dimension{is_time}, description,
                      ai_context, custom_extensions[]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Ataccama Catalog types ---


@dataclass
class TermAssignment:
    term_urn: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TermAssignment:
        return cls(term_urn=d.get("termUrn", ""))


@dataclass
class CatalogLocation:
    name: str
    description: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogLocation:
        return cls(name=d.get("name", ""), description=d.get("description"))


@dataclass
class CatalogAttribute:
    urn: str
    name: str
    catalog_item_urn: str = ""
    description: Any = None
    data_type: str | None = None
    column_type: str | None = None
    comment: str | None = None
    term_assignments: list[TermAssignment] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogAttribute:
        return cls(
            urn=d.get("urn", ""),
            name=d.get("name", ""),
            catalog_item_urn=d.get("catalogItemUrn", ""),
            description=d.get("description"),
            data_type=d.get("dataType"),
            column_type=d.get("columnType"),
            comment=d.get("comment"),
            term_assignments=[TermAssignment.from_dict(t) for t in d.get("termAssignments", [])],
        )


@dataclass
class CatalogItem:
    urn: str
    name: str
    description: Any = None
    origin_path: str | None = None
    connection_urn: str | None = None
    source_urn: str | None = None
    locations: list[CatalogLocation] = field(default_factory=list)
    term_assignments: list[TermAssignment] = field(default_factory=list)
    stewardship_group_urn: str | None = None
    primary_dq_monitor_urn: str | None = None
    aliases: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogItem:
        connection = d.get("connection") or {}
        source = d.get("source") or {}
        stewardship = d.get("effectiveStewardship") or {}
        monitor = d.get("primaryDqMonitor") or {}
        return cls(
            urn=d.get("urn", ""),
            name=d.get("name", ""),
            description=d.get("description"),
            origin_path=d.get("originPath"),
            connection_urn=connection.get("urn"),
            source_urn=source.get("urn"),
            locations=[CatalogLocation.from_dict(loc) for loc in d.get("locations", [])],
            term_assignments=[TermAssignment.from_dict(t) for t in d.get("termAssignments", [])],
            stewardship_group_urn=stewardship.get("groupUrn"),
            primary_dq_monitor_urn=monitor.get("urn"),
            aliases=list(d.get("aliases", [])),
        )


@dataclass
class Term:
    urn: str
    name: str
    type: str | None = None
    description: Any = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Term:
        return cls(
            urn=d.get("urn", ""),
            name=d.get("name", ""),
            type=d.get("type"),
            description=d.get("description"),
        )


@dataclass
class CatalogItemBundle:
    """A catalog item together with everything needed to convert it to a dataset."""

    item: CatalogItem
    attributes: list[CatalogAttribute] = field(default_factory=list)
    terms: dict[str, Term] = field(default_factory=dict)
    # Raw DqResults payload (overall + per-dimension + per-attribute quality), or None.
    dq_results: dict[str, Any] | None = None
    # The monitor's configured overall DQ threshold (0-100), or None if not set.
    dq_threshold_pct: float | None = None
    # Primary keys: [{"name": str, "columns": [ordered column names]}].
    primary_keys: list[dict[str, Any]] = field(default_factory=list)
    # Foreign keys: [{"name", "columns": [local cols], "referenced_table", "referenced_columns"}].
    foreign_keys: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogItemBundle:
        return cls(
            item=CatalogItem.from_dict(d["item"]),
            attributes=[CatalogAttribute.from_dict(a) for a in d.get("attributes", [])],
            terms={urn: Term.from_dict(t) for urn, t in d.get("terms", {}).items()},
            dq_results=d.get("dq_results"),
            dq_threshold_pct=d.get("dq_threshold_pct"),
            primary_keys=list(d.get("primary_keys", [])),
            foreign_keys=list(d.get("foreign_keys", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to the raw-JSON shape (used for recording test fixtures)."""
        return {
            "item": _dataclass_to_raw_item(self.item),
            "attributes": [_dataclass_to_raw_attr(a) for a in self.attributes],
            "terms": {urn: _dataclass_to_raw_term(t) for urn, t in self.terms.items()},
            "dq_results": self.dq_results,
            "dq_threshold_pct": self.dq_threshold_pct,
            "primary_keys": self.primary_keys,
            "foreign_keys": self.foreign_keys,
        }


def _dataclass_to_raw_item(item: CatalogItem) -> dict[str, Any]:
    return {
        "urn": item.urn,
        "name": item.name,
        "description": item.description,
        "originPath": item.origin_path,
        "connection": {"urn": item.connection_urn} if item.connection_urn else None,
        "source": {"urn": item.source_urn} if item.source_urn else None,
        "locations": [{"name": loc.name, "description": loc.description} for loc in item.locations],
        "termAssignments": [{"termUrn": t.term_urn} for t in item.term_assignments],
        "effectiveStewardship": ({"groupUrn": item.stewardship_group_urn} if item.stewardship_group_urn else None),
        "primaryDqMonitor": ({"urn": item.primary_dq_monitor_urn} if item.primary_dq_monitor_urn else None),
        "aliases": item.aliases,
    }


def _dataclass_to_raw_attr(attr: CatalogAttribute) -> dict[str, Any]:
    return {
        "urn": attr.urn,
        "catalogItemUrn": attr.catalog_item_urn,
        "name": attr.name,
        "description": attr.description,
        "dataType": attr.data_type,
        "columnType": attr.column_type,
        "comment": attr.comment,
        "termAssignments": [{"termUrn": t.term_urn} for t in attr.term_assignments],
    }


def _dataclass_to_raw_term(term: Term) -> dict[str, Any]:
    return {"urn": term.urn, "name": term.name, "type": term.type, "description": term.description}
