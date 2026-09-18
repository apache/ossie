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

"""Tests for the Hologres model_yaml -> Apache Ossie import.

The two model_yaml fixtures were read back from a real Hologres 5.0.0 instance via
`hologres.hg_semantic_view_properties`, so they are the shape Hologres actually emits
rather than a shape inferred from documentation.
"""

import subprocess
import sys

import pytest
from _util import SCHEMA, VALIDATOR, read_fixture
from ossie_hologres import ConversionError, convert_semantic_view_to_ossie
from ossie_hologres._common import dump_yaml, load_yaml


def import_view(view, **kwargs):
    return load_yaml(convert_semantic_view_to_ossie(dump_yaml(view), **kwargs))


def model_of(view, **kwargs):
    return import_view(view, **kwargs)


def table(name="o", table_name="orders", columns=("id",), **extra):
    entry = {
        "name": name,
        "base_table": {"database": "db", "schema": "public", "table": table_name},
    }
    if columns:
        entry["primary_key"] = {"columns": list(columns)}
    entry.update(extra)
    return entry


def view(*tables, **extra):
    return {"name": "sv", "tables": list(tables), **extra}


class TestGoldenFixtures:
    @pytest.mark.parametrize("name", ["fixtureA", "fixtureB"])
    def test_import_reproduces_the_ossie_fixture_exactly(self, name):
        # The full loop is Ossie -> DDL -> Hologres -> model_yaml -> Ossie, and these
        # model_yaml fixtures are the instance's own readback of the exported DDL. So a
        # byte-identical result here means the pair round-trips losslessly.
        imported = load_yaml(convert_semantic_view_to_ossie(read_fixture(f"{name}_model_yaml.yaml")))
        assert imported == load_yaml(read_fixture(f"{name}_ossie.yaml"))

    @pytest.mark.parametrize("name", ["fixtureA", "fixtureB"])
    def test_output_passes_the_official_validator(self, name, tmp_path):
        out = tmp_path / "imported.yaml"
        out.write_text(
            convert_semantic_view_to_ossie(read_fixture(f"{name}_model_yaml.yaml")),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(out), "--schema", str(SCHEMA)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Validation PASSED" in result.stdout


class TestDocumentValidation:
    def test_non_mapping_root_is_rejected(self):
        with pytest.raises(ConversionError, match="expected a mapping"):
            convert_semantic_view_to_ossie("- a\n")

    def test_missing_tables_is_rejected(self):
        with pytest.raises(ConversionError, match="'tables' must be a non-empty list"):
            convert_semantic_view_to_ossie("name: sv\n")

    def test_missing_view_name_is_rejected(self):
        with pytest.raises(ConversionError, match="missing required 'name'"):
            convert_semantic_view_to_ossie(dump_yaml({"tables": [table()]}))

    def test_duplicate_table_alias_is_rejected(self):
        with pytest.raises(ConversionError, match="duplicate table alias"):
            import_view(view(table("o"), table("o")))

    def test_model_name_can_be_overridden(self):
        assert model_of(view(table()), model_name="custom")["name"] == "custom"

    def test_invalid_yaml_is_rejected(self):
        with pytest.raises(ConversionError, match="Invalid YAML"):
            convert_semantic_view_to_ossie("a: [\n")


class TestTables:
    def test_alias_becomes_the_dataset_name(self):
        # The alias is what dimensions, metrics and relationships all reference, so using
        # it as the dataset name keeps those references valid with no rewriting.
        assert model_of(view(table("o")))["datasets"][0]["name"] == "o"

    def test_base_table_becomes_a_three_part_source(self):
        assert model_of(view(table()))["datasets"][0]["source"] == "db.public.orders"

    def test_partial_base_table_omits_the_missing_parts(self):
        entry = {"name": "o", "base_table": {"table": "orders"}}
        assert model_of(view(entry))["datasets"][0]["source"] == "orders"

    def test_missing_base_table_is_rejected(self):
        with pytest.raises(ConversionError, match="missing required 'base_table'"):
            import_view(view({"name": "o"}))

    def test_primary_key_columns_are_flattened(self):
        assert model_of(view(table(columns=["a", "b"])))["datasets"][0]["primary_key"] == ["a", "b"]

    def test_table_without_a_primary_key_omits_the_field(self):
        assert "primary_key" not in model_of(view(table(columns=None)))["datasets"][0]

    def test_table_with_metrics_but_no_dimensions_has_no_fields(self):
        # Real Hologres views do this: a table can contribute only a metric.
        entry = table("i", metrics=[{"name": "qty", "expr": "sum(i.quantity)"}])
        dataset = model_of(view(entry))["datasets"][0]
        assert "fields" not in dataset


class TestDimensions:
    def _field(self, expr, alias="o", **extra):
        entry = table(alias, dimensions=[{"name": "d", "expr": expr, **extra}])
        return model_of(view(entry))["datasets"][0]["fields"][0]

    def test_owning_alias_qualifier_is_stripped(self):
        # Hologres stores `o.region`; hand-authored Ossie uses the bare column.
        assert self._field("o.region")["expression"]["dialects"][0]["expression"] == "region"

    def test_multi_column_expression_is_fully_unqualified(self):
        expr = self._field("o.first || ' ' || o.last")["expression"]["dialects"][0]["expression"]
        assert expr == "first || ' ' || last"

    def test_expressions_are_labelled_ansi(self):
        assert self._field("o.region")["expression"]["dialects"][0]["dialect"] == "ANSI_SQL"

    def test_postgres_only_syntax_is_still_labelled_ansi(self):
        # `->` is PostgreSQL JSON access, so the label is not strictly accurate. It is
        # still the better trade: a converter that finds no ANSI_SQL expression drops the
        # field outright, whereas this at worst surfaces as a SQL error on the target.
        field = self._field("o.payload -> 'k'")
        assert field["expression"]["dialects"][0]["dialect"] == "ANSI_SQL"
        assert field["expression"]["dialects"][0]["expression"] == "payload -> 'k'"

    def test_postgres_cast_shorthand_becomes_a_standard_cast(self):
        # The portable spelling is available, so no vendor dialect is needed to carry it.
        expr = self._field("o.region::text")["expression"]["dialects"][0]
        assert expr == {"dialect": "ANSI_SQL", "expression": "CAST(region AS TEXT)"}

    def test_description_is_carried_over(self):
        assert self._field("o.region", description="A region")["description"] == "A region"

    def test_missing_expr_is_rejected(self):
        with pytest.raises(ConversionError, match="missing required 'expr'"):
            import_view(view(table("o", dimensions=[{"name": "d"}])))

    def test_missing_dimension_name_is_rejected(self):
        with pytest.raises(ConversionError, match="missing required 'name'"):
            import_view(view(table("o", dimensions=[{"expr": "o.x"}])))


class TestMetrics:
    def _metrics(self, *entries, alias="o"):
        return model_of(view(table(alias, metrics=list(entries)))).get("metrics", [])

    def test_metrics_are_lifted_to_the_model_level(self):
        metrics = self._metrics({"name": "total", "expr": "sum(o.amount)"})
        assert metrics[0]["name"] == "total"
        assert metrics[0]["expression"]["dialects"][0]["expression"] == "SUM(o.amount)"

    def test_metric_expressions_stay_dataset_qualified(self):
        # Ossie model-level metrics are qualified by dataset name, and because the
        # dataset name *is* the Hologres alias, the expression needs no rewriting.
        expr = self._metrics({"name": "m", "expr": "sum(o.amount)"})[0]
        assert "o.amount" in expr["expression"]["dialects"][0]["expression"]

    def test_count_star_records_its_owner_in_a_stash(self):
        # The owner is not recoverable from `count(*)`, so it must be recorded for the
        # export direction to reconstruct `o.order_count`.
        metric = self._metrics({"name": "n", "expr": "count(*)"})[0]
        assert metric["custom_extensions"] == [
            {"vendor_name": "HOLOGRES", "data": '{"_v": 1, "owner": "o"}'}
        ]

    def test_metrics_with_a_column_reference_need_no_stash(self):
        # The owner is implied by the expression, so stashing it would be noise.
        assert "custom_extensions" not in self._metrics({"name": "m", "expr": "sum(o.amount)"})[0]

    def test_metrics_from_several_tables_are_merged_in_table_order(self):
        model = model_of(
            view(
                table("o", metrics=[{"name": "total", "expr": "sum(o.amount)"}]),
                table("c", table_name="customers", columns=["cid"],
                      metrics=[{"name": "credit", "expr": "sum(c.credit)"}]),
            )
        )
        assert [m["name"] for m in model["metrics"]] == ["total", "credit"]

    def test_description_is_carried_over(self):
        metric = self._metrics({"name": "m", "expr": "sum(o.amount)", "description": "Rev"})[0]
        assert metric["description"] == "Rev"

    def test_a_view_without_metrics_omits_the_key(self):
        assert "metrics" not in model_of(view(table()))


class TestRelationships:
    def _rel(self, **overrides):
        rel = {
            "name": "rel_oc",
            "left_table": "o",
            "right_table": "c",
            "relationship_columns": [{"left_column": "cid", "right_column": "cid"}],
            "relationship_type": "many_to_one",
        }
        rel.update(overrides)
        return view(
            table("o"),
            table("c", table_name="customers", columns=["cid"]),
            relationships=[rel],
        )

    def test_left_becomes_from_and_right_becomes_to(self):
        # Hologres' left is the many side holding the foreign key, which is Ossie's
        # `from`; right is the one side holding the primary key, which is `to`.
        rel = model_of(self._rel())["relationships"][0]
        assert (rel["from"], rel["to"]) == ("o", "c")
        assert (rel["from_columns"], rel["to_columns"]) == (["cid"], ["cid"])

    def test_many_to_one_is_not_stashed(self):
        # It is the only type Hologres defines and is already implied by from/to, so
        # recording it would pollute every clean star schema.
        rel = model_of(self._rel())["relationships"][0]
        assert "custom_extensions" not in rel

    def test_relationship_type_defaults_to_many_to_one(self):
        rel = self._rel()
        del rel["relationships"][0]["relationship_type"]
        assert model_of(rel)["relationships"][0]["from"] == "o"

    def test_unexpected_relationship_type_is_rejected(self):
        with pytest.raises(ConversionError, match="unsupported relationship_type"):
            import_view(self._rel(relationship_type="many_to_many"))

    def test_composite_relationship_columns_keep_their_pairing(self):
        rel = self._rel(
            relationship_columns=[
                {"left_column": "fk1", "right_column": "pk1"},
                {"left_column": "fk2", "right_column": "pk2"},
            ]
        )
        imported = model_of(rel)["relationships"][0]
        assert imported["from_columns"] == ["fk1", "fk2"]
        assert imported["to_columns"] == ["pk1", "pk2"]

    def test_reference_to_an_unknown_table_is_rejected(self):
        with pytest.raises(ConversionError, match="is not a table in this view"):
            import_view(self._rel(right_table="nope"))

    def test_empty_relationship_columns_is_rejected(self):
        with pytest.raises(ConversionError, match="'relationship_columns' must be a non-empty list"):
            import_view(self._rel(relationship_columns=[]))

    def test_a_view_without_relationships_omits_the_key(self):
        assert "relationships" not in model_of(view(table()))


class TestYamlEdgeCases:
    def test_a_dimension_named_like_a_yaml_boolean_survives(self):
        # A YAML 1.1 reader turns `name: no` into False, silently renaming the dimension.
        raw = (
            "name: sv\n"
            "tables:\n"
            "- name: o\n"
            "  base_table:\n"
            "    table: t\n"
            "  dimensions:\n"
            "  - name: no\n"
            "    expr: o.x\n"
        )
        imported = load_yaml(convert_semantic_view_to_ossie(raw))
        assert imported["datasets"][0]["fields"][0]["name"] == "no"

    def test_unicode_description_is_preserved(self):
        model = model_of(view(table(), description="销售分析语义视图"))
        assert model["description"] == "销售分析语义视图"
