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

"""Ossie -> RelationalAI: the converter's output, end to end.

Each case parses an Ossie spec, converts it to an in-memory `OntologyModel`, and
decompiles the underlying pyrel model back to PyRel source. The snapshot is that
source — the code a user would otherwise have had to hand-write — so a converter
change shows up as a readable diff rather than a metamodel dump.

Nothing here talks to an engine. `to_metamodel()` compiles in-process, and the
converter declares each dataset's columns from the Ossie spec rather than asking
a warehouse for them — so the tables the generated source names need not exist.
conftest pins `RAI_CONFIG_FILE_PATH` to an offline config. The whole module is
skipped when the optional `relationalai` extra is not installed:

    pip install -e ".[relationalai]"

The snapshots are `.py`, because that is what they are: runnable PyRel source,
which `test_generated_pyrel_is_executable` below executes. pytest does not
collect them — it only imports files matching `test_*.py` — and `[tool.pyright]`
excludes `tests/snapshots`, since generated code that assigns attributes onto
`Concept` objects produces thousands of type errors that say nothing about the
converter. Keeping the real extension means an IDE highlights them, a reviewer
reads them as Python, and nothing has to be configured to make that work.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.parser import OssieParser
from ossie_ontology.vendor.palantir.parser import PalantirParser

pyrel = pytest.importorskip(
    "relationalai.semantics",
    reason='needs the optional relationalai extra: pip install -e ".[relationalai]"',
)

from relationalai.semantics.metamodel.pyrel_codegen import to_pyrel  # noqa: E402
from relationalai.semantics.metamodel.typer import Typer  # noqa: E402

from ossie_ontology.converter.ossie_to_relationalai import (  # noqa: E402
    OntologyModel,
    OssieToRelationalAIConverter,
    declared_table,
)


# Specs under tests/fixtures/. Each name is the stem of `<name>.yaml` and of the
# snapshot it produces.
from tests.conftest import SPEC_NAMES as SPECS


def convert(spec: str, specs_dir: Path) -> OntologyModel:
    """Parse the named spec and convert it.

    `declared_table` rather than the default provider: it takes the column types
    from the Ossie spec instead of reading them off the real table, so nothing
    needs a connection and the tables the generated source names need not exist.
    The default is the other way round for a good reason — only the table knows
    how its identifiers were cased — which is exactly why these tests pick.

    No rows are supplied and none are needed; reading real data is an extension
    — a CSV-backed provider, for example — not something this converter knows.
    """
    # pyrel keeps constructed models in a process-wide registry; clearing it
    # keeps each case independent and its generated names stable.
    pyrel.Model.all_models.clear()
    return OssieToRelationalAIConverter.convert(
        OssieParser().parse(specs_dir / f"{spec}.yaml"), table_provider=declared_table
    )


def pyrel_source(spec: str, specs_dir: Path) -> str:
    return to_pyrel(convert(spec, specs_dir).base_model().to_metamodel())


@pytest.mark.parametrize("spec", SPECS)
def test_converted_model_decompiles_to_expected_pyrel(snapshot, specs_dir: Path, spec: str):
    snapshot.assert_match(pyrel_source(spec, specs_dir), f"{spec}.py")


@pytest.mark.parametrize("spec", SPECS)
def test_generated_pyrel_is_executable(specs_dir: Path, spec: str):
    """The generated source has to be runnable PyRel, not merely readable.

    Executes it in a fresh namespace and type-checks the model it builds, which
    is what stops the snapshot above from silently locking in source that no one
    could actually run.
    """
    source = pyrel_source(spec, specs_dir)

    namespace: dict = {}
    pyrel.Model.all_models.clear()
    exec(compile(source, f"generated_{spec}.py", "exec"), namespace)
    Typer().infer_model(namespace["m"].to_metamodel())


def test_conversion_is_deterministic(specs_dir: Path):
    """Two conversions of one spec produce identical source.

    The converter leans on dict iteration order throughout; if any of it ever
    fell back to set or hash ordering, the snapshots above would start failing
    intermittently rather than telling anyone why.
    """
    assert pyrel_source("flights", specs_dir) == pyrel_source("flights", specs_dir)


def test_converter_result_is_an_ontology_model(specs_dir: Path):
    """convert() returns the in-memory ontology, with its concepts resolvable."""
    om = convert("flights", specs_dir)

    assert isinstance(om, OntologyModel)
    assert om.lookup_concept("Airport") is not None
    assert om.lookup_concept("NoSuchConcept") is None


# ---------------------------------------------------------------------------
# The full spoke-to-spoke path: Palantir -> Ossie -> RelationalAI
# ---------------------------------------------------------------------------

# An extracted Palantir export on disk, the same shape the real exports have:
# an ontology JSON plus a data_sets/ folder. Kept as files rather than built
# inline so it reads the way the Ossie fixtures do, and so it can be swapped for
# a real export without touching the test.
PALANTIR_SPEC = "retail_mini"


def test_palantir_export_converts_all_the_way_to_pyrel(snapshot, fixtures_dir: Path):
    """Palantir JSON in, executable PyRel out, through the Ossie hub.

    Neither converter alone proves this works: `palantir_to_ossie` stops at the
    Ossie model and `ossie_to_relationalai` starts from one. This is the only
    test that runs a vendor format through the hub and out the other side, which
    is the point of the hub-and-spoke arrangement.
    """
    ontology = PalantirToOssieConverter().convert(
        PalantirParser().parse(fixtures_dir / "palantir" / PALANTIR_SPEC)
    )

    pyrel.Model.all_models.clear()
    om = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)

    assert isinstance(om, OntologyModel)
    # Both Palantir object types survived the round trip as concepts.
    assert om.lookup_concept("Store") is not None
    assert om.lookup_concept("Sale") is not None

    source = to_pyrel(om.base_model().to_metamodel())
    snapshot.assert_match(source, "palantir_chain.py")

    namespace: dict = {}
    pyrel.Model.all_models.clear()
    exec(compile(source, "generated_palantir_chain.py", "exec"), namespace)
    Typer().infer_model(namespace["m"].to_metamodel())


def test_a_spec_without_datatypes_declares_string_columns(snapshot, behaviours_dir: Path):
    """No `datatype:` anywhere, so every declared column is `String`.

    A column's type comes only from its own `datatype`; the mappings that read
    it no longer type it. `ref_scheme.yaml` declares none, so `declared_table`
    gets an all-`String` schema even for the integer keys `storeNr` and
    `saleNr` that mappings used to pin. The generated source must still type-check.
    """
    pyrel.Model.all_models.clear()
    om = OssieToRelationalAIConverter.convert(
        OssieParser().parse(behaviours_dir / "ref_scheme.yaml"), table_provider=declared_table
    )
    source = to_pyrel(om.base_model().to_metamodel())

    column_types = re.findall(r"'[A-Z_]+': (\w+)", source)
    assert column_types and set(column_types) == {"String"}, column_types
    snapshot.assert_match(source, "untyped_columns.py")

    namespace: dict = {}
    pyrel.Model.all_models.clear()
    exec(compile(source, "generated_untyped_columns.py", "exec"), namespace)
    Typer().infer_model(namespace["m"].to_metamodel())


# ---------------------------------------------------------------------------
# Derived identifiers
# ---------------------------------------------------------------------------

def test_a_derived_identifier_creates_the_entity(behaviours_dir: Path):
    """A derived identifier must emit `Concept.new(...)`, not a bare fact.

    `_head` in the formula emitter picks between the two on
    `reasoner.is_identifier_relationship`, which reads the reasoner's registry
    of constructing roles. That registry was populated by nothing for a long
    time, so the predicate answered False for every relationship and this rule
    came out as `Product.sku(...)` — an assertion about a Product that no rule
    creates. It type-checked and produced an empty result, so nothing caught it.

    No other fixture pairs `identify_by` with `derived_by`, which is why the
    defect survived: this test is the only thing standing on that branch.
    """
    pyrel.Model.all_models.clear()
    ontology = OssieParser().parse(behaviours_dir / "derived_identifier.yaml")
    om = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)
    source = to_pyrel(om.base_model().to_metamodel())

    rules = [line for line in source.splitlines() if ".define(" in line]
    assert len(rules) == 1, f"expected one derived rule, got: {rules}"
    rule = rules[0]

    assert "Product.new(sku=" in rule, (
        "a derived identifier must create the entity; emitting a plain fact "
        f"leaves nothing to attach it to. Got: {rule}"
    )
    assert "define(Product.sku(" not in rule


def test_the_reasoner_sees_declared_identifiers(behaviours_dir: Path):
    """The registry behind that decision is actually populated.

    `identify_by` is the only declaration of an identifier Ossie has, so the
    reasoner takes the constructing roles straight from the concept rather than
    waiting for a constraint producer that does not exist.
    """
    from ossie_ontology.reasoner import OntologyReasoner

    ontology = OssieParser().parse(behaviours_dir / "derived_identifier.yaml")
    reasoner = OntologyReasoner()
    ontology.ontology.register(reasoner)

    identifiers = [
        rel for rel in ontology.ontology.relationships
        if reasoner.is_identifier_relationship(rel)
    ]
    assert {rel.name for rel in identifiers} == {"sku", "legacyCode"}


# ---------------------------------------------------------------------------
# Reference schemes behind bare-expression mappings
# ---------------------------------------------------------------------------

# The table variables the decompiler names the two datasets after, derived from
# their `source:` in the fixture. Spelled out so the assertions below can tell
# the STORES-driven rules from the SALES-driven ones.
STORES_TABLE = "db_schema_stores"
SALES_TABLE = "db_schema_sales"


def define_rules(source: str) -> list[str]:
    return [line for line in source.splitlines() if ".define(" in line]


def test_bare_expression_mappings_resolve_the_ref_scheme(behaviours_dir: Path):
    """Three call sites ask the reasoner which relationship identifies a concept.

    A mapping may give a bare `expression` instead of `referent_mappings`, and a
    derived concept may extend nothing. In both shapes the converter knows the
    value but not what to call it, so it asks `ref_scheme` for the identifying
    relationship and uses its name as the keyword of `Concept.new(...)`:

      * `_build_entity_binding` — Store mapped by expression alone;
      * `_apply_link_binding`   — Sale -> soldAt -> Store, a bare-expression link
        node with children, which needs a child binding scoped to the Store;
      * `FormulaConverter._head` — LargeSale, derived and extending nothing.

    Every other fixture identifies entities through `referent_mappings` and
    derives only subtypes, so all three branches ran without a test over them.
    """
    pyrel.Model.all_models.clear()
    ontology = OssieParser().parse(behaviours_dir / "ref_scheme.yaml")
    source = to_pyrel(
        OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)
        .base_model().to_metamodel()
    )
    defined = define_rules(source)

    # Site 1: the expression names no relationship, so `nr` can only have come
    # from the ref scheme. Without it the converter cannot key the entity at all.
    assert any("Store.new(nr=" in rule and STORES_TABLE in rule for rule in defined), (
        f"expected a Store creation rule keyed by its ref scheme, got: {defined}"
    )

    # Site 2: the grandchild property has to land on the Store the expression
    # identifies. When the ref scheme comes back empty the converter falls back
    # to the parent binding and the rule is emitted against the Sale instead —
    # silently attaching a store's region to a sale.
    region_rules = [rule for rule in defined if ".region(" in rule and SALES_TABLE in rule]
    assert len(region_rules) == 1, f"expected one region rule off SALES, got: {region_rules}"
    assert "store.region(" in region_rules[0], (
        f"region reached through soldAt must be scoped to the Store: {region_rules[0]}"
    )

    # ...and keyed by the identifier value, not by the entity. `binds_to` wants
    # `Store(col)` and the ref-scheme keyword wants `col`; rendering once and
    # using it for both keyed the Store by a Store — `Store.new(nr=Store(col))`,
    # which the typer rejects with Expected 'StoreNr', got 'Store'.
    keyed = [rule for rule in defined if "Store.new(nr=" in rule and SALES_TABLE in rule]
    assert len(keyed) == 1, f"expected one Store creation rule off SALES, got: {keyed}"
    assert f"Store.new(nr={SALES_TABLE}.STORENR)" in keyed[0], (
        f"the ref-scheme keyword takes the bare column, not the entity: {keyed[0]}"
    )

    # Site 3: a derived concept that extends nothing is created, not asserted —
    # the same distinction test_a_derived_identifier_creates_the_entity draws,
    # reached through the concept branch of `_head` rather than the relationship one.
    assert any("LargeSale.new(nr=" in rule for rule in defined), (
        f"expected LargeSale to be created with its identifier, got: {defined}"
    )


def test_a_value_type_wrapper_casts_the_column(behaviours_dir: Path):
    """`StoreNr(T.id)` in a mapping becomes `StoreNr(col)` in pyrel, once.

    Wherever the expression lands — an entity's key through a bare expression
    or a referent mapping, or a property value — the wrapper is emitted as the
    cast it names. A mapping that also names `concept: Seats` already has its
    cast in the wrapper, and must not get a second, `Seats(Seats(col))`.
    """
    pyrel.Model.all_models.clear()
    ontology = OssieParser().parse(behaviours_dir / "value_type_wrapper.yaml")
    source = to_pyrel(
        OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table).base_model().to_metamodel()
    )
    defined = define_rules(source)

    assert "m.define(Store.new(nr=StoreNr(db_schema_t.ID)))" in defined
    assert "m.define(Depot.new(nr=StoreNr(db_schema_t.ID)))" in defined
    assert any(".define(store.seats(Seats(db_schema_t.CAP)))" in rule for rule in defined), defined
    assert any(".define(store.extra(Seats(db_schema_t.RAW)))" in rule for rule in defined), defined
    assert "Seats(Seats(" not in source

    namespace: dict = {}
    pyrel.Model.all_models.clear()
    exec(compile(source, "generated_value_type_wrapper.py", "exec"), namespace)
    Typer().infer_model(namespace["m"].to_metamodel())


# ---------------------------------------------------------------------------
# Mapping formulas over bare dataset columns
# ---------------------------------------------------------------------------

def test_a_bare_column_mapping_formula_converts(validation_dir: Path):
    """A column named without its dataset resolves to that dataset's table.

    Spec documents must qualify columns (`PAYMENTS.cashReceived`), but
    `FormulaParser.parse_formula` takes a `mapping_dataset` that lets an
    expression name them bare. Those come out as `DatasetFieldHandle`s, which
    `MappingFormulaConverter.visit_datasetfieldhandle` resolves by dataset name.
    It called `Dataset.name` — a property — as a method, so every such formula
    raised `TypeError` there; the qualified form goes through
    `visit_dotjoinhandle` and never touched that line.
    """
    from collections import OrderedDict

    from ossie_ontology.expr.factory import Formula, FormulaCollector
    from ossie_ontology.expr.formula.parser import FormulaParser
    from ossie_ontology.expr.formula.visitor.converter import MappingFormulaConverter
    from ossie_ontology.vendor.relationalai.mappings import ValueMapping

    pyrel.Model.all_models.clear()
    ontology = OssieParser().parse(validation_dir / "mapping_formula_valid.yaml")
    om = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)

    mapping, = ontology.ontology_mappings
    payments = mapping.semantic_model.lookup_dataset("PAYMENTS")
    raw = "cashReceived - change"
    expr = FormulaParser(ontology.ontology, mapping.semantic_model).parse_formula(
        raw, mapping_dataset=payments
    )
    collector = FormulaCollector({})
    expr.accept(collector)
    formula = Formula([expr], collector.expression_info(), OrderedDict(), raw, None)

    result = MappingFormulaConverter(om, formula).result

    assert isinstance(result, ValueMapping)
    assert result.expr() is not None


# ---------------------------------------------------------------------------
# Relationship bindings keyed by identifier
# ---------------------------------------------------------------------------

def test_binding_by_identifier_alone_looks_up_the_last_role(behaviours_dir: Path):
    """`bind(r, **identifier)` with no `expr` keys the relationship's last role.

    Without an `expr`, `GRBinding` builds an `EntityMapping` for the concept
    playing `r`'s last role from the identifier keywords. It asked the model for
    that player without saying which relationship it meant, so the call raised
    `TypeError` before it could pick one — and every caller passed `expr`, so
    nothing noticed.
    """
    from ossie_ontology.vendor.relationalai.mappings import EntityMapping

    pyrel.Model.all_models.clear()
    ontology = OssieParser().parse(behaviours_dir / "ref_scheme.yaml")
    om = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)
    sale = om.lookup_concept("Sale")
    sales = om.lookup_table("SALES")
    assert sale is not None and sales is not None
    before = define_rules(to_pyrel(om.base_model().to_metamodel()))

    om.RelationshipBinding(EntityMapping(sale, nr=sales.saleNr)).bind(
        sale.soldAt, nr=sales.storeNr
    )

    added = [
        rule for rule in define_rules(to_pyrel(om.base_model().to_metamodel()))
        if rule not in before
    ]
    assert len(added) == 1, f"expected one soldAt rule, got: {added}"
    # The player is Store — soldAt's last role — looked up by the keyword given.
    assert f"store := Store.lookup(nr={SALES_TABLE}.STORENR)" in added[0], added[0]
    assert "define(sale.soldAt(store))" in added[0], added[0]


# ---------------------------------------------------------------------------
# Relationships skipped for clashing with PyRel reserved names
# ---------------------------------------------------------------------------

def _dataset_field(name: str) -> dict:
    return {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]}}


# Gadget's `ref` is an ordinary property mapped by a link; Gizmo's `ref` is its
# identifier. `ref` is a method on every PyRel Concept, so both are skipped.
_RESERVED_NAME_SPEC = {
    "version": "0.2.0.dev0",
    "name": "Reserved",
    "ontology": [
        {"concept": "GadgetId", "type": "ValueType", "extends": ["Integer"]},
        {"concept": "Label", "type": "ValueType", "extends": ["String"]},
        {"concept": "GizmoId", "type": "ValueType", "extends": ["Integer"]},
        {
            "concept": "Gadget",
            "type": "EntityType",
            "identify_by": ["id"],
            "relationships": [
                {"name": "id", "roles": [{"concept": "GadgetId"}], "multiplicity": "OneToOne"},
                {"name": "ref", "roles": [{"concept": "Label"}], "multiplicity": "ManyToOne"},
                {"name": "label", "roles": [{"concept": "Label"}], "multiplicity": "ManyToOne"},
            ],
        },
        {
            "concept": "Gizmo",
            "type": "EntityType",
            "identify_by": ["ref"],
            "relationships": [
                {"name": "ref", "roles": [{"concept": "GizmoId"}], "multiplicity": "OneToOne"},
            ],
        },
    ],
    "ontology_mappings": [
        {
            "name": "reserved_mapping",
            "semantic_model": {
                "version": "0.2.0.dev0",
                "name": "Reserved semantic model",
                "datasets": [
                    {
                        "name": "GADGETS",
                        "source": "DB.SCHEMA.GADGETS",
                        "fields": [_dataset_field(f) for f in ("id", "ref", "label")],
                    },
                ],
            },
            "concept_mappings": [
                {
                    "concept": "Gadget",
                    "link_mappings": [
                        {
                            "object_mapping": {
                                "referent_mappings": [
                                    {"relationship": "id", "expression": "GADGETS.id"}
                                ]
                            },
                            "children": [
                                {
                                    "relationship": "ref",
                                    "object_mapping": {"concept": "Label", "expression": "GADGETS.ref"},
                                },
                                {
                                    "relationship": "label",
                                    "object_mapping": {"concept": "Label", "expression": "GADGETS.label"},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}


def test_mappings_over_a_reserved_relationship_are_skipped_with_a_warning(tmp_path: Path):
    """A relationship skipped for its reserved name takes its uses down with it.

    `_convert_relationships` warns and skips a relationship whose name clashes
    with a PyRel `Concept` method, so it never reaches `properties_index`. The
    identifier and link-mapping lookups indexed that dict directly, so the skip
    the warning promised ended in a KeyError instead.
    """
    import yaml

    path = tmp_path / "reserved.yaml"
    path.write_text(yaml.safe_dump(_RESERVED_NAME_SPEC))
    ontology = OssieParser().parse(path)

    pyrel.Model.all_models.clear()
    with pytest.warns(UserWarning) as caught:
        om = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)
    messages = [str(w.message) for w in caught]

    assert any("Skipping identify_by on 'Gizmo'" in m for m in messages), messages
    assert any("Skipping link mapping" in m and "'Gadget.ref'" in m for m in messages), messages

    # The rest of the mapping still converts.
    defined = define_rules(to_pyrel(om.base_model().to_metamodel()))
    assert any(".label(" in rule for rule in defined), defined
    assert not any(".ref(" in rule for rule in defined), defined


# ---------------------------------------------------------------------------
# Entity bindings keyed through nested referents
# ---------------------------------------------------------------------------

# A Desk is identified by the Employee it belongs to, and an Employee by its
# number. DESKS names two employees per row, so Desk is mapped twice: once per
# column. Both mappings use `owner` at the top level and differ only in the
# nested referent below it.
_NESTED_REFERENTS_SPEC = {
    "version": "0.2.0.dev0",
    "name": "Nested",
    "ontology": [
        {"concept": "EmpNr", "type": "ValueType", "extends": ["Integer"]},
        {
            "concept": "Employee",
            "type": "EntityType",
            "identify_by": ["nr"],
            "relationships": [
                {"name": "nr", "roles": [{"concept": "EmpNr"}], "multiplicity": "OneToOne"},
            ],
        },
        {
            "concept": "Desk",
            "type": "EntityType",
            "identify_by": ["owner"],
            "relationships": [
                {"name": "owner", "roles": [{"concept": "Employee"}], "multiplicity": "OneToOne"},
            ],
        },
    ],
    "ontology_mappings": [
        {
            "name": "nested_mapping",
            "semantic_model": {
                "version": "0.2.0.dev0",
                "name": "Nested semantic model",
                "datasets": [
                    {
                        "name": "DESKS",
                        "source": "DB.SCHEMA.DESKS",
                        "fields": [_dataset_field(f) for f in ("primaryNr", "backupNr")],
                    },
                ],
            },
            "concept_mappings": [
                {
                    "concept": "Desk",
                    "object_mappings": [
                        {
                            "referent_mappings": [{
                                "relationship": "owner",
                                "referent_mappings": [
                                    {"relationship": "nr", "expression": f"DESKS.{column}"}
                                ],
                            }]
                        }
                        for column in ("primaryNr", "backupNr")
                    ],
                }
            ],
        }
    ],
}


def test_object_mappings_differing_only_in_nested_referents_both_create(tmp_path: Path):
    """Each nested referent creates its own entities.

    Bindings are cached by `_object_mapping_key` so one concept mapped twice the
    same way gets one creation rule. A referent with nested `referent_mappings`
    has no `expression` of its own, and the key stopped at the top level — so
    these two mappings keyed the same, the second reused the first's binding,
    and the Desks of the backup column were silently never created.
    """
    import yaml

    path = tmp_path / "nested.yaml"
    path.write_text(yaml.safe_dump(_NESTED_REFERENTS_SPEC))
    ontology = OssieParser().parse(path)

    pyrel.Model.all_models.clear()
    om = OssieToRelationalAIConverter.convert(ontology, table_provider=declared_table)
    created = [rule for rule in define_rules(to_pyrel(om.base_model().to_metamodel()))
               if "Desk.new(" in rule]

    assert len(created) == 2, f"expected a Desk creation rule per column, got: {created}"
    assert any("PRIMARYNR" in rule for rule in created), created
    assert any("BACKUPNR" in rule for rule in created), created
