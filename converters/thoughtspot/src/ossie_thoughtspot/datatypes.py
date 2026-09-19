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

"""The ThoughtSpot <-> Ossie datatype map, covering every data type each side supports.

Three properties of the map shape this module's surface. It is **not injective** — Decimal
and Float both become DOUBLE, and Time, DateTimeTz and Opaque collapse into types that
cannot carry them — so `declared_loss` names the types whose round trip is lossy in one
place rather than leaving each caller to rediscover them. Two types have a
**connection-dependent spelling** (BOOLEAN/BOOL, DOUBLE/FLOAT), which the forward direction
records so the return trip re-emits the same one. And `datatype` is **optional in Ossie but
compulsory in TML** — ThoughtSpot rejects a table whose column has no
`db_column_properties`, so `to_tml(None)` infers rather than raising.
"""
from __future__ import annotations

#: The closed Ossie datatype enum (core specification).
OSSIE_DATATYPES = frozenset({
    "String", "Integer", "Decimal", "Float", "Boolean",
    "Date", "Time", "DateTime", "DateTimeTz", "Opaque",
})

#: Ossie datatype -> the TML `data_type` written for it.
_TO_TML = {
    "String": "VARCHAR",
    "Integer": "INT64",
    "Decimal": "DOUBLE",
    "Float": "DOUBLE",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
    "Time": "VARCHAR",
    "DateTime": "DATE_TIME",
    "DateTimeTz": "DATE_TIME",
    "Opaque": "VARCHAR",
}

#: TML `data_type` -> the Ossie datatype emitted for it. Deliberately not the inverse of
#: `_TO_TML`: DOUBLE comes back as Decimal and VARCHAR as String, which is what makes the
#: types in `_DECLARED_LOSS` lossy.
_TO_OSSIE = {
    "VARCHAR": "String",
    "INT64": "Integer",
    "DOUBLE": "Decimal",
    "FLOAT": "Float",
    "BOOL": "Boolean",
    "BOOLEAN": "Boolean",
    "DATE": "Date",
    "DATE_TIME": "DateTime",
}

#: Types whose `Ossie -> TML -> Ossie` trip cannot return the original, and why. None of
#: these can be rescued by a stash: the stash is written from a TML document, and TML
#: never held the distinction in the first place.
_DECLARED_LOSS = {
    "Float": "ThoughtSpot has one approximate numeric type, so Float and Decimal both "
             "become DOUBLE and return as Decimal.",
    "Time": "ThoughtSpot has no time-of-day column type; the value becomes VARCHAR.",
    "DateTimeTz": "ThoughtSpot has no offset-aware column type; the value becomes "
                  "DATE_TIME and returns as DateTime.",
    "Opaque": "Opaque is Ossie's marker for a type outside the portable vocabulary; it "
              "becomes VARCHAR and returns as String.",
}

#: What a column with no declared datatype becomes. The Table TML reference advises
#: preferring INT64 and letting ThoughtSpot report a mismatch, over omitting the block.
_INFERRED = "INT64"


def to_tml(datatype: str | None, *, boolean_spelling: str = "BOOLEAN",
           float_spelling: str = "DOUBLE") -> str:
    """The TML `data_type` for an Ossie datatype. `None` infers rather than raising."""
    if datatype is None:
        return _INFERRED
    if datatype not in _TO_TML:
        raise ValueError(f"{datatype!r} is not an Ossie datatype")
    if datatype == "Boolean":
        return boolean_spelling
    if datatype == "Float":
        return float_spelling
    return _TO_TML[datatype]


def to_ossie(tml_type: str) -> str | None:
    """The Ossie datatype for a TML `data_type`, or `None` when there is no mapping.

    `None` is a legitimate answer, not a failure: `datatype` is optional in Ossie, so
    omitting it is strictly better than inventing one for a type outside the map.
    """
    return _TO_OSSIE.get(tml_type)


def declared_loss(datatype: str) -> str | None:
    """Why this datatype's round trip is lossy, or `None` when it is exact."""
    return _DECLARED_LOSS.get(datatype)
