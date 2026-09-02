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


def _table_named(name):
    # Single-quoted YAML scalar: the only escape it needs is doubling a literal
    # single quote, so a backslash in `name` (used for Windows-style path cases)
    # survives unmangled — a double-quoted scalar would try to interpret it as an
    # escape.
    escaped = name.replace("'", "''")
    return f"table:\n  name: '{escaped}'\n  db: SALES\n"


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


class TestFilenameSafety:
    """A table or model name is user-controlled data, and `dump_document_set` turns it
    into a filename. What matters is not the string shape but that joining the result
    onto an output directory and resolving it can never land outside that directory —
    checked with `Path.resolve()` on both sides so a symlinked temp directory (e.g. on
    macOS, where `/tmp` itself is a symlink) can't produce a false positive.
    """

    @pytest.mark.parametrize("name", [
        "../../etc/passwd",
        "/etc/passwd",
        "..\\..\\x",
        "A B",
        "..",
        "",
    ])
    def test_stays_inside_the_output_directory(self, tmp_path, name):
        ds = load_document_set([("t", _table_named(name)), ("m", MODEL)])
        out_dir = (tmp_path / "intended_output")
        out_dir.mkdir()
        resolved_out_dir = out_dir.resolve()
        for filename, _text in dump_document_set(ds):
            target = (out_dir / filename).resolve()
            assert target.is_relative_to(resolved_out_dir)

    def test_a_normal_name_is_unchanged(self):
        ds = load_document_set([("t", _table_named("ORDERS")), ("m", MODEL)])
        names = [name for name, _text in dump_document_set(ds)]
        assert names[0] == "ORDERS.table.tml"

    def test_distinct_names_that_collide_after_sanitising_do_not_overwrite_each_other(self):
        # `A/B` and `A\B` both lose their separator to the same replacement character.
        ds = load_document_set([
            ("a", _table_named("A/B")),
            ("b", _table_named("A\\B")),
            ("m", MODEL),
        ])
        names = [name for name, _text in dump_document_set(ds)]
        table_names = names[:-1]
        assert len(table_names) == len(set(table_names))
        assert table_names == ["A_B.table.tml", "A_B-2.table.tml"]


class TestFilenameCollisionSafety:
    """Sanitising two distinct names onto the same stem is not enough to guarantee
    distinct filenames by itself — a disambiguating counter has to be checked against
    every filename actually being emitted in this call, not just against how many
    times its own stem has been seen, or a counter-suffixed name can land on another
    document's real name and one document silently overwrites the other on disk. Every
    case below asserts only that the emitted filenames are pairwise distinct, not any
    particular suffix, so it keeps holding if the disambiguation scheme changes.
    """

    def test_a_third_name_matching_the_second_names_disambiguated_filename(self):
        # `A/B` and `A\B` both sanitise to `A_B` and would naively disambiguate to
        # `A_B` and `A_B-2`; a third table literally named `A_B-2` must not be handed
        # that same filename.
        ds = load_document_set([
            ("a", _table_named("A/B")),
            ("b", _table_named("A\\B")),
            ("c", _table_named("A_B-2")),
            ("m", MODEL),
        ])
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names) == len(set(names))

    def test_the_same_three_names_in_a_different_processing_order(self):
        ds = load_document_set([
            ("c", _table_named("A_B-2")),
            ("a", _table_named("A/B")),
            ("b", _table_named("A\\B")),
            ("m", MODEL),
        ])
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names) == len(set(names))

    def test_four_names_that_all_sanitise_to_the_same_stem(self):
        ds = load_document_set([
            ("a", _table_named("A/B")),
            ("b", _table_named("A\\B")),
            ("c", _table_named("A:B")),
            ("d", _table_named("A|B")),
            ("m", MODEL),
        ])
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names) == len(set(names))

    def test_a_table_sharing_the_models_raw_name(self):
        # A table's suffix (`.table.tml`) and the model's (`.model.tml`) differ, so this
        # pair can't collide under the current suffix scheme — but both filenames are
        # still minted from the same reservation set, and this proves that holds when
        # the raw names match too, not only when they happen to differ.
        ds = load_document_set([("t", _table_named("Sales")), ("m", MODEL)])
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names) == len(set(names))


class TestFilenameLengthCap:
    """A ThoughtSpot table or model name has no length limit of its own, but a
    filename component does — 255 bytes on most filesystems. A name at or past that
    boundary is truncated at mint time rather than left to fail when something tries
    to write the file.
    """

    def test_a_very_long_name_is_truncated_to_fit(self):
        ds = load_document_set([("t", _table_named("y" * 1000)), ("m", MODEL)])
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names[0].encode("utf-8")) <= 255

    def test_two_long_names_sharing_a_prefix_stay_distinct_after_truncation(self):
        # Identical for long enough that truncation collapses them to the same stem —
        # the two names differ only in their last four characters, well past where a
        # 255-byte cap cuts them off.
        name_a = ("x" * 250) + "AAAA"
        name_b = ("x" * 250) + "BBBB"
        ds = load_document_set([
            ("a", _table_named(name_a)),
            ("b", _table_named(name_b)),
            ("m", MODEL),
        ])
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names) == len(set(names))
        for name in names:
            assert len(name.encode("utf-8")) <= 255


class TestNestedGuidStripping:
    """The guid rule applies at every depth of the body, not only the document root —
    a nested guid is silently ignored on import, and ThoughtSpot creates a duplicate
    object rather than updating the one that already exists.
    """

    def test_a_guid_one_level_deep_is_stripped(self):
        doc = TmlDocument(kind="table", body={"name": "T", "guid": "should-not-survive"}, guid=None)
        out = dump_document(doc)
        assert "should-not-survive" not in out
        assert "guid" not in out

    def test_a_guid_inside_a_list_of_column_entries_is_stripped(self):
        doc = TmlDocument(kind="table", body={
            "name": "T",
            "columns": [
                {"name": "A", "guid": "col-a-guid"},
                {"name": "B", "guid": "col-b-guid"},
            ],
        }, guid=None)
        out = dump_document(doc)
        assert "col-a-guid" not in out
        assert "col-b-guid" not in out
        assert load_document(out).body["columns"] == [{"name": "A"}, {"name": "B"}]

    def test_a_document_with_no_guid_anywhere_is_unchanged(self):
        doc = TmlDocument(kind="table", body={"name": "T", "columns": [{"name": "A"}]}, guid=None)
        out = dump_document(doc)
        assert load_document(out).body == doc.body

    def test_dump_document_does_not_mutate_the_callers_body(self):
        body = {
            "name": "T",
            "guid": "root-level-in-body",
            "columns": [{"name": "A", "guid": "col-guid"}],
        }
        doc = TmlDocument(kind="table", body=body, guid=None)
        dump_document(doc)
        assert body["guid"] == "root-level-in-body"
        assert body["columns"][0]["guid"] == "col-guid"

    def test_fqn_is_left_alone(self):
        doc = TmlDocument(kind="model", body={
            "name": "M",
            "model_tables": [{"name": "T", "fqn": "db.schema.t", "guid": "should-strip"}],
        }, guid=None)
        out = dump_document(doc)
        assert "db.schema.t" in out
        assert "should-strip" not in out


class TestAdditionalCoverage:
    """Two cases judged most likely to bite in practice.

    A document whose body is a list rather than a mapping exercises a defensive branch
    in `load_document` that no other test here reaches — worth proving the guard
    actually fires rather than trusting it by inspection.

    Duplicate table names in one document set are a plausible real-world input (the same
    physical table re-emitted by an upstream step, or two directories merged without a
    dedupe pass). `table_by_name` still silently returns only the first match on such a
    duplicate — a known limitation of set assembly, not of this module's filename layer,
    since nothing here can tell two identically-named tables apart. The filename
    collision duplicate names used to also cause is gone: it is resolved by the same
    scheme `dump_document_set` uses for any other filename collision (see
    `TestFilenameCollisionSafety`).
    """

    def test_a_document_whose_body_is_a_list_raises(self):
        with pytest.raises(ConversionError, match="must be a mapping"):
            load_document("table:\n- name: A\n- name: B\n")

    def test_duplicate_table_names_still_shadow_on_lookup_but_no_longer_collide_on_dump(self):
        table_a = "table:\n  name: ORDERS\n  db: SALES_A\n"
        table_b = "table:\n  name: ORDERS\n  db: SALES_B\n"
        ds = load_document_set([("a", table_a), ("b", table_b), ("m", MODEL)])

        # Lookup still silently returns only the first match — a known limitation of
        # set assembly, unrelated to the filename layer this module owns.
        assert ds.table_by_name("ORDERS").body["db"] == "SALES_A"

        # But dumping no longer loses one of them to a filename collision.
        names = [name for name, _text in dump_document_set(ds)]
        assert len(names) == len(set(names))
