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

"""Tests for the Snowflake to Ossie YAML converter."""

import json
import warnings
from pathlib import Path

import pytest
import yaml

from ossie_snowflake._common import is_portable_sql, read_stash
from ossie_snowflake.ossie_to_snowflake import (
    OssieConversionError,
    convert_ossie_to_snowflake,
)
from ossie_snowflake.snowflake_to_ossie import (
    SnowflakeConversionError,
    convert_snowflake_to_ossie,
    _ai_context,
    _convert_field,
    _convert_named_expr,
    _convert_relationship,
    _convert_table,
    _duplicate_names,
    _parse_base_table,
    _qualify_columns,
    _warn_dropped,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_table(table="TBL"):
    return {"database": "DB", "schema": "SCH", "table": table}


def _minimal_snowflake(**overrides):
    """Returns a minimal convertible Snowflake Cortex Analyst model dict."""
    model = {
        "name": "test_model",
        "tables": [
            {
                "name": "my_table",
                "base_table": _base_table(),
                "dimensions": [
                    {"name": "col1", "expr": "COL1", "data_type": "VARCHAR"}
                ],
            }
        ],
    }
    model.update(overrides)
    return model


def _snowflake_table(**overrides):
    table = {"name": "t", "base_table": _base_table()}
    table.update(overrides)
    return table


def _ossie_dataset(**overrides):
    dataset = {"name": "d", "source": "a.b.c"}
    dataset.update(overrides)
    return dataset


def _convert(model):
    """Converts a Snowflake model dict and returns the Ossie document dict."""
    return yaml.safe_load(convert_snowflake_to_ossie(yaml.dump(model)))


def _silently(fn, *args, **kwargs):
    """Runs `fn` with warnings suppressed, for cases that assert on output only."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# convert_snowflake_to_ossie
# ---------------------------------------------------------------------------

class TestConvertSnowflakeToOssie:
    @pytest.mark.parametrize("source", ["[]", "- a\n- b", "just a string", ""])
    def test_non_mapping_root_is_rejected(self, source):
        with pytest.raises(SnowflakeConversionError, match="expected a mapping"):
            convert_snowflake_to_ossie(source)

    def test_malformed_yaml_is_reported_as_a_conversion_error(self):
        # Not a traceback: the CLI turns this into `Error: ...` and exit status 1.
        with pytest.raises(SnowflakeConversionError, match="Invalid Snowflake YAML"):
            convert_snowflake_to_ossie("name: x\n  bad: [unclosed\n")

    def test_version_is_supplied_by_the_converter(self):
        # Snowflake carries no Ossie document version, so the converter stamps one.
        assert _convert(_minimal_snowflake())["version"] == "0.2.0.dev0"

    def test_name_is_required(self):
        model = _minimal_snowflake()
        del model["name"]
        with pytest.raises(SnowflakeConversionError, match="Missing required 'name'"):
            convert_snowflake_to_ossie(yaml.dump(model))

    def test_a_model_without_tables_is_rejected(self):
        with pytest.raises(SnowflakeConversionError, match="no 'tables'"):
            convert_snowflake_to_ossie(yaml.dump({"name": "m", "tables": []}))

    def test_description_and_custom_instructions_are_carried(self):
        ossie = _convert(
            _minimal_snowflake(
                description="sales", custom_instructions="prefer net amounts"
            )
        )
        assert ossie["description"] == "sales"
        assert ossie["ai_context"]["instructions"] == "prefer net amounts"

    def test_key_order_is_stable(self):
        # sort_keys=False, so the document reads top-down as the spec presents it.
        keys = list(_convert(_minimal_snowflake(description="d")))
        assert keys[:3] == ["version", "name", "description"]

    def test_non_ascii_text_is_not_escaped(self):
        out = convert_snowflake_to_ossie(
            yaml.dump(_minimal_snowflake(description="売上"), allow_unicode=True)
        )
        assert "売上" in out


# ---------------------------------------------------------------------------
# malformed structure -- a bad model is a conversion error, never a traceback
# ---------------------------------------------------------------------------

class TestMalformedStructure:
    # Every collection on both sides is a list of mappings. A scalar or a mapping
    # written where a list belongs used to reach `.get` and surface as an
    # `AttributeError`, which reads as a converter crash rather than as a
    # statement about the input.

    @pytest.mark.parametrize(
        "model, expected",
        [
            ({"name": "m", "tables": ["t"]}, "'tables' must contain mappings"),
            ({"name": "m", "tables": 1}, "'tables' must be a list"),
            ({"name": "m", "tables": {"a": 1}}, "'tables' must be a list"),
            (
                {"name": "m", "tables": [_snowflake_table(dimensions=["d"])]},
                "Table 't': 'dimensions' must contain mappings",
            ),
            (
                {"name": "m", "tables": [_snowflake_table(time_dimensions=1)]},
                "Table 't': 'time_dimensions' must be a list",
            ),
            (
                {"name": "m", "tables": [_snowflake_table(facts={"a": 1})]},
                "Table 't': 'facts' must be a list",
            ),
            (
                {"name": "m", "tables": [_snowflake_table(metrics=["m"])]},
                "Table 't': 'metrics' must contain mappings",
            ),
            (
                {"name": "m", "tables": [_snowflake_table(unique_keys=1)]},
                "Table 't': 'unique_keys' must be a list",
            ),
            (
                {"name": "m", "tables": [_snowflake_table()], "relationships": ["r"]},
                "'relationships' must contain mappings",
            ),
            (
                {"name": "m", "tables": [_snowflake_table()], "metrics": ["m"]},
                "'metrics' must contain mappings",
            ),
        ],
    )
    def test_importing_a_malformed_collection_raises_a_conversion_error(
        self, model, expected
    ):
        with pytest.raises(SnowflakeConversionError, match=expected):
            convert_snowflake_to_ossie(yaml.dump(model))

    @pytest.mark.parametrize(
        "model, expected",
        [
            ({"name": "m", "datasets": ["d"]}, "'datasets' must contain mappings"),
            ({"name": "m", "datasets": 1}, "'datasets' must be a list"),
            (
                {"name": "m", "datasets": [_ossie_dataset(fields=["f"])]},
                "Dataset 'd': 'fields' must contain mappings",
            ),
            (
                {"name": "m", "datasets": [_ossie_dataset()], "relationships": ["r"]},
                "'relationships' must contain mappings",
            ),
            (
                {"name": "m", "datasets": [_ossie_dataset()], "metrics": 1},
                "'metrics' must be a list",
            ),
            (
                {"name": "m", "datasets": [_ossie_dataset(unique_keys=1)]},
                "Dataset 'd': 'unique_keys' must be a list",
            ),
        ],
    )
    def test_exporting_a_malformed_collection_raises_a_conversion_error(
        self, model, expected
    ):
        model["version"] = "0.2.0.dev0"
        with pytest.raises(OssieConversionError, match=expected):
            convert_ossie_to_snowflake(yaml.dump(model))


# ---------------------------------------------------------------------------
# _parse_base_table
# ---------------------------------------------------------------------------

class TestParseBaseTable:
    def test_a_triple_becomes_a_dotted_source(self):
        assert _parse_base_table(_base_table("FCT"), "t") == "DB.SCH.FCT"

    def test_surrounding_whitespace_is_trimmed(self):
        base = {"database": " DB ", "schema": "SCH", "table": "T\n"}
        assert _parse_base_table(base, "t") == "DB.SCH.T"

    def test_a_definition_subquery_is_carried_through(self):
        base = {"definition": "  SELECT 1 AS a  "}
        assert _parse_base_table(base, "t") == "SELECT 1 AS a"

    def test_a_definition_wins_over_a_partial_triple(self):
        base = {"definition": "SELECT 1", "database": "DB"}
        assert _parse_base_table(base, "t") == "SELECT 1"

    @pytest.mark.parametrize("base", [None, {}, "DB.SCH.T", []])
    def test_a_missing_base_table_is_an_error(self, base):
        # Ossie requires `source`, so this cannot be a silent omission.
        with pytest.raises(SnowflakeConversionError, match="requires a source"):
            _parse_base_table(base, "t")

    @pytest.mark.parametrize(
        "base",
        [
            {"database": "DB"},
            {"database": "DB", "schema": "SCH"},
            {"schema": "SCH", "table": "T"},
        ],
    )
    def test_an_incomplete_triple_is_an_error(self, base):
        with pytest.raises(SnowflakeConversionError, match="must give database"):
            _parse_base_table(base, "t")


# ---------------------------------------------------------------------------
# _convert_named_expr -- dialect labelling
# ---------------------------------------------------------------------------

class TestConvertNamedExpr:
    @pytest.mark.parametrize(
        "expr",
        [
            "AMOUNT",
            "a.b",
            '"Quoted Col"',
            "SUM(amount)",
            "COUNT(*)",
            "COALESCE(a, 0)",
            "CASE WHEN a > 0 THEN b END",
            "CAST(a AS INT)",
            "a || b",
            "a IS NOT NULL",
            "a IN (1, 2)",
            "ROW_NUMBER() OVER (PARTITION BY a ORDER BY b DESC)",
            "SUM(DISTINCT amount)",
            "EXTRACT(YEAR FROM d)",
            "CAST(x AS DECIMAL(10, 2))",
            "SUM(date)",
        ],
    )
    def test_portable_sql_is_labelled_ansi(self, expr):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = _convert_named_expr({"name": "f", "expr": expr}, "field")
        assert result["expression"]["dialects"] == [
            {"dialect": "ANSI_SQL", "expression": expr}
        ]

    @pytest.mark.parametrize(
        "expr",
        [
            "amount::NUMBER",
            "IFF(a, b, c)",
            "ARRAY_AGG(x)",
            "$1",
            "payload:name",
            "arr[0]",
            "f(x => 1)",
            "DATEDIFF(day, a, b)",
            "a ILIKE 'b%'",
            '"myfunc"(x)',
            # Type names called as functions are Snowflake builtins, not the
            # standard SQL their token kind would suggest: DATE(ts) and
            # TIMESTAMP(x) construct, CHAR(65) is CHR.
            "DATE(ts)",
            "CHAR(65)",
            "FLOAT(x)",
            "TIMESTAMP(x)",
        ],
    )
    def test_snowflake_only_sql_keeps_the_snowflake_label(self, expr):
        with pytest.warns(UserWarning, match="Snowflake-specific SQL"):
            result = _convert_named_expr({"name": "f", "expr": expr}, "field")
        assert result["expression"]["dialects"][0]["dialect"] == "SNOWFLAKE"

    def test_the_warning_says_what_a_snowflake_label_costs(self):
        # A SNOWFLAKE-only label does not merely lose fidelity: every exporter that
        # reads ANSI_SQL drops the field outright, so the warning has to say so.
        with pytest.warns(UserWarning, match="will drop this field"):
            _convert_named_expr({"name": "f", "expr": "x::INT"}, "field")

    @pytest.mark.parametrize("entry", [{}, {"name": ""}, {"expr": "X"}])
    def test_a_missing_name_is_an_error(self, entry):
        with pytest.raises(SnowflakeConversionError, match="Missing required 'name'"):
            _convert_named_expr(entry, "field")

    @pytest.mark.parametrize("expr", [None, ""])
    def test_a_missing_expr_is_an_error(self, expr):
        with pytest.raises(SnowflakeConversionError, match="Missing required 'expr'"):
            _convert_named_expr({"name": "f", "expr": expr}, "field")

    def test_the_kind_appears_in_the_error(self):
        with pytest.raises(SnowflakeConversionError, match="in metric 'm'"):
            _convert_named_expr({"name": "m"}, "metric")

    def test_synonyms_become_ai_context(self):
        result = _silently(
            _convert_named_expr,
            {"name": "f", "expr": "F", "synonyms": ["alias", "別名"]},
            "field",
        )
        assert result["ai_context"]["synonyms"] == ["alias", "別名"]

    def test_description_is_carried(self):
        result = _convert_named_expr(
            {"name": "f", "expr": "F", "description": "d"}, "field"
        )
        assert result["description"] == "d"


# ---------------------------------------------------------------------------
# _convert_field -- the dimension block
# ---------------------------------------------------------------------------

class TestConvertField:
    def test_a_fact_carries_no_dimension_block(self):
        result = _convert_field({"name": "f", "expr": "F", "data_type": "NUMBER"}, None)
        assert "dimension" not in result

    def test_a_dimension_whose_datatype_already_implies_its_role_stays_bare(self):
        # String defaults to a non-time dimension, so `is_time: false` is redundant.
        result = _convert_field(
            {"name": "f", "expr": "F", "data_type": "VARCHAR"}, is_time=False
        )
        assert result["dimension"] == {}

    def test_a_time_dimension_with_a_time_datatype_stays_bare(self):
        result = _convert_field(
            {"name": "d", "expr": "D", "data_type": "DATE"}, is_time=True
        )
        assert result["dimension"] == {}

    def test_is_time_is_written_only_when_it_contradicts_the_datatype(self):
        # A DATE listed under `dimensions` really is meant as a non-time dimension.
        result = _convert_field(
            {"name": "d", "expr": "D", "data_type": "DATE"}, is_time=False
        )
        assert result["dimension"] == {"is_time": False}

    def test_a_string_time_dimension_keeps_its_explicit_flag(self):
        result = _convert_field(
            {"name": "d", "expr": "D", "data_type": "VARCHAR"}, is_time=True
        )
        assert result["dimension"] == {"is_time": True}


# ---------------------------------------------------------------------------
# _convert_table
# ---------------------------------------------------------------------------

class TestConvertTable:
    def test_name_is_required(self):
        with pytest.raises(SnowflakeConversionError, match="Missing required 'name'"):
            _convert_table({"base_table": _base_table()})

    def test_fields_keep_dimension_then_time_then_fact_order(self):
        dataset, _ = _silently(
            _convert_table,
            {
                "name": "t",
                "base_table": _base_table(),
                "facts": [{"name": "amount", "expr": "AMOUNT"}],
                "time_dimensions": [{"name": "at", "expr": "AT", "data_type": "DATE"}],
                "dimensions": [{"name": "region", "expr": "REGION"}],
            },
        )
        assert [f["name"] for f in dataset["fields"]] == ["region", "at", "amount"]

    def test_primary_key_columns_are_carried(self):
        dataset, _ = _convert_table(
            {
                "name": "t",
                "base_table": _base_table(),
                "primary_key": {"columns": ["id"]},
            }
        )
        assert dataset["primary_key"] == ["id"]

    def test_a_unique_column_becomes_a_single_column_unique_key(self):
        # Snowflake marks uniqueness on the column; Ossie records it on the dataset.
        dataset, _ = _convert_table(
            {
                "name": "t",
                "base_table": _base_table(),
                "dimensions": [{"name": "email", "expr": "EMAIL", "unique": True}],
            }
        )
        assert dataset["unique_keys"] == [["email"]]

    def test_a_declared_unique_key_is_not_duplicated_by_the_column_flag(self):
        dataset, _ = _convert_table(
            {
                "name": "t",
                "base_table": _base_table(),
                "unique_keys": [{"columns": ["email"]}],
                "dimensions": [{"name": "email", "expr": "EMAIL", "unique": True}],
            }
        )
        assert dataset["unique_keys"] == [["email"]]

    def test_table_metrics_are_handed_back_for_the_model_to_lift(self):
        # Ossie has model-level metrics only.
        dataset, metrics = _convert_table(
            {
                "name": "t",
                "base_table": _base_table(),
                "metrics": [{"name": "total", "expr": "SUM(amount)"}],
            }
        )
        assert "metrics" not in dataset
        assert [m["name"] for m in metrics] == ["total"]


# ---------------------------------------------------------------------------
# metrics lifted from tables
# ---------------------------------------------------------------------------

class TestLiftedMetrics:
    def test_a_table_metric_reaches_the_model(self):
        ossie = _convert(
            _minimal_snowflake(
                tables=[
                    {
                        "name": "t",
                        "base_table": _base_table(),
                        "metrics": [{"name": "total", "expr": "SUM(amount)"}],
                    }
                ]
            )
        )
        assert [m["name"] for m in ossie["metrics"]] == ["total"]

    def test_model_metrics_come_before_lifted_ones(self):
        ossie = _convert(
            _minimal_snowflake(
                metrics=[{"name": "model_level", "expr": "SUM(a)"}],
                tables=[
                    {
                        "name": "t",
                        "base_table": _base_table(),
                        "metrics": [{"name": "table_level", "expr": "SUM(b)"}],
                    }
                ],
            )
        )
        assert [m["name"] for m in ossie["metrics"]] == ["model_level", "table_level"]

    def test_a_collision_created_by_lifting_is_warned_about(self):
        # Two tables may each scope a metric named `total`; the model cannot.
        model = _minimal_snowflake(
            tables=[
                {
                    "name": f"t{i}",
                    "base_table": _base_table(f"T{i}"),
                    "metrics": [{"name": "total", "expr": "SUM(amount)"}],
                }
                for i in (1, 2)
            ]
        )
        with pytest.warns(UserWarning, match="not unique once table-level metrics"):
            convert_snowflake_to_ossie(yaml.dump(model))

    def test_lifting_qualifies_bare_columns_with_their_table(self):
        # Both tables have an `amount`, so `SUM(amount)` at the model level would
        # name neither of them.
        ossie = _convert(
            _minimal_snowflake(
                tables=[
                    {
                        "name": name,
                        "base_table": _base_table(name.upper()),
                        "metrics": [
                            {"name": f"{name}_total", "expr": "SUM(amount)"}
                        ],
                    }
                    for name in ("orders", "refunds")
                ]
            )
        )
        assert [
            m["expression"]["dialects"][0]["expression"] for m in ossie["metrics"]
        ] == ["SUM(orders.amount)", "SUM(refunds.amount)"]

    def test_lifting_leaves_an_already_qualified_column_alone(self):
        ossie = _convert(
            _minimal_snowflake(
                tables=[
                    {
                        "name": "orders",
                        "base_table": _base_table(),
                        "metrics": [
                            {"name": "total", "expr": "SUM(orders.amount)"}
                        ],
                    }
                ]
            )
        )
        assert (
            ossie["metrics"][0]["expression"]["dialects"][0]["expression"]
            == "SUM(orders.amount)"
        )

    def test_a_model_level_metric_is_not_qualified(self):
        # It is already written against the model, so there is no table to add.
        ossie = _convert(
            _minimal_snowflake(metrics=[{"name": "total", "expr": "SUM(a)"}])
        )
        assert (
            ossie["metrics"][0]["expression"]["dialects"][0]["expression"]
            == "SUM(a)"
        )

    def test_qualifying_leaves_the_rest_of_the_expression_verbatim(self):
        # The parse tree only locates the columns; the prefixes are spliced into
        # the original text. Re-rendering it instead would normalize the spacing
        # and rewrite Snowflake builtins such as DIV0 into their ANSI equivalent.
        expr = "DIV0( revenue , cost )"
        ossie = _silently(
            _convert,
            _minimal_snowflake(
                tables=[
                    {
                        "name": "orders",
                        "base_table": _base_table(),
                        "metrics": [{"name": "margin", "expr": expr}],
                    }
                ]
            ),
        )
        assert (
            ossie["metrics"][0]["expression"]["dialects"][0]["expression"]
            == "DIV0( orders.revenue , orders.cost )"
        )

    def test_a_date_part_is_not_mistaken_for_a_column(self):
        ossie = _silently(
            _convert,
            _minimal_snowflake(
                tables=[
                    {
                        "name": "orders",
                        "base_table": _base_table(),
                        "metrics": [
                            {"name": "span", "expr": "DATEDIFF(day, start_at, end_at)"}
                        ],
                    }
                ]
            ),
        )
        assert (
            ossie["metrics"][0]["expression"]["dialects"][0]["expression"]
            == "DATEDIFF(day, orders.start_at, orders.end_at)"
        )

    def test_an_unparseable_expression_is_kept_and_warned_about(self):
        model = _minimal_snowflake(
            tables=[
                {
                    "name": "orders",
                    "base_table": _base_table(),
                    "metrics": [{"name": "total", "expr": "SUM(amount"}],
                }
            ]
        )
        with pytest.warns(UserWarning, match="could not be located"):
            ossie = yaml.safe_load(convert_snowflake_to_ossie(yaml.dump(model)))
        assert (
            ossie["metrics"][0]["expression"]["dialects"][0]["expression"]
            == "SUM(amount"
        )


# ---------------------------------------------------------------------------
# _convert_relationship
# ---------------------------------------------------------------------------

class TestConvertRelationship:
    def _rel(self, **overrides):
        rel = {
            "name": "r",
            "left_table": "orders",
            "right_table": "customers",
            "relationship_columns": [
                {"left_column": "customer_id", "right_column": "id"}
            ],
        }
        rel.update(overrides)
        return rel

    def test_left_and_right_become_from_and_to(self):
        result = _convert_relationship(self._rel())
        assert result["from"] == "orders"
        assert result["to"] == "customers"
        assert result["from_columns"] == ["customer_id"]
        assert result["to_columns"] == ["id"]

    def test_a_composite_key_keeps_its_column_order(self):
        result = _convert_relationship(
            self._rel(
                relationship_columns=[
                    {"left_column": "a1", "right_column": "b1"},
                    {"left_column": "a2", "right_column": "b2"},
                ]
            )
        )
        assert result["from_columns"] == ["a1", "a2"]
        assert result["to_columns"] == ["b1", "b2"]

    @pytest.mark.parametrize("columns", [1, "a", {"left_column": "a"}])
    def test_a_scalar_where_the_join_columns_belong_is_an_error(self, columns):
        with pytest.raises(SnowflakeConversionError, match="must be a list"):
            _convert_relationship(self._rel(relationship_columns=columns))

    @pytest.mark.parametrize("key", ["join_type", "relationship_type"])
    def test_how_the_join_is_performed_is_reported_as_a_known_gap(self, key):
        # An Ossie relationship carries the join columns, not the join strategy, so
        # these are a mapping gap rather than a sign that the Snowflake model is
        # newer than this converter.
        with pytest.warns(UserWarning, match=rf"no Ossie counterpart\).*{key}"):
            _convert_relationship(self._rel(**{key: "one_to_one"}))

    @pytest.mark.parametrize("key", ["name", "left_table", "right_table"])
    def test_a_missing_endpoint_is_an_error(self, key):
        rel = self._rel()
        del rel[key]
        with pytest.raises(SnowflakeConversionError):
            _convert_relationship(rel)

    @pytest.mark.parametrize(
        "columns",
        [[], None, [{"left_column": "a"}], [{"right_column": "b"}], ["a"]],
    )
    def test_incomplete_join_columns_are_an_error(self, columns):
        with pytest.raises(SnowflakeConversionError, match="at least one"):
            _convert_relationship(self._rel(relationship_columns=columns))


# ---------------------------------------------------------------------------
# portability -- what may be labelled ANSI_SQL for the other spokes to read
# ---------------------------------------------------------------------------

class TestPortableSql:
    @pytest.mark.parametrize(
        "expr",
        [
            "LAG(x) IGNORE NULLS OVER (ORDER BY t)",
            "LAG(x) RESPECT NULLS OVER (ORDER BY t)",
            "NTH_VALUE(x, 2) FROM FIRST OVER (ORDER BY t)",
            "NTH_VALUE(x, 2) FROM LAST OVER (ORDER BY t)",
            "ts AT TIME ZONE 'UTC'",
        ],
    )
    def test_a_clause_no_spoke_can_be_expected_to_read_is_not_portable(self, expr):
        # These reach the tokenizer as ordinary names, so the per-token check waves
        # them through. Labelling them ANSI_SQL would hand every other spoke SQL it
        # cannot run.
        assert is_portable_sql(expr) is False

    @pytest.mark.parametrize(
        "expr",
        [
            "SUM(amount)",
            "SUM(amount) OVER (ORDER BY t ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)",
            "ROW_NUMBER() OVER (ORDER BY t NULLS LAST)",
            "COUNT(DISTINCT customer_id)",
        ],
    )
    def test_standard_sql_stays_portable(self, expr):
        # The guard above must not take standard window framing with it.
        assert is_portable_sql(expr) is True

    @pytest.mark.parametrize(
        "expr",
        [
            "EXTRACT(DAY FROM last)",
            "EXTRACT(YEAR FROM order_date)",
            "SUBSTRING(name FROM 1 FOR 3)",
            "TRIM(LEADING ' ' FROM name)",
        ],
    )
    def test_a_standard_from_inside_a_call_stays_portable(self, expr):
        # `FROM LAST` orders a window function's nulls and so follows the call it
        # orders. The `FROM` of `EXTRACT` sits inside one, and must not be mistaken
        # for it.
        assert is_portable_sql(expr) is True


# ---------------------------------------------------------------------------
# lifted metric qualification
# ---------------------------------------------------------------------------

class TestQualifyColumns:
    def test_a_quoted_column_keeps_its_quoting(self):
        # The offset is reported at the opening quote, so the prefix goes outside it.
        assert _qualify_columns('SUM("Amount")', "orders", "m") == (
            'SUM(orders."Amount")'
        )

    def test_an_ordinary_dataset_name_is_not_quoted(self):
        assert _qualify_columns("SUM(amount)", "orders", "m") == "SUM(orders.amount)"

    def test_a_dataset_name_that_needs_quoting_gets_it(self):
        # A bare `order items.amount` would not parse, and a bare `1st.amount` would
        # not resolve.
        assert _qualify_columns("SUM(amount)", "order items", "m") == (
            'SUM("order items".amount)'
        )

    def test_every_column_in_the_expression_is_qualified(self):
        assert _qualify_columns("SUM(amount) - SUM(refund)", "orders", "m") == (
            "SUM(orders.amount) - SUM(orders.refund)"
        )

    def test_an_already_qualified_column_is_left_alone(self):
        assert _qualify_columns("SUM(orders.amount)", "orders", "m") == (
            "SUM(orders.amount)"
        )


# ---------------------------------------------------------------------------
# custom_extensions stash and dropped properties
# ---------------------------------------------------------------------------

class TestStashAndDrop:
    def test_allowlisted_field_properties_are_stashed(self):
        result = _convert_named_expr(
            {"name": "f", "expr": "F", "sample_values": ["a"], "is_enum": True},
            "field",
        )
        assert read_stash(result) == {"sample_values": ["a"], "is_enum": True}

    def test_an_opaque_datatype_records_the_concrete_snowflake_type(self):
        # The spec asks Opaque to carry what it stands in for, to stay reversible.
        result = _silently(
            _convert_named_expr, {"name": "f", "expr": "F", "data_type": "VARIANT"}, "field"
        )
        assert result["datatype"] == "Opaque"
        assert read_stash(result)["data_type"] == "VARIANT"

    def test_nothing_vendor_specific_means_no_custom_extensions(self):
        result = _convert_named_expr({"name": "f", "expr": "F"}, "field")
        assert "custom_extensions" not in result

    def test_the_stash_is_valid_json_with_a_version_marker(self):
        result = _convert_named_expr(
            {"name": "f", "expr": "F", "sample_values": ["a"]}, "field"
        )
        blob = result["custom_extensions"][0]
        assert blob["vendor_name"] == "SNOWFLAKE"
        assert json.loads(blob["data"])["_v"] == 1

    def test_a_stash_that_is_not_an_object_is_rejected(self):
        # The blob is free text on the way in, so anything but an object would
        # reach the caller as a type it cannot index.
        field = {"custom_extensions": [{"vendor_name": "SNOWFLAKE", "data": "[1, 2]"}]}
        with pytest.raises(ValueError, match="must be a JSON object"):
            read_stash(field)

    def test_a_recognized_unmappable_property_is_reported_by_name(self):
        with pytest.warns(UserWarning, match=r"no Ossie counterpart\): tags"):
            _warn_dropped({"tags": ["x"]}, "field 'f'", (), (), ("tags",))

    def test_an_unrecognized_property_is_reported_separately(self):
        # This is the signal that the Snowflake model is newer than this mapping.
        with pytest.warns(UserWarning, match="not recognized by this converter"):
            _warn_dropped({"brand_new_key": 1}, "field 'f'", (), (), ())

    def test_a_mapped_property_is_not_reported(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _warn_dropped({"name": "f"}, "field 'f'", ("name",), (), ())


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_ai_context_is_none_when_there_is_nothing_to_say(self):
        assert _ai_context(instructions=None, synonyms=[]) is None

    def test_ai_context_carries_both_halves(self):
        assert _ai_context("do this", ["a"]) == {
            "instructions": "do this",
            "synonyms": ["a"],
        }

    @pytest.mark.parametrize("synonyms", ["not a list", 5, {}])
    def test_ai_context_ignores_synonyms_that_are_not_a_list(self, synonyms):
        assert _ai_context(None, synonyms) is None

    def test_duplicate_names_reports_only_repeats(self):
        entries = [{"name": "a"}, {"name": "b"}, {"name": "a"}]
        assert _duplicate_names(entries) == {"a"}


# ---------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------

def _rich_snowflake_model():
    return {
        "name": "retail",
        "description": "orders and customers",
        "tables": [
            {
                "name": "customers",
                "base_table": {
                    "database": "ANALYTICS",
                    "schema": "PROD",
                    "table": "DIM_CUSTOMER",
                },
                "primary_key": {"columns": ["customer_id"]},
                "dimensions": [
                    {"name": "customer_id", "expr": "CUSTOMER_ID", "data_type": "VARCHAR"},
                    {
                        "name": "region",
                        "expr": "REGION",
                        "data_type": "VARCHAR",
                        "synonyms": ["area"],
                        "description": "sales region",
                    },
                ],
            },
            {
                "name": "orders",
                "base_table": {
                    "database": "ANALYTICS",
                    "schema": "PROD",
                    "table": "FCT_ORDER",
                },
                "primary_key": {"columns": ["order_id"]},
                "time_dimensions": [
                    {"name": "ordered_at", "expr": "ORDERED_AT", "data_type": "TIMESTAMP_NTZ"}
                ],
                "dimensions": [
                    {"name": "order_id", "expr": "ORDER_ID", "data_type": "VARCHAR"},
                    {"name": "customer_id", "expr": "CUSTOMER_ID", "data_type": "VARCHAR"},
                ],
                "facts": [{"name": "amount", "expr": "AMOUNT", "data_type": "NUMBER"}],
            },
        ],
        "relationships": [
            {
                "name": "order_to_customer",
                "left_table": "orders",
                "right_table": "customers",
                "relationship_columns": [
                    {"left_column": "customer_id", "right_column": "customer_id"}
                ],
            }
        ],
        "metrics": [{"name": "net_sales", "expr": "SUM(orders.amount)"}],
    }


@pytest.fixture
def rich_ossie():
    return _silently(_convert, _rich_snowflake_model())


class TestRoundTrip:
    def test_a_model_survives_snowflake_to_ossie_and_back(self):
        original = _rich_snowflake_model()
        ossie_yaml = _silently(convert_snowflake_to_ossie, yaml.dump(original))
        back = yaml.safe_load(_silently(convert_ossie_to_snowflake, ossie_yaml))

        assert back["name"] == original["name"]
        assert [t["name"] for t in back["tables"]] == ["customers", "orders"]
        assert back["relationships"] == original["relationships"]
        assert [m["name"] for m in back["metrics"]] == ["net_sales"]

    def test_the_role_of_every_column_survives_the_round_trip(self):
        # The `is_time` rule exists for this: a model that relied on the datatype
        # default must not pick up a contradictory explicit flag on the way back.
        original = _rich_snowflake_model()
        ossie_yaml = _silently(convert_snowflake_to_ossie, yaml.dump(original))
        back = yaml.safe_load(_silently(convert_ossie_to_snowflake, ossie_yaml))

        orders = next(t for t in back["tables"] if t["name"] == "orders")
        assert [d["name"] for d in orders["time_dimensions"]] == ["ordered_at"]
        assert {d["name"] for d in orders["dimensions"]} == {"order_id", "customer_id"}
        assert [f["name"] for f in orders["facts"]] == ["amount"]

    def test_a_fact_with_a_time_datatype_comes_back_a_fact(self):
        # Classification follows where the column was declared, not its datatype: a
        # date-typed fact must not be re-sorted into time_dimensions on the way back
        # just because its type looks like a time.
        model = {
            "name": "m",
            "tables": [
                {
                    "name": "orders",
                    "base_table": _base_table(),
                    "time_dimensions": [
                        {"name": "ordered_at", "expr": "ORDERED_AT",
                         "data_type": "TIMESTAMP_NTZ"}
                    ],
                    "facts": [
                        {"name": "snapshot_date", "expr": "SNAPSHOT_DATE",
                         "data_type": "DATE"},
                        {"name": "elapsed", "expr": "ELAPSED",
                         "data_type": "TIMESTAMP_NTZ"},
                    ],
                }
            ],
        }
        ossie_yaml = _silently(convert_snowflake_to_ossie, yaml.dump(model))
        back = yaml.safe_load(_silently(convert_ossie_to_snowflake, ossie_yaml))

        table = back["tables"][0]
        assert [d["name"] for d in table["time_dimensions"]] == ["ordered_at"]
        assert [f["name"] for f in table["facts"]] == ["snapshot_date", "elapsed"]

    def test_the_round_trip_is_stable_on_a_second_pass(self):
        once = _silently(convert_snowflake_to_ossie, yaml.dump(_rich_snowflake_model()))
        twice = _silently(
            convert_snowflake_to_ossie, _silently(convert_ossie_to_snowflake, once)
        )
        assert yaml.safe_load(twice) == yaml.safe_load(once)

    def test_a_clean_model_imports_without_warnings(self, recwarn):
        convert_snowflake_to_ossie(yaml.dump(_rich_snowflake_model()))
        assert [str(w.message) for w in recwarn.list] == []


# ---------------------------------------------------------------------------
# interop -- the imported model has to be usable by the other spokes
# ---------------------------------------------------------------------------

class TestInterop:
    def test_the_output_validates_against_the_core_spec_schema(self, rich_ossie):
        jsonschema = pytest.importorskip("jsonschema")
        schema_path = REPO_ROOT / "core-spec" / "ossie-schema.json"
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.validate(rich_ossie, schema)

    def test_no_expression_is_labelled_snowflake_only(self, rich_ossie):
        # Every exporter reads its own dialect, then ANSI_SQL, then OSSIE_SQL_2026,
        # and drops anything labelled with none of them. A SNOWFLAKE-only label on
        # portable SQL would therefore delete the field from every other spoke.
        labels = set()
        for dataset in rich_ossie["datasets"]:
            for field in dataset.get("fields") or []:
                for dialect in field["expression"]["dialects"]:
                    labels.add(dialect["dialect"])
        for metric in rich_ossie.get("metrics") or []:
            for dialect in metric["expression"]["dialects"]:
                labels.add(dialect["dialect"])
        assert labels == {"ANSI_SQL"}

    def test_every_dataset_has_the_source_the_spec_requires(self, rich_ossie):
        assert all(d.get("source") for d in rich_ossie["datasets"])

    def test_relationship_endpoints_name_real_datasets(self, rich_ossie):
        names = {d["name"] for d in rich_ossie["datasets"]}
        for relationship in rich_ossie["relationships"]:
            assert relationship["from"] in names
            assert relationship["to"] in names
