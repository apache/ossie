"""Convert OSI semantic model representation to FINOS Legend."""

from __future__ import annotations

import json
from typing import Any, Optional
import warnings

from legend_osi.models import (
    Column,
    Table,
    Relation,
    RelationColumnMapping,
    Join,
    OntologyConcept,
    OntologyProperty,
    OntologyEntityMapping,
    OntologyEntityPropertyMapping,
    LegendDatabase,
    LegendModel,
)


OSI_VERSION_SUPPORTED = "0.1.1"


class OsiToLegendConversionError(Exception):
    """Raised when OSI to Legend conversion fails."""


def osi_to_legend_json(
    osi_model: dict[str, Any],
    database_package: str = "org.finos.osi.generated",
) -> str:
    """Convert OSI semantic model dict to FINOS Legend JSON.
    
    Args:
        osi_model: Parsed OSI model dict (from YAML).
        database_package: Package path for the Legend database.
        
    Returns:
        JSON string representing the FINOS Legend model.
        
    Raises:
        OsiToLegendConversionError: If conversion fails.
    """
    legend_model = _convert_osi_to_legend_model(osi_model, database_package)
    return json.dumps(legend_model.to_dict(), indent=2)


def osi_to_legend_pure(
    osi_model: dict[str, Any],
    database_package: str = "org.finos.osi.generated",
) -> str:
    """Convert OSI semantic model dict to FINOS Legend Pure DSL text.
    
    Args:
        osi_model: Parsed OSI model dict (from YAML).
        database_package: Package path for the Legend database.
        
    Returns:
        Pure DSL text string representing the FINOS Legend model.
        
    Raises:
        OsiToLegendConversionError: If conversion fails.
    """
    legend_model = _convert_osi_to_legend_model(osi_model, database_package)
    return legend_model.to_pure()


def osi_to_legend_dict(
    osi_model: dict[str, Any],
    database_package: str = "org.finos.osi.generated",
) -> dict[str, Any]:
    """Convert OSI semantic model dict to FINOS Legend dict representation.
    
    Args:
        osi_model: Parsed OSI model dict (from YAML).
        database_package: Package path for the Legend database.
        
    Returns:
        Dictionary representing the FINOS Legend model.
        
    Raises:
        OsiToLegendConversionError: If conversion fails.
    """
    legend_model = _convert_osi_to_legend_model(osi_model, database_package)
    return legend_model.to_dict()


def _convert_osi_to_legend_model(
    osi_model: dict[str, Any],
    database_package: str,
) -> LegendModel:
    """Internal function to convert OSI model to LegendModel object."""
    
    # Validate version
    version = osi_model.get("version", "")
    if version != OSI_VERSION_SUPPORTED:
        warnings.warn(
            f"OSI version '{version}' may not be fully supported. "
            f"Tested with: {OSI_VERSION_SUPPORTED}"
        )
    
    top_level_ontology = osi_model.get("ontology", [])
    semantic_models = osi_model.get("semantic_model", [])
    if semantic_models is None:
        semantic_models = []
    if not isinstance(semantic_models, list):
        raise OsiToLegendConversionError(
            "Invalid OSI model: 'semantic_model' must be a list when provided"
        )
    if len(semantic_models) == 0:
        if not isinstance(top_level_ontology, list) or len(top_level_ontology) == 0:
            raise OsiToLegendConversionError(
                "Invalid OSI model: at least one of 'semantic_model' or top-level 'ontology' must be provided"
            )
        semantic_models = [{"name": osi_model.get("name", "ontology_model"), "datasets": []}]
    
    if len(semantic_models) > 1:
        warnings.warn(
            f"Multiple semantic models found ({len(semantic_models)}). "
            "Only the first will be converted."
        )
    
    osi_semantic_model = semantic_models[0]
    legend_database = _convert_semantic_model_to_database(
        osi_semantic_model, database_package, top_level_ontology
    )
    
    return LegendModel(databases=[legend_database])


def _convert_semantic_model_to_database(
    osi_model: dict[str, Any],
    package: str,
    top_level_ontology: Any = None,
) -> LegendDatabase:
    """Convert OSI semantic_model to a FINOS Legend Database.
    
    Maps:
    - semantic_model.name -> Database name
    - semantic_model.description -> Database description
    - datasets -> Relations + Tables
    - join_paths -> Joins
    - top-level ontology -> Pure classes
    """
    name = osi_model.get("name")
    if not name:
        raise OsiToLegendConversionError(
            "Semantic model must have a 'name' field"
        )
    
    description = osi_model.get("description", "")
    
    # Build lookup map: dataset_name -> dataset_def for source parsing
    datasets = osi_model.get("datasets", [])
    dataset_map = {ds["name"]: ds for ds in datasets}
    
    # Convert datasets to tables and relations
    tables = []
    relations = []
    table_name_map = {}  # dataset_name -> table_name (for join_path resolution)
    
    for dataset in datasets:
        table, relation = _convert_dataset_to_table_and_relation(dataset)
        if table:
            tables.append(table)
            table_name_map[dataset["name"]] = table.name
        if relation:
            relations.append(relation)
    
    # Convert join_paths to joins
    joins = []
    join_paths = osi_model.get("join_paths", [])
    if not join_paths and osi_model.get("relationships"):
        # Fallback for old naming convention
        join_paths = osi_model.get("relationships", [])
    
    for join_path in join_paths:
        join = _convert_join_path_to_join(join_path, table_name_map)
        if join:
            joins.append(join)

    # Ontology is a top-level element in the input YAML, not nested inside semantic_model.
    ontology_source = top_level_ontology if top_level_ontology else osi_model.get("ontology", [])
    ontology_concepts, ontology_mappings = _convert_ontology_to_concepts_and_mappings(ontology_source)
    
    return LegendDatabase(
        name=name,
        package=package,
        description=description if description else None,
        tables=tables,
        relations=relations,
        joins=joins,
        ontology_concepts=ontology_concepts,
        ontology_mappings=ontology_mappings,
    )


def _convert_ontology_to_concepts_and_mappings(
    ontology: Any,
) -> tuple[list[OntologyConcept], list[OntologyEntityMapping]]:
    """Convert OSI ontology concepts to Pure classes and Legend mappings."""
    if not isinstance(ontology, list):
        return [], []

    concepts: list[OntologyConcept] = []
    mappings: list[OntologyEntityMapping] = []
    for concept_def in ontology:
        if not isinstance(concept_def, dict):
            continue

        concept_name = concept_def.get("concept")
        if not concept_name:
            continue

        concept_extends = concept_def.get("extends", [])
        if not isinstance(concept_extends, list):
            concept_extends = []

        identify_by = concept_def.get("identify_by", [])
        if not isinstance(identify_by, list):
            identify_by = []

        properties = _convert_relationships_to_properties(
            concept_def.get("relationships", []),
            identify_by,
        )

        concepts.append(
            OntologyConcept(
                name=concept_name,
                extends=[str(ext) for ext in concept_extends if ext],
                properties=properties,
                description=concept_def.get("description") or None,
            )
        )

        mappings.extend(
            _convert_entities_to_mappings(
                concept_name=concept_name,
                entities=concept_def.get("entities", []),
            )
        )

    return concepts, mappings


def _convert_entities_to_mappings(
    concept_name: str,
    entities: Any,
) -> list[OntologyEntityMapping]:
    """Convert ontology entities section to Legend mapping definitions."""
    if not isinstance(entities, list):
        return []

    mapping_items: list[OntologyEntityMapping] = []
    for entity_def in entities:
        if not isinstance(entity_def, dict):
            continue

        role_maps = entity_def.get("entity", [])
        if not isinstance(role_maps, list):
            continue

        source_dataset: Optional[str] = None
        property_mappings: list[OntologyEntityPropertyMapping] = []

        for role_map in role_maps:
            if not isinstance(role_map, dict):
                continue

            role = role_map.get("role")
            value = role_map.get("value")
            if not (isinstance(role, str) and isinstance(value, str)):
                continue

            role_parts = role.split(".", 1)
            property_name = role_parts[1] if len(role_parts) == 2 else role

            value_parts = value.rsplit(".", 1)
            if len(value_parts) != 2:
                continue
            current_dataset, source_field = value_parts

            if source_dataset is None:
                source_dataset = current_dataset

            if current_dataset != source_dataset:
                continue

            property_mappings.append(
                OntologyEntityPropertyMapping(
                    property_name=property_name,
                    source_field=source_field,
                )
            )

        if source_dataset and property_mappings:
            mapping_items.append(
                OntologyEntityMapping(
                    concept_name=concept_name,
                    source_dataset=source_dataset,
                    property_mappings=property_mappings,
                )
            )

    return mapping_items


def _convert_relationships_to_properties(
    relationships: Any,
    identify_by: list[Any],
) -> list[OntologyProperty]:
    """Convert concept-local relationships to Pure class properties."""
    if not isinstance(relationships, list):
        return []

    properties: list[OntologyProperty] = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue

        rel_name = relationship.get("name")
        if not rel_name:
            continue

        roles = relationship.get("roles", [])
        if not isinstance(roles, list):
            roles = []
        rel_multiplicity = relationship.get("multiplicity")
        rel_description = relationship.get("description")

        if not roles:
            properties.append(
                OntologyProperty(
                    name=rel_name,
                    type="Boolean",
                    multiplicity="[0..1]",
                    description=rel_description,
                )
            )
            continue

        multi_role = len(roles) > 1
        for idx, role in enumerate(roles):
            if not isinstance(role, dict):
                continue

            player = role.get("player")
            if not player:
                continue

            role_name = role.get("name") or player
            property_name = rel_name if not multi_role else f"{rel_name}_{role_name}"

            # For ManyToOne/OneToOne, the last role is functionally determined.
            multiplicity = "[*]"
            if rel_multiplicity in {"ManyToOne", "OneToOne"} and idx == len(roles) - 1:
                multiplicity = "[0..1]"

            properties.append(
                OntologyProperty(
                    name=property_name,
                    type=player,
                    multiplicity=multiplicity,
                    description=rel_description,
                    is_key=rel_name in identify_by,
                )
            )

    return properties


def _convert_dataset_to_table_and_relation(
    dataset: dict[str, Any],
) -> tuple[Optional[Table], Optional[Relation]]:
    """Convert OSI dataset to Legend Table and Relation.
    
    Maps:
    - dataset.name -> Table name / Relation name
    - dataset.source -> Table schema and name (parsed from source string)
    - dataset.fields -> Table columns / Relation column mappings
    - dataset.description -> descriptions
    - dataset.primary_key -> marks columns as PRIMARY KEY
    
    Returns:
        Tuple of (Table, Relation) where both can be None if invalid.
    """
    dataset_name = dataset.get("name")
    if not dataset_name:
        warnings.warn("Dataset missing 'name', skipping")
        return None, None
    
    source = dataset.get("source", dataset_name)
    description = dataset.get("description", "")
    primary_key_columns = set(dataset.get("primary_key", []))
    
    # Parse source string: "database.schema.table" or "schema.table" or "table"
    source_parts = source.split(".")
    if len(source_parts) >= 3:
        database = source_parts[0]
        schema = source_parts[1]
        table_name = source_parts[-1]
    elif len(source_parts) == 2:
        database = "default"
        schema = source_parts[0]
        table_name = source_parts[1]
    else:
        database = "default"
        schema = "public"
        table_name = source
    
    # Convert fields to columns and column mappings
    fields = dataset.get("fields", [])
    columns = []
    column_mappings = []
    
    for field in fields:
        field_name = field.get("name")
        if not field_name:
            continue
        
        # Infer type from field metadata or default to STRING
        field_type = _infer_field_type(field)
        is_nullable = field.get("nullable", True)
        field_description = field.get("description", "")
        is_pk = field_name in primary_key_columns
        
        column = Column(
            name=field_name,
            type=field_type,
            nullable=is_nullable,
            description=field_description if field_description else None,
            is_primary_key=is_pk,
        )
        columns.append(column)
        
        # Map relation field to physical column
        column_mappings.append(
            RelationColumnMapping(
                relation_field=field_name,
                physical_column=field_name,
            )
        )
    
    # Create physical table
    table = Table(
        name=table_name,
        schema=schema,
        database=database,
        columns=columns,
        description=description if description else None,
    )
    
    # Create logical relation pointing to the physical table
    relation = Relation(
        name=dataset_name,
        primary_table=table_name,
        description=description if description else None,
        column_mappings=column_mappings,
    )
    
    return table, relation


def _convert_join_path_to_join(
    join_path: dict[str, Any],
    table_name_map: dict[str, str],
) -> Optional[Join]:
    """Convert OSI join_path to Legend Join.
    
    Maps:
    - join_path.from -> fromRelation
    - join_path.to -> toRelation
    - join_path.from_columns -> fromColumns
    - join_path.to_columns -> toColumns
    """
    name = join_path.get("name")
    from_dataset = join_path.get("from")
    to_dataset = join_path.get("to")
    
    if not (name and from_dataset and to_dataset):
        warnings.warn(
            f"Incomplete join_path definition: {join_path}. Skipping."
        )
        return None
    
    from_columns = join_path.get("from_columns", [])
    to_columns = join_path.get("to_columns", from_columns)
    
    if not from_columns or not to_columns:
        warnings.warn(
            f"Join path '{name}' missing column definitions. Skipping."
        )
        return None
    
    return Join(
        name=name,
        from_relation=from_dataset,
        to_relation=to_dataset,
        from_columns=from_columns,
        to_columns=to_columns,
        join_type="INNER",  # Default; could be enhanced to read from OSI
    )


def _infer_field_type(field: dict[str, Any]) -> str:
    """Infer FINOS Legend type from OSI field metadata.
    
    Returns: Type string like "VARCHAR", "INTEGER", "DECIMAL", "TIMESTAMP", etc.
    """
    # Check custom extensions for type hints
    custom_exts = field.get("custom_extensions", [])
    for ext in custom_exts:
        if ext.get("vendor_name") == "FINOS":
            data = ext.get("data", {})
            if isinstance(data, str):
                import json
                try:
                    data = json.loads(data)
                except:
                    pass
            if isinstance(data, dict) and "type" in data:
                return data["type"]
    
    # Check dimension metadata to infer type
    dimension = field.get("dimension")
    if isinstance(dimension, dict):
        if dimension.get("is_time"):
            return "TIMESTAMP"
    
    # Check expression for hints (ANSI_SQL)
    expression = field.get("expression", {})
    dialects = expression.get("dialects", [])
    for dialect in dialects:
        if dialect.get("dialect") == "ANSI_SQL":
            expr = dialect.get("expression", "").upper()
            # Very simple heuristic matching
            if "DATE" in expr or "TIME" in expr:
                return "TIMESTAMP"
            if "INT" in expr:
                return "INTEGER"
            if "FLOAT" in expr or "DECIMAL" in expr:
                return "DECIMAL(18,2)"
            if "BOOL" in expr:
                return "BOOLEAN"
    
    # Default fallback
    return "VARCHAR(256)"
