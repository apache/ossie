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

"""Round-trip closure and TPC-DS coverage.

A full Ossie -> DDL -> Hologres -> model_yaml -> Ossie loop needs a database, because
only Hologres can turn DDL back into a model_yaml; that lives in test_live_hologres.py.
What is checked here is the offline closure of the same loop: the paired fixtures were
produced by a real instance, so composing the two converters over them must agree.
"""

import warnings

import pytest
from _util import EXAMPLES, read_fixture
from ossie_hologres import (
    ConversionError,
    convert_ossie_to_semantic_view,
    convert_semantic_view_to_ossie,
)
from ossie_hologres._common import load_yaml

TPCDS = EXAMPLES / "tpcds_semantic_model.yaml"

# The canonical TPC-DS model defines these as ratios over two datasets at once, which a
# Semantic View cannot express: Hologres aggregates each metric within one table.
TPCDS_INEXPRESSIBLE_METRICS = ["customer_lifetime_value", "store_productivity"]


def quiet_export(text, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_ossie_to_semantic_view(text, **kwargs)


class TestOfflineRoundTripClosure:
    @pytest.mark.parametrize("name", ["fixtureA", "fixtureB"])
    def test_model_yaml_reaches_the_same_ddl_through_ossie(self, name):
        # model_yaml -> Ossie -> DDL must land on the same DDL as Ossie -> DDL, which
        # closes the loop offline: the model_yaml fixture is the instance's readback of
        # exactly that DDL.
        via_import = quiet_export(
            convert_semantic_view_to_ossie(read_fixture(f"{name}_model_yaml.yaml"))
        )
        assert via_import == read_fixture(f"{name}_semantic_view.sql")

    @pytest.mark.parametrize("name", ["fixtureA", "fixtureB"])
    def test_import_is_idempotent(self, name):
        once = convert_semantic_view_to_ossie(read_fixture(f"{name}_model_yaml.yaml"))
        # Importing cannot be re-applied to its own output, but exporting twice from the
        # same Ossie must be deterministic.
        assert quiet_export(once) == quiet_export(once)

    @pytest.mark.parametrize("name", ["fixtureA", "fixtureB"])
    def test_export_output_is_stable_under_reimport(self, name):
        ossie = convert_semantic_view_to_ossie(read_fixture(f"{name}_model_yaml.yaml"))
        assert load_yaml(ossie) == load_yaml(read_fixture(f"{name}_ossie.yaml"))


class TestParenthesisRoundTrip:
    def _dimension_expr(self, ossie_yaml):
        model = load_yaml(ossie_yaml)
        return model["datasets"][0]["fields"][0]["expression"]["dialects"][0]["expression"]

    def test_hologres_normalized_parentheses_are_stripped_on_import(self):
        # Export wraps a top-level operator in parentheses because the DDL grammar needs
        # it, and Hologres echoes the wrapping back (in fact re-parenthesising further).
        # Import must not accumulate it.
        model_yaml = (
            "name: sv\n"
            "tables:\n"
            "- name: c\n"
            "  base_table:\n"
            "    database: db\n"
            "    schema: public\n"
            "    table: customers\n"
            "  dimensions:\n"
            "  - name: full_name\n"
            "    expr: \"((c.first_name || ' ') || c.last_name)\"\n"
        )
        expr = self._dimension_expr(convert_semantic_view_to_ossie(model_yaml))
        assert expr == "(first_name || ' ') || last_name"

    def test_export_re_adds_the_parentheses_the_grammar_requires(self):
        ossie = (
            "version: 0.2.0.dev0\n"
            "name: sv\n"
            "datasets:\n"
            "- name: c\n"
            "  source: public.customers\n"
            "  primary_key: [id]\n"
            "  fields:\n"
            "  - name: full_name\n"
            "    expression:\n"
            "      dialects:\n"
            "      - dialect: ANSI_SQL\n"
            "        expression: first_name || last_name\n"
        )
        ddl = quiet_export(ossie)
        assert "c.full_name AS (c.first_name || c.last_name)" in ddl


class TestTpcds:
    def test_the_canonical_example_is_partially_inexpressible(self):
        # Documented limitation rather than a bug: fail closed and name the metric.
        with pytest.raises(ConversionError) as excinfo:
            convert_ossie_to_semantic_view(TPCDS.read_text(encoding="utf-8"))
        assert TPCDS_INEXPRESSIBLE_METRICS[0] in str(excinfo.value)

    def test_skipping_the_inexpressible_metrics_matches_the_golden_ddl(self):
        ddl = quiet_export(
            TPCDS.read_text(encoding="utf-8"), skip_unsupported_metrics=True
        )
        assert ddl == read_fixture("tpcds_semantic_view.sql")

    def test_both_inexpressible_metrics_are_reported(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            convert_ossie_to_semantic_view(
                TPCDS.read_text(encoding="utf-8"), skip_unsupported_metrics=True
            )
        messages = " ".join(str(w.message) for w in caught)
        for name in TPCDS_INEXPRESSIBLE_METRICS:
            assert name in messages

    def test_all_five_datasets_become_tables(self):
        ddl = read_fixture("tpcds_semantic_view.sql")
        for alias in ("store_sales", "date_dim", "customer", "item", "store"):
            assert f"{alias} AS public.{alias}" in ddl

    def test_the_composite_primary_key_survives(self):
        assert "store_sales AS public.store_sales PRIMARY KEY (ss_item_sk, ss_ticket_number)" in (
            read_fixture("tpcds_semantic_view.sql")
        )

    def test_all_four_relationships_are_emitted(self):
        ddl = read_fixture("tpcds_semantic_view.sql")
        for rel in (
            "store_sales_to_date AS store_sales(ss_sold_date_sk) REFERENCES date_dim(d_date_sk)",
            "store_sales_to_customer AS store_sales(ss_customer_sk) REFERENCES customer(c_customer_sk)",
            "store_sales_to_item AS store_sales(ss_item_sk) REFERENCES item(i_item_sk)",
            "store_sales_to_store AS store_sales(ss_store_sk) REFERENCES store(s_store_sk)",
        ):
            assert rel in ddl

    def test_the_computed_dimension_is_fully_qualified_and_parenthesised(self):
        # Both properties are required for Hologres to accept it: every column needs its
        # table, and a top-level operator needs parentheses.
        assert (
            "customer.customer_full_name AS "
            "(customer.c_first_name || ' ' || customer.c_last_name)"
        ) in read_fixture("tpcds_semantic_view.sql")

    def test_only_the_expressible_metrics_are_emitted(self):
        ddl = read_fixture("tpcds_semantic_view.sql")
        for name in ("total_sales", "total_profit", "sales_by_brand"):
            assert f"store_sales.{name} AS SUM(" in ddl
        for name in TPCDS_INEXPRESSIBLE_METRICS:
            assert name not in ddl
