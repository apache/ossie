"""FINOS Legend data model representations."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional, List
import re


@dataclass
class Column:
    """Represents a column in a FINOS Legend table."""
    name: str
    type: str
    nullable: bool = True
    description: Optional[str] = None
    is_primary_key: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, omitting None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_pure_declaration(self) -> str:
        """Generate FINOS Pure column declaration (no colon, correct syntax)."""
        pk_marker = " PRIMARY KEY" if self.is_primary_key else ""
        return f"    {self.name} {self.type}{pk_marker}"


@dataclass
class Table:
    """Represents a physical table in FINOS Legend."""
    name: str
    schema: str
    database: str
    columns: List[Column] = field(default_factory=list)
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "name": self.name,
            "schema": self.schema,
            "database": self.database,
            "columns": [c.to_dict() for c in self.columns],
        }
        if self.description:
            result["description"] = self.description
        return result

    def to_pure_declaration(self, indent: str = "  ") -> str:
        """Generate FINOS Pure table declaration (for Schema)."""
        lines = [f"{indent}Table {self.name} ("]
        for i, col in enumerate(self.columns):
            lines.append(col.to_pure_declaration() + ",")
        if lines[-1].endswith(","):
            lines[-1] = lines[-1][:-1]  # Remove trailing comma from last column
        lines.append(f"{indent})")
        return "\n".join(lines)


@dataclass
class RelationColumnMapping:
    """Represents a mapping from a relation field to a physical column."""
    relation_field: str
    physical_column: str
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "relationField": self.relation_field,
            "physicalColumn": self.physical_column,
        }


@dataclass
class Relation:
    """Represents a logical relation (like a view) in FINOS Legend."""
    name: str
    primary_table: str  # Reference to physical table name
    description: Optional[str] = None
    column_mappings: List[RelationColumnMapping] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "name": self.name,
            "primaryTable": self.primary_table,
        }
        if self.description:
            result["description"] = self.description
        if self.column_mappings:
            result["columnMappings"] = [m.to_dict() for m in self.column_mappings]
        return result


@dataclass
class Join:
    """Represents a join path between two relations."""
    name: str
    from_relation: str
    to_relation: str
    from_columns: List[str] = field(default_factory=list)
    to_columns: List[str] = field(default_factory=list)
    join_type: str = "INNER"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fromRelation": self.from_relation,
            "toRelation": self.to_relation,
            "fromColumns": self.from_columns,
            "toColumns": self.to_columns,
            "joinType": self.join_type,
        }

    def to_pure_declaration(self) -> str:
        """Generate FINOS Pure association declaration."""
        conditions = []
        for from_col, to_col in zip(self.from_columns, self.to_columns):
            conditions.append(f"      {self.from_relation}.{from_col} = {self.to_relation}.{to_col}")
        condition_str = " and\n".join(conditions)
        
        return f"""{self.name}
  (
    {self.from_relation} *
    {self.to_relation} 1
    [
{condition_str}
    ]
  )"""


@dataclass
class OntologyProperty:
    """Represents a property in a generated Pure ontology class."""
    name: str
    type: str
    multiplicity: str = "[*]"
    description: Optional[str] = None
    is_key: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "type": self.type,
            "multiplicity": self.multiplicity,
            "is_key": self.is_key,
        }
        if self.description:
            result["description"] = self.description
        return result

    def to_pure_declaration(self, indent: str = "  ") -> str:
        lines = []
        if self.is_key:
            lines.append(f"{indent}<<equality.Key>>")
        lines.append(f"{indent}{self.name}: {self.type}{self.multiplicity};")
        return "\n".join(lines)


@dataclass
class OntologyConcept:
    """Represents an OSI ontology concept mapped to a Pure class."""
    name: str
    extends: List[str] = field(default_factory=list)
    properties: List[OntologyProperty] = field(default_factory=list)
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "extends": self.extends,
            "properties": [p.to_dict() for p in self.properties],
        }
        if self.description:
            result["description"] = self.description
        return result

    def to_pure_declaration(self, namespace: str) -> str:
        class_name = _pure_identifier(self.name)
        extends_list = self.extends if self.extends else ["Any"]
        extends_clause = ", ".join(_pure_identifier(ext) for ext in extends_list)

        lines = [f"Class {namespace}::{class_name} extends {extends_clause}", "{"]
        for prop in self.properties:
            lines.append(prop.to_pure_declaration(indent="  "))
        lines.append("}")
        return "\n".join(lines)


@dataclass
class OntologyEntityPropertyMapping:
    """Represents one mapped ontology property to a source relation field."""
    property_name: str
    source_field: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "propertyName": self.property_name,
            "sourceField": self.source_field,
        }


@dataclass
class OntologyEntityMapping:
    """Represents one FINOS Legend mapping block for an ontology concept."""
    concept_name: str
    source_dataset: str
    property_mappings: List[OntologyEntityPropertyMapping] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conceptName": self.concept_name,
            "sourceDataset": self.source_dataset,
            "propertyMappings": [m.to_dict() for m in self.property_mappings],
        }


@dataclass
class LegendDatabase:
    """Represents a FINOS Legend Database (top-level semantic model)."""
    name: str
    package: str
    description: Optional[str] = None
    tables: List[Table] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)
    joins: List[Join] = field(default_factory=list)
    ontology_concepts: List[OntologyConcept] = field(default_factory=list)
    ontology_mappings: List[OntologyEntityMapping] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "name": self.name,
            "package": self.package,
            "tables": [t.to_dict() for t in self.tables],
            "relations": [r.to_dict() for r in self.relations],
        }
        if self.description:
            result["description"] = self.description
        if self.joins:
            result["joins"] = [j.to_dict() for j in self.joins]
        if self.ontology_concepts:
            result["ontology"] = [concept.to_dict() for concept in self.ontology_concepts]
        if self.ontology_mappings:
            result["ontologyMappings"] = [mapping.to_dict() for mapping in self.ontology_mappings]
        return result

    def to_pure_declaration(self) -> str:
        """Generate FINOS Pure database declaration."""
        lines = []
        
        # Group tables by schema
        schema_tables: dict[str, List[Table]] = {}
        for table in self.tables:
            schema_tables.setdefault(table.schema, []).append(table)
        
        # Generate database definition
        db_name = f"{'::'.join(self.package.split('.'))}::{self.name}"
        lines.append(f"###Relational")
        lines.append(f"Database {db_name}")
        lines.append("(")
        
        # Generate schemas with tables
        for schema, tables in sorted(schema_tables.items()):
            lines.append(f"  Schema {schema}")
            lines.append("  (")
            for i, table in enumerate(tables):
                lines.append(table.to_pure_declaration(indent="    "))
                if i < len(tables) - 1:
                    lines.append("")
            lines.append("  )")
        
        lines.append(")")
        
        # Generate one Pure function per relation/dataset.
        relation_functions = self._build_relation_functions()
        if relation_functions:
            lines.append("")
            lines.extend(relation_functions)

        relationship_functions = self._build_relationship_functions()
        if relationship_functions:
            lines.append("")
            lines.extend(relationship_functions)

        ontology_classes = self._build_ontology_class_declarations()
        if ontology_classes:
            lines.append("")
            lines.append("###Pure")
            lines.extend(ontology_classes)

        ontology_mappings = self._build_ontology_mapping_declarations()
        if ontology_mappings:
            lines.append("")
            lines.append("###Mapping")
            lines.extend(ontology_mappings)
        
        return "\n".join(lines)

    def _build_relation_functions(self) -> List[str]:
        """Generate Pure functions that return a Relation for each dataset."""
        if not self.relations:
            return []

        table_lookup = {table.name: table for table in self.tables}
        model_segment = _pure_identifier(self.name)
        function_lines: List[str] = []

        for relation in self.relations:
            table = table_lookup.get(relation.primary_table)
            if table is None:
                continue

            relation_segment = _pure_identifier(relation.name)
            db_path = "::".join(self.package.split("."))
            table_ref = f"{db_path}::{self.name}.{table.schema}.{table.name}"

            # Keep projected field order aligned with relation mappings.
            relation_fields = [m.relation_field for m in relation.column_mappings]
            if not relation_fields:
                relation_fields = [c.name for c in table.columns]

            select_clause = ", ".join(relation_fields)
            function_lines.append(
                f"function model::osi::semantic_model::{model_segment}::dataset::{relation_segment}(): Relation<Any>[1]"
            )
            function_lines.append("{")
            function_lines.append(f"  #>{{{table_ref}}}#")
            function_lines.append(f"    ->select(~[{select_clause}])")
            function_lines.append("}")
            function_lines.append("")

        if function_lines and function_lines[-1] == "":
            function_lines.pop()
        return function_lines

    def _build_ontology_class_declarations(self) -> List[str]:
        """Generate Pure classes from ontology concepts when present."""
        if not self.ontology_concepts:
            return []

        model_segment = _pure_identifier(self.name)
        namespace = f"model::osi::semantic_model::{model_segment}::ontology"
        lines: List[str] = []

        for i, concept in enumerate(self.ontology_concepts):
            lines.append(concept.to_pure_declaration(namespace))
            if i < len(self.ontology_concepts) - 1:
                lines.append("")

        return lines

    def _build_ontology_mapping_declarations(self) -> List[str]:
        """Generate FINOS Legend Mapping block from ontology entity mappings."""
        if not self.ontology_mappings:
            return []

        model_segment = _pure_identifier(self.name)
        ontology_ns = f"model::osi::semantic_model::{model_segment}::ontology"
        mapping_ns = f"{ontology_ns}::mapping::OntologyMapping"
        lines: List[str] = [f"Mapping {mapping_ns}", "("]

        for idx, entity_mapping in enumerate(self.ontology_mappings):
            concept_name = _pure_identifier(entity_mapping.concept_name)
            dataset_name = _pure_identifier(entity_mapping.source_dataset)
            lines.append(f"  *{ontology_ns}::{concept_name}[]: Relation")
            lines.append("  {")
            lines.append(
                f"    ~func model::osi::semantic_model::{model_segment}::dataset::{dataset_name}__Relation_1_"
            )

            for prop_index, prop_mapping in enumerate(entity_mapping.property_mappings):
                suffix = "," if prop_index < len(entity_mapping.property_mappings) - 1 else ""
                lines.append(
                    f"    {_pure_identifier(prop_mapping.property_name)}: {_pure_identifier(prop_mapping.source_field)}{suffix}"
                )

            lines.append("  }")

        lines.append(")")
        lines.append("")
        return lines

    def _build_relationship_functions(self) -> List[str]:
        """Generate Pure functions that return a Relation for each relationship/join."""
        if not self.joins or not self.relations:
            return []

        table_lookup = {table.name: table for table in self.tables}
        relation_lookup = {relation.name: relation for relation in self.relations}
        model_segment = _pure_identifier(self.name)
        db_path = "::".join(self.package.split("."))
        function_lines: List[str] = []

        for join in self.joins:
            from_relation = relation_lookup.get(join.from_relation)
            to_relation = relation_lookup.get(join.to_relation)
            if from_relation is None or to_relation is None:
                continue

            from_table = table_lookup.get(from_relation.primary_table)
            to_table = table_lookup.get(to_relation.primary_table)
            if from_table is None or to_table is None:
                continue

            if not join.from_columns or not join.to_columns:
                continue

            rel_segment = _pure_identifier(join.name)
            from_table_ref = f"{db_path}::{self.name}.{from_table.schema}.{from_table.name}"
            to_table_ref = f"{db_path}::{self.name}.{to_table.schema}.{to_table.name}"

            conditions = []
            for from_col, to_col in zip(join.from_columns, join.to_columns):
                conditions.append(f"$x.{from_col} == $y.{to_col}")
            predicate = " and ".join(conditions)

            function_lines.append(
                f"function model::osi::semantic_model::{model_segment}::relationship::{rel_segment}(): Relation<Any>[1]"
            )
            function_lines.append("{")
            function_lines.append(f"  #>{{{from_table_ref}}}#")
            function_lines.append(
                f"    ->join(#>{{{to_table_ref}}}#, JoinKind.{join.join_type}, {{x,y| {predicate}}});"
            )
            function_lines.append("}")
            function_lines.append("")

        if function_lines and function_lines[-1] == "":
            function_lines.pop()
        return function_lines


def _pure_identifier(name: str) -> str:
    """Normalize free-form names into safe Pure path identifiers."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not sanitized:
        return "unnamed"
    if sanitized[0].isdigit():
        return f"_{sanitized}"
    return sanitized


@dataclass
class LegendModel:
    """Top-level FINOS Legend model wrapper."""
    version: str = "1.0.0"
    databases: List[LegendDatabase] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "databases": [db.to_dict() for db in self.databases],
        }

    def to_pure(self) -> str:
        """Generate FINOS Pure DSL text."""
        lines = []
        for i, db in enumerate(self.databases):
            lines.append(db.to_pure_declaration())
            if i < len(self.databases) - 1:
                lines.append("")
        return "\n".join(lines)
