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

"""
Utilities for working with PyRel FieldRef (role) objects.

PyRel roles are accessed via rel[field] syntax and return a FieldRef.
FieldRef._root may be a Relationship, Chain, or Reading, so direct
attribute access on _root is not safe without going through these helpers.
"""
from relationalai.semantics import Concept, Relationship, Chain, Reading
from relationalai.semantics.frontend.base import FieldRef, Field


def role_part_of(role: FieldRef) -> Relationship:
    """Return the Relationship that this role belongs to."""
    cand = role._root
    if isinstance(cand, Relationship):
        return cand
    elif isinstance(cand, Chain):
        return cand._next
    else:
        raise ValueError(f"Unexpected root type: {type(cand)}")


def role_player(role: Field|FieldRef) -> Concept:
    """Return the Concept that plays this role."""
    return role._to_concept() if isinstance(role, FieldRef) else role.type


def role_guid(role: FieldRef) -> int:
    """
    Return a stable, hashable identifier for a role.
    Safe to use as a dict key across multiple FieldRef instances
    that refer to the same role (unlike FieldRef._id, which is
    per-instance, not per-role).
    """
    rel = role_part_of(role)
    if isinstance(rel, Reading):
        rel = rel._relationship
    field = role._resolved
    role_ix = rel._fields.index(field)
    return hash((role_ix, rel_guid(rel)))


def rel_guid(rel: Relationship | Chain) -> int:
    """
    Return a stable, hashable identifier for a relationship, suitable for
    structural equality and dict/set keys. Derived from the reading used by
    role_guid so that role_guid and rel_guid stay in agreement.
    """
    if isinstance(rel, Chain):
        rel = rel._next
    if isinstance(rel, Reading):
        rel = rel._relationship
    return hash(rel._readings[0]._reading)


def role_siblings(role: FieldRef) -> list[FieldRef]:
    """Return all sibling roles (all other roles of the same relationship)."""
    rel = role_part_of(role)
    return [rel[idx] for idx, _ in enumerate(rel._fields) if idx != role._resolved_ix]


def role_sibling(role: FieldRef) -> FieldRef:
    """Return the single sibling role. Asserts the relationship is binary."""
    siblings = role_siblings(role)
    assert len(siblings) == 1, f"Expected 1 sibling, got {len(siblings)}"
    return siblings[0]


def role_eq(role1: FieldRef, role2: FieldRef) -> bool:
    """Return True if two FieldRefs refer to the same role."""
    return role_guid(role1) == role_guid(role2)


def role_pprint(role: FieldRef) -> str:
    """Return a human-readable string for a role."""
    part_of = role_part_of(role)
    if isinstance(part_of, Reading):
        reading = part_of._reading
    elif isinstance(part_of, Relationship):
        reading = part_of._readings[0]._reading
    else:
        raise ValueError(f"Unexpected root type: {type(part_of)}")
    return f"{reading}[{role._resolved_ix}]"
