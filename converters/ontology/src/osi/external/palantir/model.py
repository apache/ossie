from __future__ import annotations

from enum import Enum


class Status(Enum):
    ACTIVE = 1
    DEPRECATED = 2
    EXPERIMENTAL = 3
    EXAMPLE = 4
    ENDORSED = 5
    INTERMEDIARY = 6    # This status had been introduced for testing purposes

class DataType(Enum):
    ANY = 1
    ATTACHMENT = 2
    BOOLEAN = 3
    DATE = 4
    DECIMAL = 5
    DOUBLE = 6
    FLOAT = 7
    GEOHASH = 8
    GEOPOINT = 9
    GEOSHAPE = 10
    INTEGER = 11
    LONG = 12
    SHORT = 13
    STRING = 14
    TIMESERIES = 15
    TIMESTAMP = 16
    TIME_DEPENDENT = 17
    STRUCT = 18
    VECTOR = 19
    MEDIA_REFERENCE = 20
    CIPHER_TEXT = 21

    @staticmethod
    def parse_datatype(name: str) -> DataType:
        try:
            return DataType[name.upper()]
        except KeyError:
            raise ValueError(f"Unrecognized data type: {name}")

    def to_type(self) -> str:
        if self in (DataType.STRING, DataType.GEOHASH, DataType.GEOSHAPE, DataType.GEOPOINT, DataType.TIMESERIES):
            return "String"
        elif self == DataType.INTEGER:
            return "Integer"
        elif self == DataType.DECIMAL:
            return "Decimal"
        elif self in (DataType.FLOAT, DataType.DOUBLE, DataType.TIME_DEPENDENT):
            return "Float"
        elif self == DataType.BOOLEAN:
            return "Boolean"
        elif self == DataType.TIMESTAMP:
            return "DateTime"
        elif self == DataType.DATE:
            return "Date"
        else:
            return "Integer"


class ArrayDataType:

    def __init__(self, t: DataType | ArrayDataType):
        self._base_type = t

    def base_type(self):
        return self._base_type

    def __str__(self):
        return f"array[{str(self._base_type)}]"

class Resource:

    def __init__(self, guid, rid):
        self._guid = guid
        self._readable_id = rid
        self._status = Status.ACTIVE

    def active(self):
        return self._status == Status.ACTIVE

    def experimental(self):
        return self._status == Status.EXPERIMENTAL

    def endorsed(self):
        return self._status == Status.ENDORSED

    def intermediary(self):
        return self._status == Status.INTERMEDIARY

    def guid(self):
        return self._guid

    def readable_id(self):
        return self._readable_id

    def set_status(self, stat:Status):
        self._status = stat

    def status(self):
        return self._status

class DataSetModel:

    _data_sets: dict[str, DataSet]

    def __init__(self):
        self._data_sets = {}

    def data_sets(self):
        return self._data_sets.values()

    def data_sets_map(self):
        return self._data_sets

    def info(self) -> str:
        result: list[str] = []
        for ds in self._data_sets.values():
            result.append(str(ds))
        return "\n".join(result)

class DataSet(Resource):

    def __init__(self, guid, rid):
        super().__init__(guid, rid)
        self._description: str | None = None
        self._path: str | None = None
        self._columns: list[DataSetColumn] = []
        self._depends_on: list[DataSet] = []

    def description(self):
        return self._description

    def path(self):
        return self._path

    def columns(self):
        return self._columns

    def depends_on(self):
        return self._depends_on

    def info(self, indent: int = 0, visited: set[str] | None = None) -> str:
        """
        Pretty-print this dataset with indentation and handle dependency graph
        (avoids infinite recursion on cycles by tracking visited mainDatasetIds).
        """
        pad = " " * indent
        if visited is None:
            visited = set()
        lines: list[str] = []
        ds_id = str(self._guid) if self._guid is not None else "None"
        name = str(self._readable_id) if self._readable_id is not None else "None"

        header = f'{pad}DataSet(id="{ds_id}", name="{name}")'
        lines.append(header)
        if self._description:
            lines.append(f"{pad}  description: {self._description}")
        if self._path:
            lines.append(f"{pad}  path: {self._path}")

        # Columns
        if self._columns:
            lines.append(f"{pad}  columns:")
            for col in self._columns:
                lines.append(col.info(indent + 4))

        # Dependencies
        if self._depends_on:
            lines.append(f"{pad}  depends_on:")
            if ds_id in visited:
                lines.append(f"{pad}    <cycle detected, already visited {ds_id}>")
            else:
                visited.add(ds_id)
                for dep in self._depends_on:
                    # Safeguard when dep is None
                    if dep is None:
                        lines.append(f"{pad}    <None>")
                        continue
                    lines.append(dep.info(indent + 4, visited))
                visited.remove(ds_id)

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.info()

class DataSetColumn:
    def __init__(self, name, t, ds: DataSet):
        self._type = t
        self._name = name
        self._part_of = ds

    def name(self):
        return self._name

    def type(self):
        return self._type

    def part_of(self) -> DataSet:
        return self._part_of

    def info(self, indent: int = 0) -> str:
        return f'{" " * indent}Column(name="{self._name}", type="{self._type}", part_of="{self._part_of.readable_id()}")'

    def __str__(self) -> str:
        return self.info()

class Ontology:

    _data_sets: dict[str, DataSet]
    _object_types: dict[str, ObjectType]
    _object_types_by_readable_id: dict[str, ObjectType]
    _relations: dict[str, Relation]
    _relations_by_readable_id: dict[str, Relation]
    _intermediary_relations: dict[str, IntermediaryRelation]

    def __init__(self):
        self._data_sets = {}
        self._object_types = {}
        self._object_types_by_readable_id = {}
        self._relations = {}
        self._relations_by_readable_id = {}
        self._intermediary_relations = {}

    def add_object_type(self, ot):
        self._object_types[ot.guid()] = ot
        self._object_types_by_readable_id[ot.readable_id()] = ot
        return self

    def add_relation(self, rel):
        self._relations[rel.guid()] = rel
        self._relations_by_readable_id[rel.readable_id()] = rel
        return self

    def object_types(self):
        return self._object_types

    def object_type_by_readable_id(self, rid):
        return self._object_types_by_readable_id[rid]

    def relations(self):
        return self._relations

    def relation_by_readable_id(self, rid):
        return self._relations_by_readable_id[rid]

    def intermediary_relations(self):
        return self._intermediary_relations

    def data_sets(self):
        return self._data_sets

    def set_data_sets(self, data_sets: dict[str, DataSet]):
        self._data_sets = data_sets

    def info(self, indentation="") -> str:
        result: list[str] = []

        for ot in sorted(self._object_types.values(), key=lambda x: x.guid()):
            result.append(ot.info())
            result.append("")

        for ds in sorted(self._data_sets.values(), key=lambda x: x.guid()):
            ds_name = ds.readable_id()
            result.append(indentation + f"Data set '{ds_name}':")
            for col in sorted(ds.columns(), key=lambda x: x.name()):
                result.append(indentation + f"   Column '{col.name()}' of type '{col.type()}'")
            result.append("")

        for rel in sorted(self._relations.values(), key=lambda x: x.guid()):
            result.append(indentation + rel.info())
            result.append("")

        for ir in sorted(self._intermediary_relations.values(), key=lambda x: x.guid()):
            result.append(indentation + ir.info())
            result.append("")

        return "\n".join(result)

    def subtypes_relations(self) -> dict[ObjectType, ManyToOneRelation]:
        result: dict[ObjectType, ManyToOneRelation] = {}
        for rel in self._relations.values():
            if not isinstance(rel, ManyToOneRelation):
                continue

            rel_exp_eligible = (
                    rel.experimental()
                    and rel.one_object_type().active()
                    and rel.many_object_type().active()
            )
            if not (rel.active() or rel_exp_eligible):
                continue

            one_ot = rel.one_object_type()
            many_ot = rel.many_object_type()

            if not rel.property_map():
                continue

            is_subtype = all(
                mprop in many_ot.primary_keys() and oprop in one_ot.primary_keys()
                for mprop, oprop in rel.property_map().items()
            )

            if is_subtype:
                result[one_ot] = rel

        return result

# An ObjectType is Palantir's analog of an EntityType. Its instances are
# identified by its primary-key Properties, which appear in the JSON as
# ReadingIds in an array, e.g.:
#
# {
#   "rid" : <resource-id>
#   "primaryKeys" : [
#     <reading-id>
#     ...
#   ]
# }
#
class ObjectType(Resource):

    def __init__(self, guid, rid, name):
        super().__init__(guid, rid)
        self._name = name
        self._type_groups = []
        self._syncs_from = []
        self._properties = {}
        self._pk_properties = set()
        self._data_sources = []

    def lookup_property_by_reading(self, pname):
        for prop in self._properties.values():
            if prop.readable_id() == pname:
                return prop
        return None


    def name(self):
        return self._name

    def type_groups(self):
        return self._type_groups

    def syncs_from(self):
        if not self._syncs_from:
            raise RuntimeError(f"Mandatory constraint violation: ObjectType '{self.readable_id()}' must sync with some DataSet")
        return self._syncs_from

    def sync_from_data_set(self, ds):
        self._syncs_from.append(ds)
        return self

    def properties(self):
        return self._properties

    def primary_keys(self):
        return self._pk_properties

    def data_sources(self) -> list[DataSource]:
        if not self._data_sources:
            raise RuntimeError(f"Mandatory constraint violation: ObjectType '{self.readable_id()}' must have some data source")
        return self._data_sources

    def set_properties(self, properties):
        self._properties = properties

    def set_primary_keys(self, pk_properties):
        self._pk_properties = pk_properties

    def info(self, indent: int = 0) -> str:
        keys = ", ".join([prop.readable_id() for prop in self._pk_properties])
        result: list[str] = [f'{" " * indent}Object type "{self._name}({keys})":']
        if self._syncs_from:
            for ds in self._syncs_from:
                result.append(f'{" " * (indent + 4)}Syncs from "{ds.readable_id()}"')
        if self._properties:
            for p in self._properties.values():
                result.append(p.info(indent + 4))
        if self._type_groups:
            result.append(f'{" " * (indent + 4)}Belongs to type groups:')
            for tg in self._type_groups:
                result.append(f'{" " * (indent + 8)} "{tg}"')
        return "\n".join(result)

    def __str__(self) -> str:
        return self.info()

class DataSource:
    def __init__(self, backing_dataset_id, backing_datasource_id):
        self._backing_dataset_id = backing_dataset_id
        self._backing_datasource_id = backing_datasource_id

    def backing_dataset_id(self):
        return self._backing_dataset_id

    def backing_datasource_id(self):
        return self._backing_datasource_id

class Property(Resource):

    def __init__(self, guid, rid, t, ot: ObjectType, column_name, datasource_resource_id):
        super().__init__(guid, rid)
        self._part_of = ot
        self._type = t
        self._column_name = column_name
        self._datasource_resource_id = datasource_resource_id
        self._pk_mapping = {}

    def part_of(self) -> ObjectType:
        return self._part_of

    def type(self):
        return self._type

    def column_name(self):
        return self._column_name

    def datasource_resource_id(self):
        return self._datasource_resource_id

    def pk_mapping(self):
        return self._pk_mapping

    def info(self, indent: int = 0) -> str:
        result = f'{" " * indent}Property "{self.readable_id()}" has data type "{str(self._type)}"'
        if self._column_name and not self._pk_mapping:
            result += f'\n{" " * (indent + 4)}Refers to "{str(self._column_name)}" of "{self._datasource_resource_id}" dataset'
        if self._pk_mapping:
            for k,v in self._pk_mapping.items():
                result += f'\n{" " * (indent + 4)}Maps to primary key column "{v}" of "{k}" dataset'
        return result

    def __str__(self) -> str:
        return self.info()

# In Palantir, a Relation is a binary relation whose roles are played
# by ObjectTypes rather than DataTypes -- i.e., entity types rather
# than value types. They come in two forms: ManyToOne and ManyToMany.
#
# Palantir does not model roles or constraints directly. Instead, Relations
# represent roles using Properties of the ObjectTypes that play the role.
#
class Relation(Resource):

    def __init__(self, guid, rid):
        super().__init__(guid, rid)

    def info(self) -> str:
        return ""

# A ManyToOneRelation is a binary relation with a uniqueness constraint that spans
# the "many" role. These objects are populated from a JSON message that looks like this:
#
#  {
#    "definition": {
#      "type" : "oneToMany",
#      "oneToMany" : {
#        "objectTypeRidOneSide" : <object-type-resource-id>,
#        "objectTypeIdOneSide" : <reading-id>,
#        ...
#      },
#      "objectTypeIdManySide" : <reading-id>,
#      "objectTypeRidManySide" : <object-type-resource-id>,
#      "oneSidePrimaryKeyToManySidePropertyMapping" : {
#        <property-resource-id> : <property-resource-id>,  // one-object-property -> many-object-fk-property
#        ...
#        <property-resource-id> : <property-resource-id>,  // one-object-property -> many-object-fk-property
#      },
#      "rid" : <relation-resource-id>
#    }
#  }
#
# Consider the conceptual relationship "Subscription is part of Account" with a UC on
# the Subscription role. Subscription then plays the "many" role, and Account plays the
# "one" role. The resource ids for the role players can be found using these paths:
#
#   - <definition.objectTypeRidManySide> for Subscription, and
#   - <definition.oneToMany.objectTypeRidOneSide> for Account
# 
# Such relations are implemented using one or more Properties of the ObjectType that
# plays the many role. Each of these Properties is interpreted as a foreign-key
# reference to a Property of the ObjectType that plays the one role. Because an ObjectType
# might have a compound key, there will be as many properties in the ObjectType that
# plays the many role as there are key properties in the ObjectType that plays the one
# role. And while we might naturally think about representing the correspondence between
# FK properties of the "many" object type to properties of the "one" object type, for
# some reason Palantir represents this in the reverse direction, which is equivalent,
# just weird. This is captured in the "oneSidePrimaryKeyToManySidePropertyMapping"
# message.
#
class ManyToOneRelation(Relation):

    def __init__(self, guid, rid, many_object_type: ObjectType, one_object_type: ObjectType,
                 property_map: dict[Property, Property]):
        super().__init__(guid, rid)
        self._one_object_type = one_object_type
        self._many_object_type = many_object_type
        # We map the property from the many object type to the property of the one object type
        self._property_map = property_map

    def info(self) -> str:
        one_role = self._one_object_type
        many_role = self._many_object_type
        return f'Relation "{self.readable_id()}" maps "{many_role._name}" to "{one_role._name}"'

    def many_object_type(self):
        return self._many_object_type

    def one_object_type(self):
        return self._one_object_type

    def property_map(self):
        return self._property_map

# A ManyToManyRelation is a binary relation with a uniqueness constraint that spans
# both of its roles. These are populated from a JSON message that looks like this:
#
#  {
#    "type": "manyToMany",
#    "objectTypeRidA" : <object-type-resource-id>,
#    "objectTypeRidB" : <object-type-resource-id>,
#    "objectTypeIdA" : <reading-id>,
#    "objectTypeIdB" : <reading-id>,
#    "objectTypeAPrimaryKeyPropertyMapping" : {
#        <property-resource-id> : <property-resource-id>,  // a-object-property -> join-table-property
#        ...
#    },
#    "objectTypeBPrimaryKeyPropertyMapping" : {
#        <property-resource-id> : <property-resource-id>,  // b-object-property -> join-table-property
#        ...
#    },
#    "joinTableDataSource": {
#      "backingResourceRid" : <dataset-resource-id>,
#      "datasourceRid" : <datasource-resource-id>
#    }
#  }
#
class ManyToManyRelation(Relation):
    def __init__(self, guid, rid, role_a_object_type: ObjectType, role_b_object_type: ObjectType,
                 role_a_property_map: dict[Property, str], role_b_property_map: dict[Property, str]):
        super().__init__(guid, rid)
        self._role_a_object_type = role_a_object_type
        self._role_b_object_type = role_b_object_type
        #
        # We map properties from each of the two roles' object types to the id of a property
        # of the backing resource. Notice that we map to <resource-id> rather than Property
        # because the JSON form we are using may not record property information about the
        # backing resource.
        #
        self._role_a_property_map = role_a_property_map
        self._role_b_property_map = role_b_property_map
        #
        self._backing_dataset_id = None
        self._backing_datasource_id = None
        #
        self._data_set: DataSet | None = None

    def role_a_player(self):
        return self._role_a_object_type

    def role_b_player(self):
        return self._role_b_object_type

    def role_a_property_map(self):
        return self._role_a_property_map

    def role_b_property_map(self):
        return self._role_b_property_map

    def backing_dataset_id(self):
        if self._backing_dataset_id is None:
            raise RuntimeError(f"Mandatory constraint violation: ManyToManyRelation '{self.readable_id()}' must name a backing dataset resource-id")
        return self._backing_dataset_id

    def backing_datasource_id(self):
        if self._backing_datasource_id is None:
            raise RuntimeError(f"Mandatory constraint violation: ManyToManyRelation '{self.readable_id()}' must name a backing datasource resource-id")
        return self._backing_datasource_id

    def data_set(self):
        if self._data_set is None:
            raise RuntimeError(f"Mandatory constraint violation: ManyToManyRelation '{self.readable_id()}' must have some DataSet")
        return self._data_set

    def set_backing_dataset_id(self, id):
        self._backing_dataset_id = id

    def set_backing_datasource_id(self, id):
        self._backing_datasource_id = id

    def info(self):
        result = [f'Relation "{self.readable_id()}" associates "{self.role_a_player()._name}" with "{self.role_b_player()._name}"']
        if self._data_set:
            result.append(f'    DataSet "{self.data_set().readable_id()}"')
        return "\n".join(result)


class IntermediaryRelation(Relation):
    def __init__(self, guid, rid, role_a_object_type: ObjectType, role_b_object_type: ObjectType,
                 intermediary_object_type: ObjectType, relation_a_rid: str, relation_b_rid: str):
        super().__init__(guid, rid)
        self._role_a_object_type = role_a_object_type
        self._role_b_object_type = role_b_object_type
        self._intermediary_object_type = intermediary_object_type
        self._relation_a = relation_a_rid
        self._relation_b = relation_b_rid

    def role_a_player(self):
        return self._role_a_object_type

    def role_b_player(self):
        return self._role_b_object_type

    def intermediary_player(self):
        return self._intermediary_object_type

    def relation_a(self):
        return self._relation_a

    def relation_b(self):
        return self._relation_b

    def info(self):
        return (f'Relation "{self.readable_id()}" associates "{self.role_a_player()._name}" with '
                f'"{self.role_b_player()._name}" via intermediary player "{self.intermediary_player()._name}" and '
                f'relations "{self.relation_a()}" and "{self.relation_b()}"')

