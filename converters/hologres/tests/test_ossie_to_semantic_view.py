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

"""Tests for the Apache Ossie -> Hologres CREATE SEMANTIC VIEW export.

The two golden .sql fixtures were executed against a real Hologres 5.0.0 instance and
the resulting views queried, so they assert grammar that is known to work rather than
grammar that merely looks right.
"""

import warnings

import pytest
from _util import ossie_doc, read_fixture
from ossie_hologres import ConversionError, convert_ossie_to_semantic_view
from ossie_hologres._common import dump_yaml


def export(model, **kwargs):
    """Convert a single model dict, ignoring the fidelity warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_ossie_to_semantic_view(dump_yaml(ossie_doc(model)), **kwargs)


def warnings_from(model, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        convert_ossie_to_semantic_view(dump_yaml(ossie_doc(model)), **kwargs)
    return [str(w.message) for w in caught]


def ansi(expression):
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}


def dataset(name, source="db.public.t", primary_key=("id",), fields=(), **extra):
    ds = {"name": name, "source": source}
    if primary_key:
        ds["primary_key"] = list(primary_key)
    if fields:
        ds["fields"] = list(fields)
    ds.update(extra)
    return ds


def field(name, expression, **extra):
    return {"name": name, "expression": ansi(expression), **extra}


def metric(name, expression, **extra):
    return {"name": name, "expression": ansi(expression), **extra}


# A minimal single-table model used as the base for most focused tests.
def one_table(fields=(), metrics=(), **extra):
    model = {
        "name": "sv",
        "datasets": [dataset("o", fields=fields)],
    }
    if metrics:
        model["metrics"] = list(metrics)
    model.update(extra)
    return model


class TestGoldenFixtures:
    @pytest.mark.parametrize("name", ["fixtureA", "fixtureB"])
    def test_matches_the_instance_verified_ddl(self, name):
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # these fixtures must convert losslessly
            ddl = convert_ossie_to_semantic_view(read_fixture(f"{name}_ossie.yaml"))
        assert ddl == read_fixture(f"{name}_semantic_view.sql")

    def test_multi_table_clause_order_and_shape(self):
        ddl = read_fixture("fixtureB_semantic_view.sql")
        # Clauses must appear in the order the Hologres grammar defines. The view-level
        # COMMENT is located with rindex because dimensions carry COMMENTs of their own.
        positions = [
            ddl.index(kw)
            for kw in ("CREATE SEMANTIC VIEW", "TABLES", "RELATIONSHIPS", "DIMENSIONS", "METRICS")
        ]
        positions.append(ddl.rindex("COMMENT ="))
        assert positions == sorted(positions)
        assert ddl.endswith(";\n")

    def test_relationship_direction_is_many_to_one(self):
        # `from` is the many side and becomes the referencing table.
        assert "rel_oc AS o(customer_id) REFERENCES c(customer_id)" in read_fixture(
            "fixtureB_semantic_view.sql"
        )

    def test_metrics_are_namespaced_by_owning_table(self):
        ddl = read_fixture("fixtureB_semantic_view.sql")
        assert "o.total AS SUM(o.amount)" in ddl
        assert "c.credit AS SUM(c.credit_limit)" in ddl
        assert "i.item_qty AS SUM(i.quantity)" in ddl


class TestDocumentValidation:
    def test_non_mapping_root_is_rejected(self):
        with pytest.raises(ConversionError, match="expected a mapping"):
            convert_ossie_to_semantic_view("- a\n- b\n")

    def test_wrong_version_is_rejected(self):
        with pytest.raises(ConversionError, match="Unsupported Apache Ossie version"):
            convert_ossie_to_semantic_view("version: 0.1.0\n")

    def test_removed_wrapper_is_rejected(self):
        with pytest.raises(ConversionError, match="no longer accepted"):
            convert_ossie_to_semantic_view("version: 0.2.0.dev0\nsemantic_model: []\n")

    def test_model_without_datasets_is_rejected(self):
        with pytest.raises(ConversionError, match="has no datasets"):
            export({"name": "sv", "datasets": []})

    def test_duplicate_dataset_name_is_rejected(self):
        with pytest.raises(ConversionError, match="duplicate dataset name"):
            export({"name": "sv", "datasets": [dataset("o"), dataset("o")]})


class TestSourceParsing:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("retail.public.orders", "public.orders"),
            ("public.orders", "public.orders"),
            ("orders", "orders"),
        ],
    )
    def test_source_forms(self, source, expected):
        ddl = export(one_table(metrics=[metric("m", "COUNT(o.id)")]) | {
            "datasets": [dataset("o", source=source)]
        })
        assert f"o AS {expected} PRIMARY KEY (id)" in ddl

    def test_schema_option_supplies_a_default_for_unqualified_sources(self):
        ddl = export(
            {"name": "sv", "datasets": [dataset("o", source="orders")]}, schema="analytics"
        )
        assert "o AS analytics.orders" in ddl
        # It also qualifies the view itself.
        assert "CREATE SEMANTIC VIEW analytics.sv" in ddl

    def test_schema_option_never_overrides_an_explicit_source_schema(self):
        ddl = export(
            {"name": "sv", "datasets": [dataset("o", source="public.orders")]},
            schema="analytics",
        )
        assert "o AS public.orders" in ddl

    def test_datasets_spanning_databases_are_rejected(self):
        model = {
            "name": "sv",
            "datasets": [
                dataset("o", source="db1.public.orders"),
                dataset("c", source="db2.public.customers"),
            ],
        }
        with pytest.raises(ConversionError, match="cannot span multiple databases"):
            export(model)

    def test_database_option_must_agree_with_the_sources(self):
        with pytest.raises(ConversionError, match="does not match"):
            export({"name": "sv", "datasets": [dataset("o", source="db1.public.o")]},
                   database="db2")

    @pytest.mark.parametrize("source", ["", "   ", "a..b", "a.b.c.d", "a.b c.d"])
    def test_malformed_sources_are_rejected(self, source):
        with pytest.raises(ConversionError):
            export({"name": "sv", "datasets": [dataset("o", source=source)]})

    def test_whitespace_around_source_parts_is_tolerated(self):
        ddl = export({"name": "sv", "datasets": [dataset("o", source="db. public . orders")]})
        assert "o AS public.orders" in ddl

    def test_missing_source_is_rejected(self):
        with pytest.raises(ConversionError, match="source"):
            export({"name": "sv", "datasets": [{"name": "o", "primary_key": ["id"]}]})


class TestPrimaryKeys:
    def test_unreferenced_dataset_may_omit_its_primary_key(self):
        ddl = export({"name": "sv", "datasets": [dataset("o", primary_key=None)]})
        assert "o AS public.t" in ddl
        assert "PRIMARY KEY" not in ddl

    def test_referenced_dataset_without_any_key_is_rejected(self):
        model = {
            "name": "sv",
            "datasets": [dataset("o"), dataset("c", primary_key=None)],
            "relationships": [
                {"name": "r", "from": "o", "to": "c", "from_columns": ["cid"],
                 "to_columns": ["cid"]}
            ],
        }
        with pytest.raises(ConversionError, match="declares no 'primary_key'"):
            export(model)

    def test_matching_unique_key_is_promoted_with_a_warning(self):
        model = {
            "name": "sv",
            "datasets": [
                dataset("o"),
                dataset("c", primary_key=None, unique_keys=[["cid"]]),
            ],
            "relationships": [
                {"name": "r", "from": "o", "to": "c", "from_columns": ["cid"],
                 "to_columns": ["cid"]}
            ],
        }
        assert "c AS public.t PRIMARY KEY (cid)" in export(model)
        assert any("promoting unique_keys" in w for w in warnings_from(model))

    def test_composite_primary_key_is_emitted_in_order(self):
        ddl = export({"name": "sv", "datasets": [dataset("ss", primary_key=["item", "ticket"])]})
        assert "PRIMARY KEY (item, ticket)" in ddl


class TestRelationships:
    def _model(self, **rel_overrides):
        rel = {
            "name": "rel_oc",
            "from": "o",
            "to": "c",
            "from_columns": ["cid"],
            "to_columns": ["cid"],
        }
        rel.update(rel_overrides)
        return {
            "name": "sv",
            "datasets": [dataset("o"), dataset("c", primary_key=["cid"])],
            "relationships": [rel],
        }

    def test_simple_relationship(self):
        assert "rel_oc AS o(cid) REFERENCES c(cid)" in export(self._model())

    def test_to_columns_must_be_the_target_primary_key(self):
        # Hologres rejects a REFERENCES target that is not the declared primary key, so
        # catching it offline gives a far better message than the server's.
        with pytest.raises(ConversionError, match="REFERENCES target to be the primary key"):
            export(self._model(to_columns=["other"]))

    def test_mismatched_column_counts_are_rejected(self):
        with pytest.raises(ConversionError, match="same number of columns"):
            export(self._model(from_columns=["a", "b"]))

    def test_empty_column_lists_are_rejected(self):
        with pytest.raises(ConversionError, match="must be non-empty"):
            export(self._model(from_columns=[], to_columns=[]))

    def test_unknown_dataset_reference_is_rejected(self):
        with pytest.raises(ConversionError, match="unknown dataset"):
            export(self._model(to="nope"))

    def test_composite_key_columns_are_reordered_to_primary_key_order(self):
        # Hologres pairs the two column lists positionally, so to_columns is emitted in
        # primary-key order and from_columns moves with it to keep the pairing intact.
        model = {
            "name": "sv",
            "datasets": [dataset("f"), dataset("d", primary_key=["pk1", "pk2"])],
            "relationships": [
                {
                    "name": "r",
                    "from": "f",
                    "to": "d",
                    "from_columns": ["fk2", "fk1"],
                    "to_columns": ["pk2", "pk1"],
                }
            ],
        }
        assert "r AS f(fk1, fk2) REFERENCES d(pk1, pk2)" in export(model)


class TestDimensions:
    def test_bare_column_is_qualified_with_the_owning_table(self):
        ddl = export(one_table(fields=[field("region_dim", "region")]))
        assert "o.region_dim AS o.region" in ddl

    def test_already_qualified_column_is_left_alone(self):
        ddl = export(one_table(fields=[field("region_dim", "o.region")]))
        assert "o.region_dim AS o.region" in ddl

    def test_multi_column_expression_is_fully_qualified(self):
        # The naive "only prefix a bare identifier" rule would emit unqualified columns
        # here, which Hologres cannot resolve. The parentheses are required too -- see
        # TestDefinitionParentheses.
        ddl = export(one_table(fields=[field("full_name", "first_name || ' ' || last_name")]))
        assert "o.full_name AS (o.first_name || ' ' || o.last_name)" in ddl

    def test_description_becomes_a_comment(self):
        ddl = export(one_table(fields=[field("d", "x", description="A dim")]))
        assert "o.d AS o.x COMMENT = 'A dim'" in ddl

    def test_apostrophe_in_a_comment_is_escaped(self):
        ddl = export(one_table(fields=[field("d", "x", description="customer's city")]))
        assert "COMMENT = 'customer''s city'" in ddl

    def test_string_ai_context_is_folded_into_the_comment(self):
        ddl = export(one_table(fields=[field("d", "x", description="A", ai_context="B")]))
        assert "COMMENT = 'A\nB'" in ddl

    def test_dimension_name_collision_across_datasets_is_rejected(self):
        # Ossie only requires field names to be unique per dataset, but Semantic View
        # queries reference dimensions by bare name, so the view-wide space must be flat.
        model = {
            "name": "sv",
            "datasets": [
                dataset("o", fields=[field("name", "n")]),
                dataset("c", fields=[field("name", "n")]),
            ],
        }
        with pytest.raises(ConversionError, match="defined by both dataset"):
            export(model)

    def test_aggregate_in_a_dimension_is_rejected(self):
        with pytest.raises(ConversionError, match="an aggregate function"):
            export(one_table(fields=[field("d", "sum(x)")]))

    def test_cross_table_dimension_is_rejected(self):
        model = {
            "name": "sv",
            "datasets": [dataset("o", fields=[field("d", "c.city")]), dataset("c")],
        }
        with pytest.raises(ConversionError, match="cannot span tables"):
            export(model)

    def test_missing_usable_dialect_is_rejected(self):
        model = one_table(
            fields=[{"name": "d", "expression": {"dialects": [{"dialect": "MDX", "expression": "[x]"}]}}]
        )
        with pytest.raises(ConversionError, match="no ANSI_SQL"):
            export(model)

    def test_non_ansi_dialects_are_ignored(self):
        model = one_table(
            fields=[
                {
                    "name": "d",
                    "expression": {
                        "dialects": [
                            {"dialect": "SNOWFLAKE", "expression": "x::varchar"},
                            {"dialect": "ANSI_SQL", "expression": "x"},
                        ]
                    },
                }
            ]
        )
        assert "o.d AS o.x" in export(model)

    def test_postgres_cast_shorthand_is_normalized_to_standard_cast(self):
        # Hologres accepts `x::text`, but the portable spelling is always available and
        # sqlglot rewrites to it, which is why no vendor dialect token is needed.
        assert "o.d AS CAST(o.x AS TEXT)" in export(one_table(fields=[field("d", "x::text")]))


class TestDefinitionParentheses:
    """The Hologres DDL grammar rejects a bare top-level operator in a definition.

    Verified against Hologres 5.0.0: `a || b`, `a + 1` and `a::text` are all syntax
    errors in a DIMENSIONS clause, while the parenthesised forms and function calls are
    accepted. The same operators are fine inside a function call's argument list.
    """

    def _dim(self, expr):
        return export(one_table(fields=[field("d", expr)]))

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("a || b", "(o.a || o.b)"),
            ("a + 1", "(o.a + 1)"),
            ("a - b", "(o.a - o.b)"),
            ("a > 1", "(o.a > 1)"),
        ],
    )
    def test_top_level_operators_are_parenthesised(self, expr, expected):
        assert f"o.d AS {expected}" in self._dim(expr)

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            # A plain column, a function call and a CASE are all accepted bare, so they
            # are left unwrapped to keep the DDL readable.
            ("region", "o.region"),
            ("upper(region)", "UPPER(o.region)"),
            ("concat(a, b)", "CONCAT(o.a, o.b)"),
            ("CASE WHEN a > 1 THEN 'x' ELSE 'y' END", "CASE WHEN o.a > 1 THEN 'x' ELSE 'y' END"),
            # sqlglot rewrites the PostgreSQL cast shorthand into CAST(...), which is a
            # function call and therefore already acceptable bare.
            ("a::text", "CAST(o.a AS TEXT)"),
        ],
    )
    def test_forms_accepted_bare_are_not_wrapped(self, expr, expected):
        assert f"o.d AS {expected}" in self._dim(expr)

    def test_an_operator_inside_a_function_call_needs_no_extra_wrapping(self):
        assert "o.d AS UPPER(o.a || o.b)" in self._dim("upper(a || b)")

    def test_metrics_are_function_calls_and_stay_unwrapped(self):
        ddl = export(one_table(metrics=[metric("m", "SUM(o.a + o.b)")]))
        assert "o.m AS SUM(o.a + o.b)" in ddl


class TestMetrics:
    def test_owner_is_inferred_from_the_qualified_column(self):
        ddl = export(one_table(metrics=[metric("total", "SUM(o.amount)")]))
        assert "o.total AS SUM(o.amount)" in ddl

    def test_unqualified_columns_are_qualified_with_the_stashed_owner(self):
        m = metric("total", "SUM(amount)")
        m["custom_extensions"] = [{"vendor_name": "HOLOGRES", "data": '{"owner": "o"}'}]
        assert "o.total AS SUM(o.amount)" in export(one_table(metrics=[m]))

    def test_count_star_owner_comes_from_the_stash(self):
        m = metric("n", "COUNT(*)")
        m["custom_extensions"] = [{"vendor_name": "HOLOGRES", "data": '{"owner": "o"}'}]
        assert "o.n AS COUNT(*)" in export(one_table(metrics=[m]))

    def test_count_star_owner_can_come_from_the_option(self):
        ddl = export(one_table(metrics=[metric("n", "COUNT(*)")]), metric_owners={"n": "o"})
        assert "o.n AS COUNT(*)" in ddl

    def test_count_star_without_an_owner_is_rejected_with_guidance(self):
        # Guessing the owner would silently change the number under a fan-out join, so
        # this fails closed and tells the user both ways to fix it.
        with pytest.raises(ConversionError, match="--metric-owner"):
            export(one_table(metrics=[metric("n", "COUNT(*)")]))

    def test_declared_owner_contradicting_the_expression_is_rejected(self):
        m = metric("total", "SUM(o.amount)")
        m["custom_extensions"] = [{"vendor_name": "HOLOGRES", "data": '{"owner": "c"}'}]
        model = {
            "name": "sv",
            "datasets": [dataset("o"), dataset("c")],
            "metrics": [m],
        }
        with pytest.raises(ConversionError, match="contradicts the expression"):
            export(model)

    def test_owner_must_be_a_real_dataset(self):
        with pytest.raises(ConversionError, match="not a dataset in this model"):
            export(one_table(metrics=[metric("n", "COUNT(*)")]), metric_owners={"n": "nope"})

    def test_cross_table_metric_is_rejected(self):
        model = {
            "name": "sv",
            "datasets": [dataset("o"), dataset("c")],
            "metrics": [metric("m", "SUM(o.amount + c.credit)")],
        }
        with pytest.raises(ConversionError, match="Hologres metrics must aggregate over a single table"):
            export(model)

    def test_ratio_metric_is_rejected(self):
        model = {
            "name": "sv",
            "datasets": [dataset("o")],
            "metrics": [metric("m", "SUM(o.amount) / COUNT(*)")],
        }
        with pytest.raises(ConversionError, match="count/sum/avg/min/max"):
            export(model)

    def test_unsupported_metrics_can_be_skipped(self):
        model = {
            "name": "sv",
            "datasets": [dataset("o")],
            "metrics": [
                metric("ratio", "SUM(o.amount) / COUNT(*)"),
                metric("total", "SUM(o.amount)"),
            ],
        }
        ddl = export(model, skip_unsupported_metrics=True)
        assert "o.total AS SUM(o.amount)" in ddl
        assert "ratio" not in ddl
        assert any("skipped" in w for w in warnings_from(model, skip_unsupported_metrics=True))

    def test_metric_description_becomes_a_comment(self):
        ddl = export(one_table(metrics=[metric("total", "SUM(o.amount)", description="Rev")]))
        assert "o.total AS SUM(o.amount) COMMENT = 'Rev'" in ddl


class TestDropIfExists:
    def test_drop_is_prefixed_when_requested(self):
        ddl = export({"name": "sv", "datasets": [dataset("o")]}, drop_if_exists=True)
        assert ddl.startswith("DROP SEMANTIC VIEW IF EXISTS sv;\nCREATE SEMANTIC VIEW sv")

    def test_drop_is_schema_qualified_too(self):
        ddl = export(
            {"name": "sv", "datasets": [dataset("o")]}, schema="analytics", drop_if_exists=True
        )
        assert ddl.startswith("DROP SEMANTIC VIEW IF EXISTS analytics.sv;")

    def test_no_drop_by_default(self):
        assert "DROP" not in export({"name": "sv", "datasets": [dataset("o")]})


class TestQuoting:
    def test_reserved_words_are_quoted(self):
        # sqlglot does not quote these, so the DDL writer must.
        model = {
            "name": "select",
            "datasets": [dataset("order", source="public.table",
                                 fields=[field("group", "user")])],
        }
        ddl = export(model)
        assert 'CREATE SEMANTIC VIEW "select"' in ddl
        assert '"order" AS public."table"' in ddl
        assert '"order"."group" AS "order"."user"' in ddl

    def test_uppercase_identifiers_are_quoted_to_survive_folding(self):
        ddl = export({"name": "SV", "datasets": [dataset("O", source="public.Orders")]})
        assert 'CREATE SEMANTIC VIEW "SV"' in ddl
        assert '"O" AS public."Orders"' in ddl


class TestFidelityWarnings:
    @pytest.mark.parametrize(
        ("model", "fragment"),
        [
            (one_table(fields=[field("d", "x", datatype="String")]), "datatype dropped"),
            (one_table(fields=[field("d", "x", label="L")]), "label dropped"),
            (
                one_table(fields=[field("d", "x", dimension={"is_time": True})]),
                "dimension.is_time",
            ),
            (
                one_table(fields=[field("d", "x", ai_context={"synonyms": ["a"]})]),
                "ai_context (object) dropped",
            ),
            (one_table(ai_context={"instructions": "hi"}), "model-level ai_context"),
            (
                {"name": "sv", "datasets": [dataset("o", description="A table")]},
                "dataset description dropped",
            ),
            (
                {
                    "name": "sv",
                    "datasets": [dataset("o", unique_keys=[["a"], ["b"]])],
                },
                "unique_keys",
            ),
            (
                one_table(custom_extensions=[{"vendor_name": "DBT", "data": "{}"}]),
                "foreign-vendor custom_extensions dropped",
            ),
        ],
    )
    def test_dropped_metadata_is_reported(self, model, fragment):
        assert any(fragment in w for w in warnings_from(model)), warnings_from(model)

    def test_relationship_ai_context_is_reported(self):
        model = {
            "name": "sv",
            "datasets": [dataset("o"), dataset("c", primary_key=["cid"])],
            "relationships": [
                {
                    "name": "r",
                    "from": "o",
                    "to": "c",
                    "from_columns": ["cid"],
                    "to_columns": ["cid"],
                    "ai_context": "joins them",
                }
            ],
        }
        assert any("relationship ai_context dropped" in w for w in warnings_from(model))

    def test_a_fully_expressible_model_warns_about_nothing(self):
        assert warnings_from(one_table(fields=[field("d", "x")],
                                       metrics=[metric("m", "SUM(o.y)")])) == []
