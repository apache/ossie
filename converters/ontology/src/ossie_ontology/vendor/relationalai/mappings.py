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

from relationalai.semantics import Concept as RAIConcept, Chain
from relationalai.semantics import Relationship as RAIRelationship
from relationalai.semantics.frontend.base import Ref

# Given some entity type E. An E *ref* is a Python variable that can be used in
# a select query or fragment to range over E objects in the graph. For instance,
# given:
#
#    Person = Concept('Person')
#
# the Person object that is declared here is a Person ref, as is Person.ref().
# Because PyRel conflates types with variables, we use refs to mean the type
# or the variable depending on context.


# Given some entity type E. An E *identifier* is an object (Python dictionary) whose:
#
#  - keys are the short names of identifying properties (the ref scheme) of E, and
#  - values take on one of the following two forms depending on whether the "one role"
#    of the property named by the key is played by:
#
#      (1) a value type, in which case the value is a PyRel expression that typically
#          involves a reference to one or more dataset fields (columns), or
#      (2) some entity type E', in which case, the value is an EntityMapping that
#          encapsulates details for looking up references of E'.
#
# Identifiers implement "identifier objects" as they are declared using the *identifier*
# key in an entity or general relationship mapping as proposed in the OSI specification
# for ontologies document.


# Given some entity type E, this abstract class encapsulates the E ref to use when
# referencing and/or deriving the population of E in the graph, and all details needed
# to generate the PyRel that makes the E ref usable for schema mapping. The specific
# details include:
#
#  - The E ref itself (self.ref())
#  - The identifier that will be used to look up E ref objects in PyRel (self.identifier())
#  - The PyRel fragment that applies the identifier to look up E ref objects(self.lookup())
#  - Any constraints that might need to appear in a where(...) clause in order for the
#    use of the E ref to be correct (self.constraints())
#
class Eref:

    # Erefs are parameterized by:
    #
    #  - the entity type (e) being referenced;
    #  - the creation context, in which this Eref will be used. Set this flag
    #    if the context creates new instances of e because it allows us to optimize
    #    the generated PyRel when looking up instances (using e.to_identity rather
    #    than e.filter_by).
    #  - a user supplied E identifier, which may contain nested EntityMaps
    #
    def __init__(self, e: RAIConcept, creation_context=False, **identifier):
        self._entity_type = e
        self._creation_context = creation_context
        (self._identifier_obj, self._constraints) = self._normalize_identifier(**identifier)

    # The identifier object (dictionary) that is passed in when we construct an
    # Eref maps identifying property keys to either PyRel expressions or nested
    # EntityMappings. But identifier objects must map these keys to PyRel expressions
    # exclusively (no nested entity mappings).
    #
    # This method generates:
    #   - a proper identifier object (a copy of identifier in which every key that mapped
    #     to an EntityMapping is replaced with a key that maps to a reference to the entity
    #     type represented by that mapping, and
    #  - an array of constraints imposed by any nested EntityMapping in identifier
    #
    def _normalize_identifier(self, **identifier):
        args = {}
        constraints = []

        for k in identifier:
            if isinstance(identifier[k], EntityMapping):
                args[k] = identifier[k].ref()
                constraints += identifier[k].constraints()
            elif isinstance(identifier[k], ValueMapping):
                args[k] = identifier[k].expr()
                constraints += identifier[k].constraints()
            else:
                args[k] = identifier[k]

        return (args, constraints)

    # Constraints on the variables used to populate the ref. These will appear
    # in the where(...) clause of a rule. Will include any constraints that are
    # associated with nested entity mappings that are handled when we replace
    # those nested entity mappings to create self.identifier().
    #
    def constraints(self):
        return self._constraints

    # The entity type being referenced
    def entity_type(self):
        return self._entity_type

    # The identifier object (dictionary) used when referencing instances of
    # this entity type. The object maps identifying properties to PyRel expressions
    def identifier(self):
        return self._identifier_obj

    # Generates a PyRel fragment to look up/constrain the objects in the graph that
    # self.ref() will range over when the fragment is used in a rule or query.
    #
    # The creation_context lets us optimize how we look up these objects.
    #
    def lookup(self):
        ref = self.ref()
        if self._creation_context:
            # `to_identity` exists on a Concept but not on a Ref, so a creation
            # context requires the concept itself. That holds today: the only
            # subclass that swaps in a plain Ref is EntityMapping, which never
            # creates. Asserting it keeps the coupling visible if that changes.
            assert isinstance(ref, RAIConcept), "a creation context needs a concept ref"
            return ref.to_identity(**(self.identifier()), unsafe=True)
        return ref.filter_by(**(self.identifier()))

    # The reference variable (of type self._entity_type) to use in generated
    # code. This ref is bound by the self.lookup() atom. This method is
    # overridden by some subclasses.
    #
    # A subclass may hand back a plain `Ref` instead of the concept — see
    # EntityMapping below — so the declared type covers both.
    def ref(self) -> RAIConcept | Ref:
        return self._entity_type


# Given some entity type E, this class implements Erefs that range over E objects
# without populating any concepts or relationships as a side effect. These are used
# strictly to aid in referencing E objects in larger mapping contexts as we do when
# we declare nested entity mappings in our OSI proposal (mappings section)

class EntityMapping(Eref):

    def __init__(self, e: RAIConcept, **identifier):
        super().__init__(e, **identifier)
        self._ref = e.ref()

    def constraints(self):
        wc = super().constraints()
        wc.append(self.lookup())
        return wc

    def ref(self):
        return self._ref

    # Additional conditions (PyRel fragments) that constrain this EntityMapping.
    # These will typically refer to Dataset fields
    def where(self, expr):
        self._constraints.append(expr)
        return self


class ValueMapping:
    def __init__(self, expr):
        self._constraints = []
        self._expr = expr

    def constraints(self):
        return self._constraints

    def expr(self):
        return self._expr

    # Additional conditions (PyRel fragments) that constrain this ValueMapping.
    # These will typically refer to Dataset fields
    def where(self, expr):
        self._constraints.append(expr)
        return self


class EBinding(Eref):

    # If provided, stype must be a RAIConcept that provides (at least part of)
    # the identifier
    def __init__(self, model, e: RAIConcept, stype: RAIConcept|None, **identifier):

        if model.declares_preferred_identifier(e):
            # e provides a ref scheme
            super().__init__(e, creation_context=True, **identifier)
            self._model = model

            where_clauses = self._constraints
            if len(where_clauses) == 0:
                self._model.define(e.new(**(self.identifier())))
            else:
                self._model.define(e.new(**(self.identifier()))).where(*where_clauses)

        else:
            # e must inherit its ref scheme
            if e._name in model._super_type_of:
                super().__init__(e, **identifier)
                self._model = model

                if stype is not None:
                    super_ref = EntityMapping(stype, **identifier)
                    # The rule that "asserts" the population of self.entity_type()
                    self._model.where(*(super_ref.constraints())).define(e(super_ref.ref()))
                # else: Assume the supertype binding is declared elsewhere (e.g., when e is a derived subtype)

            else:
                raise ValueError(f'Currently no way to bind entity concept {e._name} because it neither declares nor '
                                 'inherits an identifier')

    #
    # Declares that a property of this entity type binds to one or more role
    # values that ultimately refer to fields of this table. Each value can be:
    #
    #   1) An arbitrary PyRel expression that references one or more
    #      fields to produce some value; or
    #   2) An EntityMapping that declares how to look up an instance of
    #      some entity type to populate for each row of the table; or
    #   3) A ValueMapping that carries a PyRel expression together with
    #      WHERE constraints (e.g. produced by a WHERE-filtered mapping formula).
    #
    # Binary relationships take a single expr. Ternary (and higher-arity)
    # relationships pass the intermediate role value(s) via extra_exprs —
    # e.g. binds_to(returns, item_ref, amount_expr) for Transaction.returns(Item, Amount).
    # Passing no expr at all asserts a unary (zero-role) relationship fact.
    #
    def binds_to(self, property, *exprs):
        ref = self.lookup()
        where_clauses = [ref]
        where_clauses += self.constraints()

        # Unwrap each role value: extract the PyRel expression and accumulate
        # any WHERE constraints the value carries (EntityMapping / ValueMapping).
        def _resolve(e):
            if isinstance(e, EntityMapping):
                where_clauses.extend(e.constraints())
                return e.ref()
            if isinstance(e, ValueMapping):
                where_clauses.extend(e.constraints())
                return e.expr()
            return e

        vals = [_resolve(e) for e in exprs if e is not None]

        atom = EBinding._property_atom(ref, property, *vals)
        self._model.where(*where_clauses).define(atom)

        return self

    #
    # Helper function that declares automates the construction of an EntityMapping
    # by analyzing the property to determine the concept that plays the target role.
    # The identifier contains the arguments to that EntityMapping
    #
    def binds_to_entity(self, property, **identifier):
        emap = EntityMapping(self._model._target_role_player(property), **identifier)
        return self.binds_to(property, emap)

    #
    # Builds the PyRel atom that populates a property of ref with the given
    # role values. Accepts zero or more values:
    #   - zero vals: asserts a unary (boolean) relationship fact
    #   - one val:   binary relationship (the common case)
    #   - N vals:    ternary / higher-arity relationship
    #
    @staticmethod
    def _property_atom(ref, property, *vals):
        attr = ref.__getattr__(property._short_name)
        return attr(*vals) if vals else attr


class JoinPathBinding:

    def __init__(self, model, r: RAIRelationship):
        self._model = model
        self._relationship = r
        self._src_concept = model._source_role_player(r)
        self._trg_concept = model._target_role_player(r)
        self._bind_from = None
        self._bind_to = None

    # Every JoinPathBinding populates the relationship using entity objects that
    # it locates using entity mappings (emap1 and emap2)
    #
    def bind(self, emap1, emap2):
        where_clauses = emap1.constraints() + emap2.constraints()
        self._model.where(*where_clauses).define(self._relationship(emap1.ref(), emap2.ref()))
        return self

    #
    # Helper function that creates automate construction of EntityMappings
    # by analyzing the concepts that play the from and to roles of the
    # relationship being bound
    #

    def bind_from(self, **ctor_args):
        from_emap = EntityMapping(self._src_concept, **ctor_args)
        if self._bind_to is None:
            self._bind_from = from_emap
        else:
            return self.bind(from_emap, self._bind_to)
        return self

    def bind_to(self, **ctor_args):
        to_emap = EntityMapping(self._trg_concept, **ctor_args)
        if self._bind_from is None:
            self._bind_to = to_emap
        else:
            return self.bind(self._bind_from, to_emap)
        return self


class GRBinding:

    # We can initialize a generalized relationship binding with an array
    # of exprs, any one of which can be either:
    #
    #   1) An arbitrary PyRel expression that references one or more
    #      fields to produce some value; or
    #   2) An EntityMapping that declares how to look up an instance of
    #      some entity type to populate for each row of the table
    #
    def __init__(self, model, *exprs):
        self._model = model
        self._key_constraints = []
        self._keys = []

        for expr in exprs:
            if isinstance(expr, EntityMapping):
                self._key_constraints += expr.constraints()
                self._keys.append(expr.ref())
            else:
                self._keys.append(expr)

    def keys(self):
        return self._keys

    def bind(self, handle, expr=None, **identifier):
        r = handle._next if isinstance(handle, Chain) else handle
        keys = self._keys.copy()
        where_clauses = self._key_constraints.copy()

        has_extra = expr is not None or len(identifier) > 0
        self._validate_role_count(r, len(keys) + (1 if has_extra else 0))

        if has_extra:
            extra_key, extra_constraints = self._resolve_extra_key(expr, identifier)
            keys.append(extra_key)
            where_clauses += extra_constraints

        self._model.define(handle(*keys)).where(*where_clauses)
        return self

    @staticmethod
    def _validate_role_count(r, expected):
        if len(r._fields) != expected:
            raise ValueError(
                f"Relationship {r} has {len(r._fields)} roles but used in a "
                f"GRBinding context that expects {expected}")

    def _resolve_extra_key(self, expr, identifier):
        """Resolve the trailing key/constraints pair from either an explicit
        expr (EntityMapping / ValueMapping / raw PyRel expression) or
        **identifier kwargs that synthesize an EntityMapping for the
        relationship's last role.
        """
        if expr is None:
            expr = EntityMapping(self._model._last_role_player(), **identifier)
        if isinstance(expr, EntityMapping):
            return expr.ref(), expr.constraints()
        if isinstance(expr, ValueMapping):
            return expr.expr(), expr.constraints()
        return expr, []
