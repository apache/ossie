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

"""Mapping between CDS built-in types and Ossie logical data types."""

from typing import Any, Optional

# CDS built-ins, plus the SAP HANA-specific ones CSN spells `cds.hana.*`.
_CDS_TO_OSSIE: dict[str, str] = {
    "cds.String": "String",
    "cds.LargeString": "String",
    "cds.UUID": "String",
    "cds.hana.CHAR": "String",
    "cds.hana.NCHAR": "String",
    "cds.hana.VARCHAR": "String",
    "cds.hana.CLOB": "String",
    "cds.Integer": "Integer",
    "cds.Int16": "Integer",
    "cds.Int32": "Integer",
    "cds.Int64": "Integer",
    "cds.Integer64": "Integer",
    "cds.UInt8": "Integer",
    "cds.hana.SMALLINT": "Integer",
    "cds.hana.TINYINT": "Integer",
    "cds.Decimal": "Decimal",
    "cds.DecimalFloat": "Decimal",
    "cds.hana.SMALLDECIMAL": "Decimal",
    "cds.Double": "Float",
    "cds.hana.REAL": "Float",
    "cds.Boolean": "Boolean",
    "cds.Date": "Date",
    "cds.Time": "Time",
    "cds.DateTime": "DateTime",
    "cds.Timestamp": "DateTime",
}

# Types with no portable Ossie equivalent: carried as Opaque.
_CDS_OPAQUE = frozenset(
    {
        "cds.Binary",
        "cds.LargeBinary",
        "cds.Vector",
        "cds.hana.BINARY",
        "cds.hana.ST_POINT",
        "cds.hana.ST_GEOMETRY",
    }
)

_OSSIE_TO_CDS: dict[str, str] = {
    "String": "cds.String",
    "Integer": "cds.Integer",
    "Decimal": "cds.Decimal",
    "Float": "cds.Double",
    "Boolean": "cds.Boolean",
    "Date": "cds.Date",
    "Time": "cds.Time",
    "DateTime": "cds.Timestamp",
    "DateTimeTz": "cds.Timestamp",
}

ASSOCIATION_TYPES = frozenset({"cds.Association", "cds.Composition"})


def builtin_type(type_name: Optional[str], definitions: dict[str, Any]) -> Optional[str]:
    """Resolve a (possibly user-defined) CSN type name down to a CDS built-in.

    Returns ``None`` when the chain ends in something that is not a known built-in
    (an unknown name, a structured type, or a cycle).
    """
    seen: set[str] = set()
    while type_name is not None and type_name not in seen:
        if type_name in _CDS_TO_OSSIE or type_name in _CDS_OPAQUE or type_name in ASSOCIATION_TYPES:
            return type_name
        seen.add(type_name)
        definition = definitions.get(type_name)
        if not isinstance(definition, dict):
            return None
        type_name = definition.get("type")
    return None


def ossie_datatype(builtin: Optional[str]) -> Optional[str]:
    """Ossie datatype for a CDS built-in, ``Opaque`` for a known non-portable one, else ``None``."""
    if builtin is None:
        return None
    if builtin in _CDS_TO_OSSIE:
        return _CDS_TO_OSSIE[builtin]
    if builtin in _CDS_OPAQUE:
        return "Opaque"
    return None


def cds_type(datatype: Optional[str]) -> Optional[str]:
    """CDS built-in for an Ossie datatype (``None`` for ``Opaque`` or an unset type)."""
    return _OSSIE_TO_CDS.get(datatype) if datatype else None


# SQL type names used when rendering / parsing CAST in expressions.
CDS_TO_SQL_TYPE: dict[str, str] = {
    "cds.String": "VARCHAR",
    "cds.LargeString": "VARCHAR",
    "cds.Integer": "INT",
    "cds.Int16": "SMALLINT",
    "cds.Int32": "INT",
    "cds.Int64": "BIGINT",
    "cds.Decimal": "DECIMAL",
    "cds.Double": "DOUBLE",
    "cds.Boolean": "BOOLEAN",
    "cds.Date": "DATE",
    "cds.Time": "TIME",
    "cds.DateTime": "TIMESTAMP",
    "cds.Timestamp": "TIMESTAMP",
}
