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

from typing import Optional

from ossie_ontology.expr.formula.model import (
    Formula,
    FormulaExpressionInfo,
    ConceptRefHandle,
    RelationshipRefHandle,
    DatasetRefHandle,
    DatasetFieldHandle,
    VarHandle,
    DotJoinHandle,
    FactRef
)
from ossie_ontology.expr.formula.visitor.unbound import UnboundRefFinder
from ossie_ontology.model import (
    OntologyComponent,
    Concept,
    Relationship
)
from ossie_ontology.reasoner import OntologyReasoner

# What a variable in a formula body can be bound to. It starts as the concept
# the collector inferred (or None where inference found nothing), and a dot join
# can later rebind it to the relationship it resolved to.
VarType = Concept | Relationship | None


class FormulaValidator:
    """FormulaValidator — validates a parsed formula against a single Ontology
    (and an optional SemanticModel for mapping context)."""

    @staticmethod
    def validate_rule(ontology: OntologyComponent, reasoner: OntologyReasoner, formula: Formula | None):
        if formula is None or not formula._expr:
            return
        # An ontology-level `requires` has no parent concept/relationship to bind
        # variables against, so every variable and concept reference must be
        # bound within the formula itself — by an aggregate or by EXISTS.
        if formula.parent is None:
            FormulaValidator._validate_no_unbound_refs(formula)
        head_vars = formula.head_vars()
        body_vars = formula.expr_info().var_handles

        head_var_map = {k: v for k, v in head_vars.items() if isinstance(k, VarHandle)}
        body_var_handles = FormulaValidator._build_handle_maps(body_vars, formula.raw_expr)

        FormulaValidator._validate_dot_join(
            ontology, reasoner, formula.expr_info().dot_join_handles, body_var_handles, formula.raw_expr
        )
        if isinstance(formula.parent, Relationship) and not reasoner.is_identifier_relationship(formula.parent):
            for vh in head_var_map.keys():
                if vh not in body_var_handles and not formula.is_formula_target(vh):
                    raise ValueError(
                        f"Var '{vh.name()}' in the head not found in the body of the formula '{formula.raw_expr}'"
                    )
        FormulaValidator._validate_unresolved_var_handles(body_var_handles, formula.raw_expr)
        FormulaValidator._validate_fact_refs(
            reasoner,
            formula.expr_info().fact_refs,
            formula.expr_info().dot_join_handles,
            body_var_handles,
            formula.raw_expr,
        )

    @staticmethod
    def _validate_no_unbound_refs(formula: Formula) -> None:
        names: list[str] = []
        for handle in UnboundRefFinder(formula).unbound_refs():
            name = handle.name() if isinstance(handle, VarHandle) else handle._concept.name
            if name not in names:
                names.append(name)
        if not names:
            return
        rendered = ", ".join(f"'{n}'" for n in names)
        raise ValueError(
            f"Unbound reference(s) {rendered} in the ontology-level constraint "
            f"'{formula.raw_expr}'. An ontology-level `requires` has no parent scope to "
            f"bind variables against, so every reference must be bound within the formula — "
            f"by an aggregate (e.g. COUNT[...]) or by wrapping it in EXISTS(...), "
            f"e.g. 'EXISTS({formula.raw_expr})'."
        )

    @staticmethod
    def _build_handle_maps(
        var_handles: list[tuple[VarHandle, Concept | None]], formula: str
    ) -> dict[VarHandle, VarType]:
        var_handles_map: dict[VarHandle, VarType] = {}
        for h, c in var_handles:
            if h in var_handles_map and var_handles_map[h] != c:
                seen = var_handles_map[h]
                raise ValueError(
                    f"An ambiguous type for the var '{h.name()}' in '{formula}': "
                    f"'{c.name if c else None}' and '{seen.name if seen else None}'"
                )
            var_handles_map[h] = c
        return var_handles_map

    @staticmethod
    def _validate_dot_join(
        ontology: OntologyComponent,
        reasoner: OntologyReasoner,
        dot_join_handles: list[DotJoinHandle],
        var_handles: dict[VarHandle, VarType],
        formula: str,
    ):
        for h in dot_join_handles:
            left, right = h._handle1, h._handle2
            if isinstance(right, VarHandle):
                rel: Relationship | None = None
                if isinstance(left, VarHandle) and left in var_handles:
                    owner = var_handles[left]
                    # A var already resolved to a relationship (by an earlier dot
                    # join) owns no further relationships to look up.
                    if isinstance(owner, Concept):
                        rel = ontology.lookup_concept_relationship(owner, right._var)
                if rel:
                    var_handles[right] = rel
                else:
                    if isinstance(left, DatasetRefHandle):
                        raise ValueError(
                            f"Undefined column '{right}' for dataset '{left}' in the formula '{formula}'"
                        )
                    raise ValueError(f"Undefined relationship '{h}' in the formula '{formula}'")

            if isinstance(right, RelationshipRefHandle):
                first_role_player = right._relationship.roles[0].player
                if isinstance(left, ConceptRefHandle):
                    concept = left._concept
                elif isinstance(left, DotJoinHandle):
                    last_handle = left.last_handle()
                    if isinstance(last_handle, RelationshipRefHandle):
                        concept = last_handle._relationship.last_role.player
                    else:
                        concept = (var_handles.get(last_handle)
                                   if isinstance(last_handle, VarHandle) else None)
                elif isinstance(left, FactRef):
                    handle = left._handle
                    if isinstance(handle, RelationshipRefHandle):
                        concept = handle._relationship.last_role.player
                    elif isinstance(handle, DotJoinHandle) and isinstance(handle._handle2, RelationshipRefHandle):
                        concept = handle._handle2._relationship.last_role.player
                    else:
                        concept = None
                else:
                    concept = var_handles.get(left) if isinstance(left, VarHandle) else None
                if concept != first_role_player and not (
                    isinstance(concept, Concept)
                    and reasoner.in_subtype_closure(concept, first_role_player)
                ):
                    raise ValueError(
                        f"Type mismatch for '{h}' in the formula '{formula}'. "
                        f"Expected type in the relationship '{first_role_player}' got '{concept}'"
                    )

    @staticmethod
    def _validate_unresolved_var_handles(var_handles: dict[VarHandle, VarType], formula: str):
        for k, v in var_handles.items():
            if v is None:
                raise ValueError(
                    f"Unresolved reference '{k}' in the formula '{formula}'. "
                    f"If '{k}' is a dataset field, use the format 'DATASET_NAME.{k}'."
                )

    @staticmethod
    def _validate_fact_refs(
        reasoner: OntologyReasoner,
        fact_refs: list[FactRef],
        dot_join_handles: list[DotJoinHandle],
        var_handles: dict[VarHandle, VarType],
        formula: str,
    ):
        partial_applies = {dj._handle1 for dj in dot_join_handles if isinstance(dj._handle1, FactRef)}
        for f in fact_refs:
            # `Relationship` is reachable: a dot join can rebind a body var to
            # the relationship it resolved to. `None` is not — the pass above
            # rejects unresolved vars before this one runs.
            args_type: list[Concept | Relationship] = []
            if isinstance(f._handle, RelationshipRefHandle):
                rel = f._handle._relationship
            elif isinstance(f._handle, DotJoinHandle) and isinstance(f._handle._handle2, RelationshipRefHandle):
                rel = f._handle._handle2._relationship
                if len(f._args) != rel.arity:
                    h1 = f._handle._handle1
                    if isinstance(h1, ConceptRefHandle):
                        args_type.append(h1._concept)
                    elif isinstance(h1, VarHandle):
                        bound = var_handles[h1]
                        # None is unreachable here (see args_type above), but a
                        # dropped element would shift the arity check below, so
                        # only a real binding is contributed.
                        if bound is not None:
                            args_type.append(bound)
                    elif isinstance(h1, FactRef):
                        inner = h1._handle
                        if isinstance(inner, DotJoinHandle) and isinstance(inner._handle2, RelationshipRefHandle):
                            args_type.append(inner._handle2._relationship.last_role.player)
                        elif isinstance(inner, RelationshipRefHandle):
                            args_type.append(inner._relationship.last_role.player)
            else:
                continue
            for arg in f._args:
                t: Concept | Relationship | None = None
                if isinstance(arg, ConceptRefHandle):
                    t = arg._concept
                elif isinstance(arg, VarHandle):
                    t = var_handles[arg]
                elif isinstance(arg, DotJoinHandle):
                    if isinstance(arg._handle1, RelationshipRefHandle):
                        t = arg._handle1._relationship.first_role.player
                    elif isinstance(arg._handle1, DatasetRefHandle) and isinstance(arg._handle2, DatasetFieldHandle):
                        field = arg._handle2._field
                        if field.type is None:
                            raise ValueError(
                                f"Dataset field '{arg._handle1.name()}.{field.name}' has no type "
                                f"and cannot be used as a typed argument in '{formula}'"
                            )
                        t = field.type
                    elif isinstance(arg._handle2, RelationshipRefHandle):
                        t = arg._handle2._relationship.last_role.player
                        args_type.append(t)
                        if f in partial_applies:
                            args_type.append(rel.last_role.player)
                        continue
                elif isinstance(arg, FactRef):
                    if isinstance(arg._handle, ConceptRefHandle):
                        t = arg._handle._concept
                if t is None:
                    raise ValueError(
                        f"Unsupported argument '{arg}' for the fact reference in the formula '{formula}'"
                    )
                args_type.append(t)

            # A fact on the left of a dot join may leave its last role open and
            # continue the traversal from it, so the dot join supplies that
            # argument: 'Person.employment(Company).level' fills (Person, Company)
            # and reads `level` off the Position playing the trailing role. The
            # parser resolves the step after the dot the same way.
            if f in partial_applies and len(args_type) == rel.arity - 1:
                args_type.append(rel.last_role.player)

            rel_signature = rel.signature
            mismatch = len(rel_signature) != len(args_type) or any(
                expected != actual
                and not (isinstance(actual, Concept)
                         and reasoner.in_subtype_closure(actual, expected))
                for expected, actual in zip(rel_signature, args_type)
            )
            if mismatch:
                raise ValueError(
                    f"Relationship '{rel}' signature mismatch in the formula '{formula}': "
                    f"expected ({', '.join(c.name for c in rel_signature)}) "
                    f"got ({', '.join(c.name for c in args_type)})"
                )


class MappingFormulaValidator:
    """Validates a parsed mapping expression.

    Mapping expressions may only contain: dataset field references (qualified or
    single-field), literals, and arithmetic/comparison operators. All ontology
    constructs and unsupported expression types are rejected here so they fail
    at spec-parse time rather than later during PyRel conversion."""

    @staticmethod
    def validate(expr_info: FormulaExpressionInfo, formula_str: str) -> None:
        MappingFormulaValidator._validate_no_var_handles(expr_info, formula_str)
        MappingFormulaValidator._validate_no_relationship_refs(expr_info, formula_str)
        MappingFormulaValidator._validate_no_concept_refs(expr_info, formula_str)
        MappingFormulaValidator._validate_no_dataset_refs(expr_info, formula_str)
        MappingFormulaValidator._validate_no_fact_refs(expr_info, formula_str)

    @staticmethod
    def _validate_no_var_handles(expr_info: FormulaExpressionInfo, formula_str: str) -> None:
        for vh, _ in expr_info.var_handles:
            raise ValueError(
                f"Unresolved variable '{vh.name()}' in mapping expression '{formula_str}'. "
                f"Use the qualified form 'DATASET_NAME.{vh.name()}' to reference a dataset field."
            )

    @staticmethod
    def _validate_no_relationship_refs(expr_info: FormulaExpressionInfo, formula_str: str) -> None:
        for rrh in expr_info.relationship_ref_handles:
            raise ValueError(
                f"Relationship references are not allowed in mapping expressions: "
                f"'{rrh._relationship.name}' in '{formula_str}'."
            )

    @staticmethod
    def _validate_no_concept_refs(expr_info: FormulaExpressionInfo, formula_str: str) -> None:
        for crh, _ in expr_info.concept_ref_handles:
            raise ValueError(
                f"Concept references are not allowed in mapping expressions: "
                f"'{crh._concept.name}' in '{formula_str}'."
            )

    @staticmethod
    def _validate_no_dataset_refs(expr_info: FormulaExpressionInfo, formula_str: str) -> None:
        # A DatasetRefHandle that is the left side of a DotJoinHandle is a valid
        # "Dataset.field" reference — only reject truly bare, unqualified dataset refs.
        qualified = {id(dj._handle1) for dj in expr_info.dot_join_handles}
        for drh in expr_info.dataset_ref_handles:
            if id(drh) not in qualified:
                raise ValueError(
                    f"Bare dataset references are not allowed in mapping expressions: "
                    f"'{drh.name()}' in '{formula_str}'. Use 'DATASET_NAME.field_name' instead."
                )

    @staticmethod
    def _validate_no_fact_refs(expr_info: FormulaExpressionInfo, formula_str: str) -> None:
        if expr_info.fact_refs:
            raise ValueError(
                f"Function/apply expressions are not allowed in mapping expressions: '{formula_str}'."
            )