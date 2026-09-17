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

import warnings

from relationalai.semantics import Model as RAIModel, Concept as RAIConcept, Chain
from relationalai.semantics import select, Relationship as RAIRelationship, Table as RAITable
from relationalai.semantics.frontend.base import Ref, CoreConcepts, NumberConcept
from relationalai.semantics.frontend.core import is_primitive_concept, Float

from ossie_ontology.vendor.relationalai.mappings import EBinding, GRBinding, JoinPathBinding
from ossie_ontology.vendor.relationalai.roles import role_player


# Given c, which is either a PyRel Concept or a Ref, return either
# c or the Concept that c references
def concept_from_ref(c):
    return c._concept if isinstance(c, Ref) else c

class OntologyModel:

    def __init__(self, m):
        self._delegate = RAIModel(m)
        self._concept = {}
        self._concept_module = {}
        self._properties = {}
        self._super_type_of = {}
        self._edges = []
        self._table_index = {}

    def base_model(self):
        return self._delegate

    def Concept(self, name, extends = None):
        if extends is None:
            extends = []
        c = self._delegate.Concept(name, extends=extends)
        key = self._concept_key(c)
        self._concept[key] = c
        self._properties[key] = []
        if not is_primitive_concept(c):
            if len(extends) > 0:
                if len(extends) > 1:
                    warnings.warn(f"Assuming first supertype of {name} provides inherited identifier")
                self._super_type_of[name] = extends[0]
        return c

    # If provided, stype must be a RAIConcept that provides (at least part of)
    # the identifier
    def EntityBinding(self, e: RAIConcept, stype: RAIConcept | None = None, **identifier):
        return EBinding(self, e, stype, **identifier)

    def Property(self, madlib, short_name: str | None = None):
        # pyrel annotates `short_name` as `str` and defaults it to "", but it
        # also uses the value as a key in `relationship_index`, so None and ""
        # are not interchangeable. Pass what the caller gave us unchanged.
        p = self._delegate.Property(madlib, short_name=short_name)  # type: ignore[arg-type]

        if self._is_binary(p):
            src_key = self._role_key(self._source_role(p))
            if src_key in self._concept:
                self._properties[src_key].append(p)

            if self._is_edge(p):
                self._log_edge(p)
        return p

    def Relationship(self, madlib, short_name: str | None = None):
        # pyrel annotates `short_name` as `str` and defaults it to "", but it
        # also uses the value as a key in `relationship_index`, so None and ""
        # are not interchangeable. Pass what the caller gave us unchanged.
        r = self._delegate.Relationship(madlib, short_name=short_name)  # type: ignore[arg-type]
        if self._is_edge(r):
            self._log_edge(r)
        return r

    def RelationshipBinding(self, *emaps):
        return GRBinding(self, *emaps)

    def JoinPath(self, r: RAIRelationship):
        return JoinPathBinding(self, r)

    def query(self, c: RAIConcept):
        """Return a PyRel select of all properties of concept *c*."""
        key = self._concept_key(c)
        if key is None:
            raise ValueError("Fail")

        params = []
        schemes = self.ref_schemes(c)
        if schemes:
            for rel in schemes:
                params.append(Chain(c, rel).alias(rel._short_name))

        if key in self._properties:
            props = self._properties[key]
            for prop in props:
                params.append(self._readable_relationship_expr(c, prop))

        return select(*params)

    def declares_preferred_identifier(self, c: RAIConcept):
        return self.ref_schemes(c, shallow=True) is not None

    def define(self, *args):
        return self._delegate.define(*args)

    def not_(self, *args):
        return self._delegate.not_(*args)

    def where(self, *args):
        return self._delegate.where(*args)

    def ref_schemes(self, concept: RAIConcept, shallow: bool = False) -> tuple[RAIRelationship, ...] | None:
        seen_ids: set[int] = set()
        ref_schema: list[RAIRelationship] = []

        def add_ref_scheme_component(cand: RAIRelationship) -> None:
            rel_id = cand._id
            if rel_id not in seen_ids:
                seen_ids.add(rel_id)
                ref_schema.append(cand)

        if not shallow:
            for parent in concept._extends:
                parent_schema = self.ref_schemes(parent)
                if parent_schema:
                    for rel in parent_schema:
                        add_ref_scheme_component(rel)
        for rel in concept._identify_by:
            add_ref_scheme_component(rel)
        return tuple(ref_schema) if ref_schema else None

    def readable_dot_join(self, h, rel):
        expr = Chain(h, rel)
        ref_schemes = self.ref_schemes(role_player(self._target_role(rel)))
        if not ref_schemes:
            return expr

        rel_name = rel._short_name
        warnings.warn(
            "Attempting to dot join on a composite entity type, joining on the first component of the ref scheme")
        return Chain(expr, ref_schemes[0]).alias(rel_name)

    # Returns a pair (Comp, G) where Comp is a new Concept named *name* that represents
    # weakly connected components of nodes of type C. The caller is expected to then
    # populate the edge relation of G. Comp declares several properties, including:
    #   - Comp.equates(C), which connects Comp to each C node it groups, and
    #   - Comp.size, which is the size of Comp.equates for a given Comp.
    def find_path_schemas(self, start: RAIConcept, end, depth=4):
        initial_term_path = {}
        term_key = self._concept_key(concept_from_ref(end))

        key = self._concept_key(start)
        if key not in self._concept_module:
            raise ValueError(f"Cannot find paths starting at concept {start}. Try using an entity type")

        for r in self._concept_module[key]:
            path_end = self._role_key(self._target_role(r))

            # Prune paths that do not terminate in the ending concept
            if depth <= 1 and path_end != term_key:
                continue

            if path_end not in initial_term_path:
                initial_term_path[path_end] = []
            initial_term_path[path_end].append([r])

        term_path = self._recursive_find_path_schemas(initial_term_path, term_key, depth - 1)

        paths = []
        for v in term_path.values():
            for p in v:
                paths.append(p)
        return paths

    def add_table(self, name: str, table):
        # Deliberately untyped: whatever a table provider returns. The default
        # yields a pyrel `Table`, but an extension may supply anything with the
        # same column-accessor surface, such as a CSV-backed stand-in.
        self._table_index[name] = table

    def lookup_concept(self, name: str) -> RAIConcept | None:
        if name in CoreConcepts:
            return CoreConcepts[name]
        else:
            return self.base_model().concept_index.get(name)

    def lookup_table(self, name: str) -> RAITable | None:
        return self._table_index.get(name)

    def lookup_relationship(self, name: str) -> RAIRelationship | None:
        return self.base_model().relationship_index.get(name)

    def get_topmost_parent(self, concept: RAIConcept) -> RAIConcept:
        if not concept._extends or isinstance(concept, NumberConcept) or concept is Float:
            return concept
        return self.get_topmost_parent(concept._extends[0])

    def _recursive_find_path_schemas(self, term_map, term_key, depth):
        if depth == 0:
            return term_map
        new_term_map = {}
        for c, paths in term_map.items():
            for path in paths:
                for r in self._concept_module[c]:
                    self._extend_path_schema(
                        new_term_map, path, r, term_key, depth)
        return self._recursive_find_path_schemas(new_term_map, term_key, depth - 1)

    def _extend_path_schema(self, new_term_map, path, r, term_key, depth):
        """Append ``path + [r]`` to *new_term_map* under the key of r's target
        role, unless the target wouldn't reach *term_key* on the next pass.
        """
        path_end = self._role_key(self._target_role(r))
        # Prune paths that do not terminate in the ending concept
        if depth <= 1 and path_end != term_key:
            return
        new_term_map.setdefault(path_end, []).append(path + [r])

    # Given a Relationship r whose facts should represent edges in the graph,
    # log r as an edge so that we can later do path analysis using it.
    def _log_edge(self, r):
        self._edges.append(r)
        #
        src_key = self._role_key(self._source_role(r))
        if src_key not in self._concept_module:
            self._concept_module[src_key] = []
        self._concept_module[src_key].append(r)

    # Given a binary relationship rel, returns an expression that allows rel to be
    # queried so that it returns a readable answer when used in a select query.
    def _readable_relationship_expr(self, c, rel):
        schemes = self.ref_schemes(role_player(self._target_role(rel)))
        rel_name = rel._short_name

        if not schemes:
            return Chain(c, rel).alias(rel_name)

        first_scheme = schemes[0]
        if len(schemes) > 1:
            warnings.warn(
                "Attempting to join on a composite entity type, joining on the first component of the ref scheme")
        return Chain(Chain(c, rel), first_scheme).alias(rel_name)

    def _concept_key(self, c):
        if isinstance(c, RAIConcept):
            return c._name.lower()
        else:
            return c.lower()

    def _is_value_type(self, c: RAIConcept):
        return is_primitive_concept(c)

    def _is_entity_type(self, c: RAIConcept):
        return not self._is_value_type(c)

    def _is_entity_role(self, role):
        key = self._role_key(role)
        if key in self._concept and self._is_entity_type(self._concept[key]):
            return True
        return False

    def _is_binary(self, r):
        return len(r._fields) == 2

    def _is_edge(self, r):
        if (self._is_binary(r) and self._is_entity_role(self._source_role(r)) and
                self._is_entity_role(self._target_role(r))):
            return True
        return False

    def _last_role(self, r):
        return r._fields[len(r._fields) - 1]

    def _last_role_player(self, r):
        return self._role_player(self._last_role(r))

    def _role_key(self, role):
        return self._concept_key(role.type._name)

    def _role_player(self, r):
        return self._concept[self._role_key(r)]

    def _source_role(self, r):
        return r._fields[0]

    def _source_role_player(self, r):
        return self._role_player(self._source_role(r))

    def _target_role(self, r):
        return r._fields[1]

    def _target_role_player(self, r):
        return self._role_player(self._target_role(r))
