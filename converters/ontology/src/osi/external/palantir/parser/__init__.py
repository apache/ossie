import json
import warnings
import zipfile
from io import IOBase
from pathlib import Path
from typing import Any, Iterable

from osi.common.utils import camel_to_snake
from osi.external.palantir.model import DataSet, DataSetColumn, DataSetModel, ObjectType, Ontology, DataType, \
    ArrayDataType, Property, Status, ManyToOneRelation, Relation, ManyToManyRelation, IntermediaryRelation, DataSource
from osi.common.file_utils import iter_json_files_from_dir_in_zip, open_top_level_file_from_zip, \
    iter_json_files_from_dir, get_top_level_json_file_from_dir, validate_dir


# Helper functions to aid in parsing. Palantir's JSON exports can be inconsistent in their formatting, especially
# across versions. For example, some fields that are expected to be strings may sometimes be empty strings or
# missing entirely, and some fields that are expected to be lists may sometimes be singletons or missing entirely.
# These helper functions normalize these inconsistencies to make parsing easier.

def norm(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    return v if v.strip() else None

def set_if_value(curr: str | None, new_val: str | None) -> str | None:
    # Only set when new_val is not None (i.e., not empty string or missing)
    return new_val if new_val is not None else curr

def get_dict(d, key):
    v = d.get(key)
    return v if isinstance(v, dict) else {}

def get_list(d, key):
    v = d.get(key)
    return v if isinstance(v, list) else []

# DataSets in Palantir have their own JSON format that is separate from the Ontology JSON format.
class PalantirDataSetParser:

    _model: DataSetModel

    def __init__(self):
        self._model = DataSetModel()

    def model(self):
        return self._model

    def _dataset_from_dict(self, d: dict[str, Any], registry: dict[str, DataSet]) -> DataSet | None:
        ds_id = norm(d.get("mainDatasetId"))

        if not ds_id:
            return None

        # Reuse or create instance; do not return early to ensure children are populated
        ds = registry.get(ds_id, None)
        if ds is None:
            ds = DataSet(ds_id, norm(d.get("datasetName")))
            registry[ds_id] = ds

        # Scalars without overriding with empty values
        ds._path = set_if_value(ds.path(), norm(d.get("datasetPath")))
        ds._readable_id = set_if_value(ds.readable_id(), norm(d.get("datasetName")))
        ds._description = set_if_value(ds.description(), norm(d.get("description")))

        # Columns
        ds_schema = d.get("datasetSchema")
        if isinstance(ds_schema, list):
            cols: list[DataSetColumn] = []
            for item in ds_schema:
                if isinstance(item, dict):
                    cols.append(DataSetColumn(item.get("name"), item.get("type"), ds))
            ds._columns = cols  # only when provided as a proper list

        # Dependencies
        raw_inputs = d.get("inputDatasetIds")
        if isinstance(raw_inputs, list):
            inputs: list[DataSet] = []
            for item in raw_inputs:
                if not isinstance(item, dict):
                    continue
                child = self._dataset_from_dict(item, registry)
                if child is not None:
                    inputs.append(child)
            ds._depends_on = inputs

        return ds

    def parse(self, file: IOBase):
        data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("Top-level JSON must be an array of datasets")

        registry: dict[str, DataSet] = {}
        for item in data:
            if isinstance(item, dict):
                ds = self._dataset_from_dict(item, registry)
                if ds:
                    self.model().data_sets_map()[ds.guid()] = ds

#
# The constructs declared within a Palantir ontology refer to one another using one
# or both of two different reference schemes:
#  - Resource ids, which are essentially GUIDs, and
#  - Readable ids, which are human-readable strings that are not guaranteed to be unique
#    but are more stable across versions and easier to work with.
# The parser extracts both forms of identifiers for each construct and builds lookup maps
# keyed by both forms of identifier to make it easier to resolve references regardless of
# which form they use. In general, resource ids correspond to the 'rid' JSON key, while
# readable ids correspond to the 'id' or 'apiName' JSON keys.
#
class PalantirOntologyParser:
    _model: Ontology

    def __init__(self):
        self._model = Ontology()

    def model(self):
        return self._model

    def parse(self, file: IOBase):
        data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Top-level JSON must be a dictionary of Ontology data")

        # Object Types
        object_types, object_types_by_readable_id = self._parse_object_types(data)
        self._model._object_types = object_types
        self._model._object_types_by_readable_id = object_types_by_readable_id

        # ManyToOneRelations
        self._model._relations, self._model._intermediary_relations = self._parse_relations(data, object_types)
        self.validate_intermediary_relations()

        self._parse_extra(data)

    # Given a Raw Palantir ObjectType, extract the string to use as its name regardless
    # of JSON convention
    def _parse_object_type_name(self, raw_ot):
        # Newer JSONs contain a displayMetadata section with this information
        display_metadata = get_dict(raw_ot, "displayMetadata")
        if display_metadata:
            ot_name = norm(display_metadata.get("displayName"))
        else:
            ot_name = norm(raw_ot.get("displayName"))

        if ot_name is None:
            raise ValueError(f'Could not extract a name from ObjectType with rid: {raw_ot.get("rid")}')
        return ot_name

    def _parse_property_backing_data(self, raw_prop, property_id, object_type_id):
        # In the new exports this information stores in the `source` field, but in the old exports
        # it leaves in the `column` and `datasourceRid` fields. We need to support both cases.
        # Really old format doesn't even have column/datasource info - so use property name as column
        source = get_dict(raw_prop, "source")
        column_name = norm(source.get("columnName")) or norm(raw_prop.get("column")) or property_id
        backing_datasource_id = norm(source.get("datasourceBackingResourceRid")) or norm(raw_prop.get("datasourceRid")) or object_type_id

        return (column_name, backing_datasource_id)

    def _parse_object_types(self, data: dict) -> tuple[dict[str, ObjectType], dict[str, ObjectType]]:
        object_types = {}
        object_types_by_readable_id = {}
        for raw_ot in get_list(data, "objectTypes"):
            guid = norm(raw_ot.get("rid"))
            if not guid:
                raise ValueError("Object type `rid` field must be non-empty")
            # Support both formats: new (id) and old (apiName)
            readable_id = norm(raw_ot.get("id")) or norm(raw_ot.get("apiName"))

            # Extract the ObjectType's name
            ot_name = self._parse_object_type_name(raw_ot)

            object_type = ObjectType(guid, readable_id, ot_name)

            object_type._type_groups = get_list(raw_ot, "typeGroups")

            status_message = get_dict(raw_ot, "status")
            if status_message:
                object_type.set_status(self._get_status(norm(status_message.get("type"))))

            object_types[guid] = object_type
            object_types_by_readable_id[readable_id] = object_type

            data_sources = get_list(raw_ot, "datasources")
            if len(data_sources) < 1:
                # No backing datasource? This is common in old versions of the JSON.
                # Then create one that uses the same identifier as the ObjectType it backs.
                object_type._data_sources.append(DataSource(readable_id, readable_id))
            else:
                for data_source in data_sources:
                    datasource_rid = norm(data_source.get("datasourceRid"))
                    backing_resource_rid = norm(data_source.get("backingResourceRid"))

                    if not datasource_rid or not backing_resource_rid:
                        raise ValueError("Object type fields `datasourceRid` and `backingResourceRid` must be non-empty")

                    object_type._data_sources.append(DataSource(backing_resource_rid, datasource_rid))


            properties = {}
            properties_by_readable_id = {}

            # Support both formats: list (new) and dict (old)
            raw_properties = raw_ot.get("properties", [])
            if isinstance(raw_properties, dict):
                # Old format: properties is a dict keyed by property name
                raw_properties = list(raw_properties.values())

            for raw_prop in raw_properties:
                # Parse type (supports nested arrays)
                # Old format uses 'dataType', new uses 'baseType'
                raw_base_type = get_dict(raw_prop, "baseType") or get_dict(raw_prop, "dataType")
                prop_type = self._parse_datatype_node(raw_base_type)

                # Support both formats: new (id) and old (apiName)
                prop_id = norm(raw_prop.get("id")) or norm(raw_prop.get("apiName"))
                prop_guid = norm(raw_prop.get("rid"))
                if not prop_guid or not prop_id:
                    warnings.warn(f"Skipping property with missing id/rid in object type '{ot_name}'")
                    continue

                (column_name, backing_datasource_id) = self._parse_property_backing_data(raw_prop, prop_id, guid)

                prop_name = prop_id
                prop = Property(prop_guid, prop_name, prop_type, object_type, column_name, backing_datasource_id)

                status_message = get_dict(raw_prop, "status")
                if status_message:
                    prop.set_status(self._get_status(norm(status_message.get("type"))))

                # This information exists only in the latest exports
                primary_key_mapping = get_dict(raw_prop, "primaryKeyMapping")
                if primary_key_mapping:
                    pk_mapping = {}
                    for k,v in primary_key_mapping.items():
                        pk_column_name = norm(v.get("columnName"))
                        pk_mapping[k] = pk_column_name
                    prop._pk_mapping = pk_mapping

                properties[prop_guid] = prop
                properties_by_readable_id[prop_name] = prop

            object_type._properties = properties

            pk_properties = set()
            # Support both formats: primaryKeys (list) and primaryKey (string)
            pk_list = get_list(raw_ot, "primaryKeys")
            if not pk_list:
                single_pk = norm(raw_ot.get("primaryKey"))
                if single_pk:
                    pk_list = [single_pk]
            for raw_pk_prop in pk_list:
                pk_property = properties_by_readable_id.get(raw_pk_prop, None)
                if pk_property is None:
                    warnings.warn(f"Property '{raw_pk_prop}' is not defined in object type '{ot_name}' - skipping as primary key")
                    continue
                pk_properties.add(pk_property)

            object_type._pk_properties = pk_properties

        return object_types, object_types_by_readable_id


    def _parse_raw_relation_id(self, raw_relation):
        return norm(raw_relation.get("id")) or norm(raw_relation.get("apiName"))

    def _parse_raw_relation_guid(self, raw_relation):
        return norm(raw_relation.get("rid")) or norm(raw_relation.get("linkTypeRid"))

    # Assumes raw_relation is a "MANY" relation and looks to make sure that it is
    # an alternative reading of a "ONE" relation
    def _verify_alternative_reading_of(self, raw_relation, all_relations):
        id = raw_relation.get("linkTypeRid")
        for r in all_relations:
            if r.get("cardinality") == 'ONE':
                if r.get("linkTypeRid") == id:
                    return True
        return False

    def _parse_source_and_target(self, raw_relation, object_types):
        # In the old format, sourceObjectType/targetObjectType name the source and target
        # using readings rather than guids.
        source_ot = norm(raw_relation.get("sourceObjectType"))
        target_ot = norm(raw_relation.get("targetObjectType"))

        # Look up object types by apiName (readable_id)
        source_object_type = None
        target_object_type = None
        for ot in object_types.values():
            if ot.readable_id() == source_ot:
                source_object_type = ot
            if ot.readable_id() == target_ot:
                target_object_type = ot
        return (source_object_type, target_object_type)

    # The old style JSON format supports only ManyToOne relations and uses a simpler format.
    def _parse_old_style_relation(self, raw_relation, object_types):
        id = self._parse_raw_relation_id(raw_relation)
        (source_object_type, target_object_type) = self._parse_source_and_target(raw_relation, object_types)

        if not source_object_type or not target_object_type:
            warnings.warn(f"Skipping relation {self._parse_raw_relation_id(raw_relation)}: source or target object type not found")
            return None

        # target_object_type must comprise exactly one primary-key property
        if len(target_object_type.primary_keys()) == 1:

            # Choose the lone property from the set of target_object_type's primary key properties
            target_object_pk_property = next(iter(target_object_type.primary_keys()))

            # Look up the name of the source property that is a foreign key reference
            # to target_object_type's primary key property
            source_property_name = norm(raw_relation.get("foreignKeyPropertyApiName"))
            if source_property_name is not None:
                fk_property = source_object_type.lookup_property_by_reading(source_property_name)
                if fk_property is not None:
                    # Build property mapping from foreign key
                    property_map = { fk_property: target_object_pk_property }
                    guid = self._parse_raw_relation_guid(raw_relation)
                    return ManyToOneRelation(guid, id, source_object_type, target_object_type, property_map)

        warnings.warn(f"Skipping relation {id}: no foreign key mapping available.")
        return None

    def _parse_relations(self, data: dict, object_types: dict[str, ObjectType]) -> tuple[dict[str, Relation], dict[str, IntermediaryRelation]]:
        relations = {}
        intermediary_relations = {}

        all_relations = get_list(data, "relations")
        for raw_relation in all_relations:
            # Support both formats: new (id/rid) and old (apiName/linkTypeRid)
            relation_id = self._parse_raw_relation_id(raw_relation)
            relation_guid = self._parse_raw_relation_guid(raw_relation)

            if not relation_guid or not relation_id:
                # Skip relations without proper identifiers (can happen with SDK-extracted ontologies)
                warnings.warn(f"Skipping relation with missing id/rid: {raw_relation.get('apiName', 'unknown')}")
                continue

            relation_type = None
            definition = get_dict(raw_relation, "definition")
            if definition:
                relation_type = norm(definition.get("type"))

            relation: Relation | None = None

            if not definition:
                cardinality = norm(raw_relation.get("cardinality"))
                if cardinality == 'MANY':
                    if not self._verify_alternative_reading_of(raw_relation, all_relations):
                        warnings.warn(f'Encountered an unsupported ManyToMany relation {relation_id}')
                    continue

                # Otherwise, assume the cardinality is "ONE"
                relation = self._parse_old_style_relation(raw_relation, object_types)
                if relation is None:
                    continue

            elif relation_type and relation_type.lower() == "onetomany":
                one_to_many_dict = get_dict(definition, "oneToMany")
                relation = self._parse_many_to_one_relation(relation_guid, relation_id, one_to_many_dict, object_types)
            elif relation_type and relation_type.lower() == "intermediary":
                intermediary_dict = get_dict(definition, "intermediary")
                relation = self._parse_intermediary_relation(relation_guid, relation_id, intermediary_dict, object_types)
            else:
                many_to_many_dict = get_dict(definition, "manyToMany")
                relation = self._parse_many_to_many_relation(relation_guid, relation_id, many_to_many_dict, object_types)

            status_message = get_dict(raw_relation, "status")
            if status_message:
                relation.set_status(self._get_status(norm(status_message.get("type"))))

            if isinstance(relation, IntermediaryRelation):
                intermediary_relations[relation_guid] = relation
            else:
                relations[relation_guid] = relation

        return relations, intermediary_relations

    def validate_intermediary_relations(self):
        for r in self._model.intermediary_relations().values():
            # Validate that the intermediary relation's link types exist
            if r.relation_a() not in self._model.relations().keys():
                raise ValueError(
                    f"Relation with rid {r.relation_a()} is not defined for intermediary relation {r.guid()}")
            if r.relation_b() not in self._model.relations().keys():
                raise ValueError(
                    f"Relation with rid {r.relation_b()} is not defined for intermediary relation {r.guid()}")


    def _parse_many_to_one_relation(self, guid: str, id: str, raw: dict[Any, Any], object_types: dict[str, ObjectType]) -> Relation:

        one_object_type_rid = norm(raw.get("objectTypeRidOneSide"))
        many_object_type_rid = norm(raw.get("objectTypeRidManySide"))
        if not one_object_type_rid or not many_object_type_rid:
            raise ValueError("ManyToOne relation is missing objectTypeRid fields")

        try:
            one_object_type = object_types[one_object_type_rid]
            many_object_type = object_types[many_object_type_rid]
        except KeyError as e:
            raise ValueError(f"Object type {e.args[0]} is not defined") from None

        one_to_many_mapping = get_dict(raw, "oneSidePrimaryKeyToManySidePropertyMapping")
        if not one_to_many_mapping:
            raise ValueError("Relation definition must contain `oneSidePrimaryKeyToManySidePropertyMapping`")

        property_map: dict[Property, Property] = {}
        for k, v in one_to_many_mapping.items():
            try:
                one_property = one_object_type.properties()[k]
                many_property = many_object_type.properties()[v]
            except KeyError as e:
                raise ValueError(f"Property {e.args[0]} is not defined in object type {e.args[1]}") from None

            property_map[many_property] = one_property

        return ManyToOneRelation(guid, id, many_object_type, one_object_type, property_map)

    def _parse_many_to_many_relation(self, guid: str, id: str, raw: dict[Any, Any], object_types: dict[str, ObjectType]) -> Relation:

        role_a_object_type_rid = norm(raw.get("objectTypeRidA"))
        role_b_object_type_rid = norm(raw.get("objectTypeRidB"))
        if not role_a_object_type_rid or not role_b_object_type_rid:
            raise ValueError("ManyToMany relation is missing objectTypeRid fields")

        try:
            role_a_object_type = object_types[role_a_object_type_rid]
            role_b_object_type = object_types[role_b_object_type_rid]
        except KeyError as e:
            raise ValueError(f"Object type {e.args[0]} is not defined") from None

        def build_property_map(object_type, pk_mapping: dict[str, str]) -> dict[Property, str]:
            prop_map: dict[Property, str] = {}
            for src_prop_id, dst_prop_id in pk_mapping.items():
                try:
                    obj_prop = object_type.properties()[src_prop_id]
                except KeyError as e:
                    raise ValueError(f"Property {e.args[0]} is not defined in object type {e.args[1]}") from None
                prop_map[obj_prop] = dst_prop_id
            return prop_map

        role_a_pk_mapping = get_dict(raw, "objectTypeAPrimaryKeyPropertyMapping")
        if not role_a_pk_mapping:
            raise ValueError("Relation definition must contain `objectTypeAPrimaryKeyPropertyMapping`")

        role_a_property_map: dict[Property, str] = build_property_map(role_a_object_type, role_a_pk_mapping)

        role_b_pk_mapping = get_dict(raw, "objectTypeBPrimaryKeyPropertyMapping")
        if not role_b_pk_mapping:
            raise ValueError("Relation definition must contain `objectTypeBPrimaryKeyPropertyMapping`")

        role_b_property_map: dict[Property, str] = build_property_map(role_b_object_type, role_b_pk_mapping)

        relation = ManyToManyRelation(guid, id, role_a_object_type, role_b_object_type, role_a_property_map,
                                  role_b_property_map)

        join_table_data_source = get_list(raw, "joinTableDatasource")
        if len(join_table_data_source) != 1:
            raise ValueError("Relation definition must contain exactly one `joinTableDatasource`")

        datasource_rid = norm(join_table_data_source[0].get("datasourceRid"))
        backing_resource_rid = norm(join_table_data_source[0].get("backingResourceRid"))

        if not datasource_rid or not backing_resource_rid:
            raise ValueError("Relation fields `datasourceRid` and `backingResourceRid` must be non-empty")

        relation.set_backing_datasource_id(datasource_rid)
        relation.set_backing_dataset_id(backing_resource_rid)

        return relation

    def _parse_intermediary_relation(self, guid: str, id: str, raw: dict[Any, Any], object_types: dict[str, ObjectType]) -> Relation:
        role_a_object_type_rid = norm(raw.get("objectTypeRidA"))
        role_b_object_type_rid = norm(raw.get("objectTypeRidB"))
        intermediary_rid = norm(raw.get("intermediaryObjectTypeRid"))
        if not role_a_object_type_rid or not role_b_object_type_rid or not intermediary_rid:
            raise ValueError("Intermediary relation is missing objectTypeRid fields")

        try:
            role_a_object_type = object_types[role_a_object_type_rid]
            role_b_object_type = object_types[role_b_object_type_rid]
            intermediary_object_type = object_types[intermediary_rid]
        except KeyError as e:
            raise ValueError(f"Object type {e.args[0]} is not defined") from None

        a_to_intermediary_link_rid = norm(raw.get("aToIntermediaryLinkTypeRid"))
        intermediary_to_b_link_rid = norm(raw.get("intermediaryToBLinkTypeRid"))
        if not a_to_intermediary_link_rid or not intermediary_to_b_link_rid:
            raise ValueError("Intermediary relation is missing link type rid fields")

        return IntermediaryRelation(guid, id, role_a_object_type, role_b_object_type, intermediary_object_type,
                                    a_to_intermediary_link_rid, intermediary_to_b_link_rid)

    def _get_status(self, status):
        match status:
            case "active":
                return Status.ACTIVE
            case "deprecated":
                return Status.DEPRECATED
            case "experimental":
                return Status.EXPERIMENTAL
            case "example":
                return Status.EXAMPLE
            case "endorsed":
                return Status.ENDORSED
            # This status had been introduced for testing purposes
            case "intermediary":
                return Status.INTERMEDIARY
            case _:
                raise ValueError(f"Unrecognized Resource status {status}")

    def _parse_datatype_node(self, node) -> DataType | ArrayDataType:
        """
        node: {"type": "...", "subType": {...}} possibly nested arrays
        Returns DataType or ArrayDataType wrapping.
        """

        t = norm(node.get("type"))
        if not t:
            # Default to STRING for missing types
            return DataType.STRING

        if t.upper() == "ARRAY":
            # Support both camelCase (subType) and snake_case (sub_type)
            sub = get_dict(node, "subType") or get_dict(node, "sub_type")
            if not sub:
                # Default to STRING array if subType is missing
                return ArrayDataType(DataType.STRING)
            inner = self._parse_datatype_node(sub)
            return ArrayDataType(inner)

        # Non-array primitive
        return DataType.parse_datatype(t)


    def _parse_extra(self, data: dict) -> None:
        """Extension point for subclasses to parse additional data from the ontology JSON.

        Called at the end of :meth:`parse` with the fully-deserialized JSON dict.
        The base implementation is a no-op; override in a subclass to add domain-specific
        parsing (e.g. actions, workflows, validations) without re-reading the file.
        """

class PalantirParser:
    _model: Ontology | None

    def __init__(self):
        self._model = None

    def _make_ontology_parser(self) -> PalantirOntologyParser:
        return PalantirOntologyParser()

    def parse(self, path: Path) -> Ontology:
        # A Palantir export may arrive as a ZIP archive, an already extracted
        # folder, or a folder that simply wraps a single ZIP archive. Detect
        # which and process accordingly.
        if path.is_dir():
            wrapped_zip = self._single_zip_in_dir(path)
            if wrapped_zip is not None:
                with zipfile.ZipFile(wrapped_zip) as zf:
                    self._parse_from_zip(zf)
            else:
                self._parse_from_dir(path)
        elif path.is_file():
            if not zipfile.is_zipfile(path):
                raise ValueError(f"Unsupported Palantir source '{path}'. Expected a ZIP archive or a directory")
            with zipfile.ZipFile(path) as zf:
                self._parse_from_zip(zf)
        else:
            raise FileNotFoundError(f"Palantir source '{path}' does not exist")
        return self._model

    @staticmethod
    def _single_zip_in_dir(base_dir: Path) -> Path | None:
        """Return the lone ZIP archive inside ``base_dir`` when the folder wraps
        exactly one ``.zip`` file (and nothing else); otherwise ``None``. This
        mirrors the convenience of accepting a folder that just contains a
        Palantir export archive."""
        entries = list(base_dir.iterdir())
        if len(entries) == 1 and entries[0].is_file() and zipfile.is_zipfile(entries[0]):
            return entries[0]
        return None

    def _parse_from_zip(self, zf: zipfile.ZipFile):
        self._validate_archive(zf)

        def _data_set_streams() -> Iterable[IOBase]:
            for _name, fh in iter_json_files_from_dir_in_zip(zf, "data_sets"):
                yield fh

        try:
            with open_top_level_file_from_zip(zf, self._get_ontology_json_file_path(zf)) as ontology_fh:
                self._build_model(_data_set_streams(), ontology_fh)
        except FileNotFoundError as e:
            raise FileNotFoundError(str(e)) from e

    def _parse_from_dir(self, base_dir: Path):
        validate_dir(base_dir)
        ontology_path = get_top_level_json_file_from_dir(base_dir)

        def _data_set_streams() -> Iterable[IOBase]:
            for _name, fh in iter_json_files_from_dir(base_dir, "data_sets"):
                yield fh

        with ontology_path.open("rb") as ontology_fh:
            self._build_model(_data_set_streams(), ontology_fh)

    def _build_model(self, data_set_streams: Iterable[IOBase], ontology_stream: IOBase):
        """Build the ontology model from a stream of data set JSON files and the
        top-level ontology JSON stream. Shared by the ZIP and directory paths."""
        any_json = False
        data_sets: dict[str, DataSet] = {}
        for fh in data_set_streams:
            any_json = True
            try:
                parser = PalantirDataSetParser()
                parser.parse(fh)
                data_sets.update(parser.model().data_sets_map())
            finally:
                fh.close()
        if not any_json:
            raise ValueError("'data_sets' folder contains no JSON files")

        parser = self._make_ontology_parser()
        parser.parse(ontology_stream)
        model = parser.model()
        model.set_data_sets(data_sets)

        for ot in model.object_types().values():
            for ds in ot.data_sources():
                data_set = data_sets.get(ds.backing_dataset_id(), None)
                if data_set is None:
                    # For SDK-extracted ontologies with synthetic datasources,
                    # mainDatasetId in data_sets JSON matches the object type's RID
                    data_set = data_sets.get(ot.guid(), None)
                if data_set:
                    ot.sync_from_data_set(data_set)
                    # For SDK-extracted ontologies, property column_name defaults to
                    # the apiName (camelCase), but dataset columns use snake_case.
                    # Cross-reference to use the actual dataset column names.
                    ds_col_names = {col.name() for col in data_set.columns()}
                    for prop in ot.properties().values():
                        col_name = prop.column_name()
                        if col_name not in ds_col_names:
                            snake_name = camel_to_snake(col_name)
                            if snake_name in ds_col_names:
                                prop._column_name = snake_name
        for rel in model.relations().values():
            if isinstance(rel, ManyToManyRelation):
                rel._data_set = data_sets.get(rel.backing_dataset_id(), None)

        self._model = model

    def _validate_archive(self, zf: zipfile.ZipFile):
        """
        Ensure the ZIP archive contains a required 'data_sets/' directory.
        Accept either:
          - Top-level 'data_sets/' folder, or
          - A single-root folder with 'root/data_sets/' inside.
        """
        names = zf.namelist()

        # Fast path: direct presence at top-level or files under 'data_sets/'
        has_data_sets = any(
            n.endswith("/") and n.rstrip("/").endswith("data_sets") for n in names
        ) or any(n.startswith("data_sets/") for n in names)
        if has_data_sets:
            return

        # Single-root archives: if there's exactly one root folder, allow root/data_sets/
        roots = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(roots) == 1:
            root = next(iter(roots))
            has_rooted_data_sets = any(
                n.endswith("/") and n.rstrip("/").endswith(f"{root}/data_sets") for n in names
            ) or any(n.startswith(f"{root}/data_sets/") for n in names)
            if has_rooted_data_sets:
                return

        raise ValueError("Archive does not contain required 'data_sets' folder")

    def _get_ontology_json_file_path(self, zf: zipfile.ZipFile) -> str:
        """
        Find exactly one top-level JSON file and return its archive path.

        Rules:
          - "Top-level" means entries without '/' in their name.
          - If the archive is packaged under a single root directory, then
            "top-level" means entries directly under that root (exactly one '/').
          - There must be exactly one JSON at this level; otherwise raise.
        """
        names = zf.namelist()

        # Identify entries without any parent directory.
        top_level = [n for n in names if "/" not in n]

        # If nothing is at the real top-level, accept the case of a single root folder.
        if not top_level:
            roots = {n.split("/", 1)[0] for n in names if "/" in n}
            if len(roots) == 1:
                root = next(iter(roots))
                # Entries directly under the single root (e.g., 'root/file.json')
                top_level = [n for n in names if n.startswith(f"{root}/") and n.count("/") == 1]

        # Keep only JSON files
        json_candidates = [n for n in top_level if n.lower().endswith(".json")]

        # Enforce exactly one ontology JSON at the top level
        if len(json_candidates) == 0:
            raise ValueError("Archive must contain exactly one top-level JSON file (none found)")
        if len(json_candidates) > 1:
            raise ValueError("Archive must contain exactly one top-level JSON file (multiple found)")

        return json_candidates[0]
