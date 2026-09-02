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

import pytest

from ossie_thoughtspot import _yaml
from ossie_thoughtspot.errors import ConversionError
from ossie_thoughtspot.tml import (
    DocumentSet, TmlDocument, block_scalar, dump_document,
    dump_document_set, load_document, load_document_set,
)

TABLE = """\
guid: tbl-orders-001
table:
  name: ORDERS
  db: SALES
  schema: PUBLIC
  columns:
  - name: AMOUNT
    db_column_name: AMOUNT
    properties:
      column_type: MEASURE
    db_column_properties:
      data_type: DOUBLE
"""

MODEL = """\
guid: model-001
model:
  name: Sales
  model_tables:
  - name: ORDERS
  formulas:
  - id: formula_Revenue
    name: Revenue
    expr: sum ( [ORDERS::AMOUNT] )
  columns:
  - name: Revenue
    formula_id: formula_Revenue
    properties:
      column_type: MEASURE
"""


class TestLoad:
    def test_detects_a_table_document(self):
        doc = load_document(TABLE)
        assert doc.kind == "table"
        assert doc.body["name"] == "ORDERS"
        assert doc.guid == "tbl-orders-001"

    def test_detects_a_model_document(self):
        assert load_document(MODEL).kind == "model"

    def test_a_document_with_no_recognised_root_key_raises(self):
        with pytest.raises(ConversionError, match="not a TML document"):
            load_document("answer:\n  name: Nope\n")

    def test_a_document_with_two_root_kinds_raises(self):
        with pytest.raises(ConversionError, match="more than one"):
            load_document("table:\n  name: A\nmodel:\n  name: B\n")

    def test_yaml_1_1_boolean_tokens_survive_as_strings(self):
        # A column really can be called `on` — it must not be coerced to a boolean.
        doc = load_document("table:\n  name: T\n  columns:\n  - name: 'on'\n")
        assert doc.body["columns"][0]["name"] == "on"


class TestLoadDocumentSet:
    def test_splits_the_model_from_the_tables(self):
        ds = load_document_set([("orders.table.tml", TABLE), ("sales.model.tml", MODEL)])
        assert ds.model.body["name"] == "Sales"
        assert [t.body["name"] for t in ds.tables] == ["ORDERS"]

    def test_order_of_input_does_not_matter(self):
        ds = load_document_set([("sales.model.tml", MODEL), ("orders.table.tml", TABLE)])
        assert ds.model.body["name"] == "Sales"

    def test_table_lookup_by_name(self):
        ds = load_document_set([("o", TABLE), ("m", MODEL)])
        assert ds.table_by_name("ORDERS").body["db"] == "SALES"
        assert ds.table_by_name("MISSING") is None

    def test_no_model_raises(self):
        with pytest.raises(ConversionError, match="no model document"):
            load_document_set([("o", TABLE)])

    def test_two_models_raise(self):
        with pytest.raises(ConversionError, match="more than one model"):
            load_document_set([("m1", MODEL), ("m2", MODEL)])


class TestDump:
    def test_guid_is_never_written(self):
        # The single most consequential invariant in this module.
        out = dump_document(load_document(TABLE))
        assert "guid" not in out
        assert "tbl-orders-001" not in out

    def test_the_kind_key_is_the_document_root(self):
        out = dump_document(load_document(TABLE))
        assert out.startswith("table:")

    def test_a_brace_expression_is_written_as_a_block_scalar(self):
        # A plain scalar containing `{ }` fails to parse on re-read.
        doc = TmlDocument(kind="model", body={
            "name": "M",
            "formulas": [{"id": "formula_X", "name": "X",
                          "expr": block_scalar("last_value ( sum ( [T::c] ) , { [D::d] } )")}],
        }, guid=None)
        out = dump_document(doc)
        assert ">-" in out
        assert _yaml.load(out)["model"]["formulas"][0]["expr"].strip() == (
            "last_value ( sum ( [T::c] ) , { [D::d] } )"
        )

    def test_an_on_key_is_quoted(self):
        # `on` is a YAML 1.1 reserved word; unquoted it would come back as True.
        doc = TmlDocument(kind="table", body={
            "name": "T",
            "joins_with": [{"name": "j", "on": "[A::x] = [B::y]",
                            "type": "INNER", "cardinality": "MANY_TO_ONE"}],
        }, guid=None)
        out = dump_document(doc)
        assert "'on':" in out
        assert _yaml.load(out)["table"]["joins_with"][0]["on"] == "[A::x] = [B::y]"

    def test_round_trips_through_load(self):
        doc = load_document(TABLE)
        assert load_document(dump_document(doc)).body == doc.body


class TestDumpDocumentSet:
    def test_tables_come_before_the_model(self):
        # The model references tables by name, so they must exist first.
        ds = load_document_set([("m", MODEL), ("o", TABLE)])
        names = [name for name, _text in dump_document_set(ds)]
        assert names == ["ORDERS.table.tml", "Sales.model.tml"]

    def test_every_emitted_document_reloads(self):
        ds = load_document_set([("o", TABLE), ("m", MODEL)])
        for _name, text in dump_document_set(ds):
            load_document(text)


class TestAdditionalCoverage:
    """Two cases judged most likely to bite in practice, beyond the transcribed set.

    A document whose body is a list rather than a mapping exercises a defensive branch
    in `load_document` that the transcribed tests never reach — worth proving the guard
    actually fires rather than trusting it by inspection.

    Duplicate table names in one document set are a plausible real-world input (the same
    physical table re-emitted by an upstream step, or two directories merged without a
    dedupe pass) and they hit two silent failure modes at once: `table_by_name` returns
    only the first match with no signal that a second exists, and `dump_document_set`
    produces two `(filename, text)` pairs with the identical filename — a caller that
    writes these to disk loses one table's document with no error raised anywhere in
    this module.
    """

    def test_a_document_whose_body_is_a_list_raises(self):
        with pytest.raises(ConversionError, match="must be a mapping"):
            load_document("table:\n- name: A\n- name: B\n")

    def test_duplicate_table_names_shadow_on_lookup_and_collide_on_dump(self):
        table_a = "table:\n  name: ORDERS\n  db: SALES_A\n"
        table_b = "table:\n  name: ORDERS\n  db: SALES_B\n"
        ds = load_document_set([("a", table_a), ("b", table_b), ("m", MODEL)])

        # Lookup silently returns only the first match.
        assert ds.table_by_name("ORDERS").body["db"] == "SALES_A"

        # Both documents are still dumped, but under the identical filename.
        names = [name for name, _text in dump_document_set(ds)]
        assert names.count("ORDERS.table.tml") == 2
