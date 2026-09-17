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

"""Formula parser. The grammar, precedence table, and AST."""

from __future__ import annotations

import ply.yacc as yacc

from ossie_ontology.model import OntologyComponent, SemanticModel, Concept, Dataset
from ossie_ontology.expr.model import Expression, BinOp
from ossie_ontology.expr.formula.model import (
    Atom,
    VarHandle,
    LiteralHandle,
    Negation,
    Conjunction,
    Disjunction,
    Exists,
    WhereExpr,
    AggregationMethod,
    AggExpr,
    DotJoinHandle,
    FactRef,
    ConceptRefHandle,
    RelationshipRefHandle,
    DatasetRefHandle,
    DatasetFieldHandle,
)
from ossie_ontology.expr.formula.lexer import FormulaLexer
from ossie_ontology.expr.common import AggMethod


class FormulaParser:
    """Formula parser. Constructed against a single `Ontology` (where
    concepts and relationships live). For mapping formulas, callers pass an
    additional `SemanticModel` so dataset and field references resolve."""

    _HEAD_VARS_PARAM = "head_vars"
    _MAPPING_DATASET_PARAM = "mapping_dataset"

    def __init__(self, ontology: OntologyComponent, semantic_model: SemanticModel | None = None):
        # `write_tables=False, debug=False`: ply's defaults write `parsetab.py`
        # (~12 KB) and `parser.out` (~85 KB) next to this module, which for an
        # installed package means into site-packages on first parse — it fails
        # on a read-only install and dirties container layers. Rebuilding the
        # LALR tables costs ~4 ms, so there is nothing to cache.
        self.parser = yacc.yacc(module=self, debug=False, write_tables=False)
        self.lexer = FormulaLexer()
        self._ontology = ontology
        self._semantic_model = semantic_model

    def with_semantic_model(self, semantic_model: SemanticModel) -> FormulaParser:
        """Returns a parser bound to the same ontology and the given logical
        model. Useful when iterating over multiple OntologyMappings."""
        return FormulaParser(self._ontology, semantic_model)

    tokens = FormulaLexer.tokens

    precedence = (
        ("left", "OR"),
        ("left", "AND"),
        ("right", "NOT"),
        ("nonassoc", "EQUALS", "NOTEQUALS", "GREATER", "GREATEREQ", "LESS", "LESSEQ"),
        ("left", "LPAREN"),
        ("left", "DOT"),
        ("left", "PLUS", "MINUS"),
        ("left", "TIMES", "DIVIDE"),
    )

    # ----------------- grammar ------------------------------------------

    def p_goal(self, p):
        "goal : formula"
        p[0] = p[1]

    def p_formula_expr(self, p):
        "formula : expr"
        p[0] = p[1]

    def p_formula_comp_expr(self, p):
        "formula : expr comp_op expr"
        op = BinOp.from_value(p[2])
        p[0] = Expression(op, p[1], p[3])

    def p_formula_and(self, p):
        "formula : formula AND formula"
        p[0] = Conjunction(p[1], p[3])

    def p_formula_or(self, p):
        "formula : formula OR formula"
        p[0] = Disjunction(p[1], p[3])

    def p_formula_exists(self, p):
        "formula : EXISTS LPAREN formula RPAREN"
        p[0] = Exists(p[3])

    def p_formula_not(self, p):
        "formula : NOT formula"
        p[0] = Negation(p[2])

    def p_expr_node_expr(self, p):
        "expr : node_expr"
        p[0] = p[1]

    def p_expr_minus_expr(self, p):
        "expr : MINUS expr %prec NOT"
        p[0] = Expression(BinOp.MINUS, LiteralHandle(0), p[2])

    def p_expr_agg(self, p):
        "expr : agg_expr"
        p[0] = p[1]

    def p_expr_arith(self, p):
        """
        expr : expr PLUS expr
             | expr MINUS expr
             | expr TIMES expr
             | expr DIVIDE expr
        """
        op = BinOp.from_value(p[2])
        p[0] = Expression(op, p[1], p[3])

    def p_expr_where(self, p):
        "expr : expr WHERE LPAREN formula RPAREN"
        p[0] = WhereExpr(p[1], p[4])

    def p_expr_parenthesized(self, p):
        "expr : LPAREN expr RPAREN"
        p[0] = p[2]

    def p_node_expr_literal(self, p):
        "node_expr : literal"
        p[0] = p[1]

    def p_node_expr_variable(self, p):
        "node_expr : ID"
        mapping_dataset = p.parser.params.get(self._MAPPING_DATASET_PARAM)
        if mapping_dataset is not None:
            field = mapping_dataset.field(p[1])
            if field is not None:
                p[0] = DatasetFieldHandle(mapping_dataset, field)
                return
            handle = self._to_identifier_ref(p[1])
            if isinstance(handle, VarHandle):
                raise ValueError(
                    f"'{p[1]}' is not a field in dataset '{mapping_dataset.name()}' "
                    f"and is not a known concept, relationship, or dataset"
                )
            p[0] = handle
        else:
            head_vars = p.parser.params.get(self._HEAD_VARS_PARAM)
            if head_vars:
                vh = VarHandle(p[1])
                if vh in head_vars:
                    p[0] = vh
                    return
            p[0] = self._to_identifier_ref(p[1])

    def p_node_expr_dot(self, p):
        "node_expr : node_expr DOT ID"
        left_handle = p[1]
        step_name = p[3]

        # `Dataset.field` always means the dataset's field, even when the
        # identifier on the right also matches a concept or relationship name
        # in the ontology. Resolve the right side as a field of the dataset
        # directly — never through _to_identifier_ref, which would shadow it
        # with the ontology hit.
        if isinstance(left_handle, DatasetRefHandle):
            dataset = self._lookup_dataset(left_handle.name())
            if dataset is None:
                raise ValueError(f"Unknown dataset '{left_handle.name()}'")
            field = dataset.field(step_name)
            if field is None:
                raise ValueError(
                    f"Undefined column '{step_name}' for dataset '{left_handle.name()}'"
                )
            p[0] = DotJoinHandle(left_handle, DatasetFieldHandle(dataset, field))
            return

        container: Concept | None = None
        if isinstance(left_handle, ConceptRefHandle):
            container = left_handle._concept
        elif isinstance(left_handle, VarHandle):
            head_vars = p.parser.params.get(self._HEAD_VARS_PARAM)
            if head_vars and left_handle in head_vars:
                container = head_vars[left_handle]
        elif isinstance(left_handle, DotJoinHandle):
            handle = left_handle._handle2
            if isinstance(handle, RelationshipRefHandle):
                container = handle._relationship.last_role.player
        elif isinstance(left_handle, FactRef):
            handle = left_handle._handle
            if isinstance(handle, RelationshipRefHandle):
                container = handle._relationship.last_role.player
            elif isinstance(handle, DotJoinHandle) and isinstance(handle._handle2, RelationshipRefHandle):
                container = handle._handle2._relationship.last_role.player
            elif isinstance(handle, ConceptRefHandle):
                container = handle._concept

        right_handle = self._to_identifier_ref(step_name, container)
        p[0] = DotJoinHandle(left_handle, right_handle)

    def p_node_expr_apply(self, p):
        "node_expr : node_expr LPAREN expr_seq RPAREN %prec LPAREN"
        handle = self._to_identifier_ref(p[1])
        p[0] = FactRef(handle, *p[3])

    def p_expr_seq(self, p):
        "expr_seq : expr"
        p[0] = [p[1]]

    def p_expr_seq_many(self, p):
        "expr_seq : expr_seq COMMA expr"
        p[1].append(p[3])
        p[0] = p[1]

    def p_node_expr_seq_single(self, p):
        "node_expr_seq : node_expr"
        p[0] = [p[1]]

    def p_node_expr_seq_many(self, p):
        "node_expr_seq : node_expr_seq COMMA node_expr"
        p[1].append(p[3])
        p[0] = p[1]

    def p_agg_expr(self, p):
        "agg_expr : agg_method LBRACKET node_expr_seq WHERE formula GROUP BY node_expr_seq RBRACKET"
        p[0] = AggExpr(p[1], p[3], p[5], p[8])

    def p_agg_expr_bare(self, p):
        "agg_expr : agg_method LBRACKET node_expr_seq RBRACKET"
        # Aggregate over a bare expression with no WHERE filter or GROUP BY,
        # e.g. an ontology-level constraint like "COUNT[Airport] > 0".
        p[0] = AggExpr(p[1], p[3], None, [])

    def p_agg_method(self, p):
        """
        agg_method : AVG
                   | COUNT
                   | MAX
                   | MIN
                   | SUM
        """
        p[0] = AggregationMethod(AggMethod.from_value(p[1]))

    def p_comp_op(self, p):
        """
        comp_op : EQUALS
                | NOTEQUALS
                | LESS
                | LESSEQ
                | GREATER
                | GREATEREQ
        """
        p[0] = p[1]

    def p_literal(self, p):
        """
        literal : INTEGER
                | FLOAT
                | STRING_LITERAL
        """
        p[0] = LiteralHandle(p[1])

    def p_literal_bool(self, p):
        """
        literal : TRUE
                | FALSE
        """
        p[0] = LiteralHandle(p[1] == "TRUE")

    def p_error(self, p):
        raise ValueError("Unable to parse:", p)

    # ----------------- identifier resolution -----------------------------

    def _to_identifier_ref(self, val, container: Concept | None = None) -> Atom:
        if isinstance(val, Atom):
            return val

        concept = self._ontology.lookup_concept(val)
        relationship = (
            self._ontology.lookup_concept_relationship(container, val)
            if container
            else None  # all relationships live under a concept; can't resolve without one
        )
        dataset = self._lookup_dataset(val)

        matches = [m for m in (concept, relationship, dataset) if m is not None]
        assert len(matches) <= 1, (
            f"Conflicting identifier: {val}, must be unique across concepts, relationships or datasets"
        )

        if concept is not None:
            return ConceptRefHandle(concept)
        if relationship is not None:
            return RelationshipRefHandle(relationship)
        if dataset is not None:
            return DatasetRefHandle(dataset)
        return VarHandle(val)

    def _lookup_dataset(self, name: str) -> Dataset | None:
        if self._semantic_model is None:
            return None
        return self._semantic_model.lookup_dataset(name)

    # ----------------- entrypoint ----------------------------------------

    def parse_formula(
        self,
        formula: str,
        head_vars: dict[VarHandle | ConceptRefHandle, Concept] | None = None,
        mapping_dataset: Dataset | None = None,
    ):
        self.parser.params = {
            self._HEAD_VARS_PARAM: head_vars,
            self._MAPPING_DATASET_PARAM: mapping_dataset,
        }
        return self.parser.parse(formula, lexer=self.lexer.lexer)