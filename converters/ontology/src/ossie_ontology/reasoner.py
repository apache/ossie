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

from __future__ import annotations

from ossie_ontology.model import (
    Concept,
    Formula,
    OntologyObserver,
    Relationship,
    RelationshipMultiplicity,
    Role,
)


class OntologyReasoner(OntologyObserver):
    """Answers the questions the converters and the formula layer ask about an
    ontology's shape: which relationships construct an entity, and how concepts
    sit relative to each other in the type hierarchy.

    Only identifiers are indexed, from concepts as they arrive. Every other
    question is answered off the model on demand, so those answers hold
    whatever order concepts were registered in.

    Two preconditions, both met by every converter in this package:

    *Register on a finished ontology.* `register()` replays the concepts already
    in the component, so registering last costs nothing. Registering early does
    cost something: a concept's `identify_by` is resolved in a later pass than
    the `add_concept` that announces it -- relationships have to exist first --
    so a reasoner attached mid-build records no identifiers and
    `is_identifier_relationship` then answers False for every relationship.
    That query is the one thing here that cannot be answered on demand: it asks
    whether *any* concept identifies itself by a given relationship, and a
    relationship named in a subtype's `identify_by` may be owned by a supertype
    that identifies by nothing -- a reverse lookup no forward walk can resolve.

    *`extends` should be acyclic.* `spec_to_ossie` rejects cycles and
    `palantir_to_ossie` breaks them, so no converter here can hand one over.
    A cycle reaching this class anyway -- from a model assembled by hand -- is
    answered rather than fatal: every walk over `extends` stops at an edge that
    leads nowhere new. The answers under a cycle are only as meaningful as the
    hierarchy that produced them.
    """

    def __init__(self):
        # The identifying side of each relationship some concept names in its
        # `identify_by` -- the reasoner's only state.
        self._constructor_roles: set[Role] = set()

    # --- observer protocol ------------------------------------------------

    def on_concept_added(self, concept: Concept) -> None:
        self.new_concept(concept)

    def on_require_added(self, require: Formula) -> None:
        # Not indexed yet. Which constraint a require states — a mandatory
        # role, a uniqueness, a cardinality — only comes out of walking the AST
        # that `ossie_ontology.expr` attaches.
        pass

    # --- ingestion --------------------------------------------------------

    def new_concept(self, concept: Concept) -> None:
        if not concept.is_primitive:
            self._register_declared_identifier(concept)

    def _register_declared_identifier(self, concept: Concept) -> None:
        """Record the roles a concept's `identify_by` constructs it from."""
        for identifier in concept.identify_by.values():
            self._constructor_roles.add(identifier.last_role)

    # --- queries ----------------------------------------------------------

    @staticmethod
    def ref_scheme(concept: Concept, shallow: bool = False) -> tuple[Relationship, ...] | None:
        """The relationships that identify `concept`, nearest inherited scheme
        first, or None when neither it nor its supertypes declare one."""
        return OntologyReasoner._ref_scheme(concept, shallow, set())

    @staticmethod
    def _ref_scheme(concept: Concept, shallow: bool,
                    path: set[Concept]) -> tuple[Relationship, ...] | None:
        # `path` holds the concepts on the branch being walked right now, so a
        # branch that re-enters a concept it is already inside stops instead of
        # looping forever, and the concept is dropped again on the way out.
        # A global visited set would answer the same on every acyclic shape --
        # a branch reaching a scheme-bearing ancestor always returns one, so
        # nothing it marked could have mattered to a later branch -- but that
        # is an argument about the `break` below, and a path set does not need
        # it to be true.
        if concept in path:
            return None
        path.add(concept)
        try:
            ref_schema: list[Relationship] = []
            if not shallow:
                for parent in concept.extends:
                    parent_schema = OntologyReasoner._ref_scheme(parent, False, path)
                    if parent_schema:
                        ref_schema.extend(parent_schema)
                        break
            if concept.identify_by:
                ref_schema.extend(concept.identify_by.values())
            return tuple(ref_schema) if ref_schema else None
        finally:
            path.discard(concept)

    def is_constructing_role(self, role: Role) -> bool:
        return role in self._constructor_roles

    def is_identifier_relationship(self, relationship: Relationship) -> bool:
        if not relationship.binary:
            return False
        return any(self.is_constructing_role(r) for r in relationship.roles)

    def in_subtype_closure(self, sub: Concept, sup: Concept) -> bool:
        """Is `sub` a strict subtype of `sup`, transitively?

        Walked on demand rather than indexed as concepts arrive: an index built
        incrementally only sees a parent's own supertypes if that parent was
        registered first, so a child-before-parent ordering silently lost the
        transitive edges. `extends` chains are short and this is asked a handful
        of times per conversion, so the walk costs nothing worth indexing for.
        """
        # Iterative, with a visited set rather than the path set `_ref_scheme`
        # uses: this answers reachability, and reaching a concept a second time
        # cannot change whether `sup` lies beyond it. That also means no
        # recursion depth to run out of on a long `extends` chain.
        seen: set[Concept] = set()
        stack = list(sub.extends)
        while stack:
            parent = stack.pop()
            if parent is sup:
                return True
            if parent in seen:
                continue
            seen.add(parent)
            stack.extend(parent.extends)
        return False

    def is_property(self, rel: Relationship) -> bool:
        if rel.arity < 2:
            return False
        return rel.multiplicity in (RelationshipMultiplicity.MANY_TO_ONE, RelationshipMultiplicity.ONE_TO_ONE)
