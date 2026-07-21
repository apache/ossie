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

"""Tests for the Ossie to Snowflake YAML converter."""

import warnings

import pytest
import yaml

from ossie_snowflake.converter import (
    OsiConversionError,
    convert_osi_to_snowflake,
    _classify_field,
    _convert_dataset,
    _convert_named_expr,
    _convert_relationship,
    _extract_expression,
    _extract_synonyms,
    _normalize_identifier,
    _parse_source,
    _warn_dropped_fields,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap_osi(model_dict):
    """Wrap a model dict in the standard Ossie envelope."""
    return yaml.dump(
        {"version": "0.2.0.dev0", "semantic_model": [model_dict]},
        default_flow_style=False,
    )


def _minimal_model(**overrides):
    """Return a minimal valid Ossie model dict."""
    base = {
        "name": "test_model",
        "datasets": [
            {
                "name": "my_table",
                "source": "db.schema.tbl",
                "fields": [
                    {
                        "name": "col1",
                        "expression": {
                            "dialects": [
                                {"dialect": "ANSI_SQL", "expression": "col1"}
                            ]
                        },
                        "dimension": {"is_time": False},
                    }
                ],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _normalize_identifier
# ---------------------------------------------------------------------------

class TestNormalizeIdentifier:
    def test_unquoted_uppercased(self):
        assert _normalize_identifier("my_table") == "MY_TABLE"

    def test_quoted_preserved(self):
        assert _normalize_identifier('"My Table"') == '"My Table"'

    def test_whitespace_stripped(self):
        assert _normalize_identifier("  foo  ") == "FOO"

    def test_quoted_whitespace_stripped(self):
        assert _normalize_identifier('  "bar"  ') == '"bar"'


# ---------------------------------------------------------------------------
# _parse_source
# ---------------------------------------------------------------------------

class TestParseSource:
    def test_three_part_name(self):
        result = _parse_source("db.schema.table")
        assert result == {"database": "DB", "schema": "SCHEMA", "table": "TABLE"}

    def test_quoted_identifiers_preserved(self):
        result = _parse_source('"myDb"."mySchema"."myTable"')
        assert result == {
            "database": '"myDb"',
            "schema": '"mySchema"',
            "table": '"myTable"',
        }

    def test_quoted_identifiers_with_dots_preserved(self):
        result = _parse_source('"my.db"."my schema"."my table"')
        assert result == {
            "database": '"my.db"',
            "schema": '"my schema"',
            "table": '"my table"',
        }

    def test_subquery_select(self):
        result = _parse_source("SELECT * FROM foo")
        assert result == {"definition": "SELECT * FROM foo"}

    def test_subquery_with(self):
        result = _parse_source("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert result == {
            "definition": "WITH cte AS (SELECT 1) SELECT * FROM cte"
        }

    def test_none_returns_none(self):
        assert _parse_source(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_source("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_source("   ") is None

    def test_two_part_name_raises(self):
        with pytest.raises(OsiConversionError, match="fully qualified"):
            _parse_source("schema.table")

    def test_one_part_name_raises(self):
        with pytest.raises(OsiConversionError, match="fully qualified"):
            _parse_source("table")

    def test_table_starting_with_select_not_subquery(self):
        """Table names like SELECT_RESULTS shouldn't be treated as subqueries."""
        with pytest.raises(OsiConversionError, match="fully qualified"):
            _parse_source("SELECT_RESULTS")


# ---------------------------------------------------------------------------
# _extract_synonyms
# ---------------------------------------------------------------------------

class TestExtractSynonyms:
    def test_dict_with_synonyms(self):
        assert _extract_synonyms({"synonyms": ["a", "b"]}) == ["a", "b"]

    def test_dict_without_synonyms(self):
        assert _extract_synonyms({"instructions": "foo"}) is None

    def test_empty_synonyms_list(self):
        assert _extract_synonyms({"synonyms": []}) is None

    def test_string_ai_context(self):
        assert _extract_synonyms("some instructions") is None

    def test_none(self):
        assert _extract_synonyms(None) is None

    def test_returns_copy(self):
        original = ["x", "y"]
        result = _extract_synonyms({"synonyms": original})
        assert result == original
        assert result is not original


# ---------------------------------------------------------------------------
# _classify_field
# ---------------------------------------------------------------------------

class TestClassifyField:
    def test_no_dimension_is_fact(self):
        assert _classify_field({"name": "x"}) == "fact"

    def test_dimension_not_time(self):
        assert _classify_field({"dimension": {"is_time": False}}) == "dimension"

    def test_dimension_is_time(self):
        assert _classify_field({"dimension": {"is_time": True}}) == "time_dimension"

    def test_dimension_bare_true(self):
        """A bare truthy `dimension` with no is_time dict => dimension."""
        assert _classify_field({"dimension": True}) == "dimension"

    def test_dimension_none_is_fact(self):
        assert _classify_field({"dimension": None}) == "fact"


# ---------------------------------------------------------------------------
# _extract_expression
# ---------------------------------------------------------------------------

class TestExtractExpression:
    def test_snowflake_preferred_over_ansi(self):
        expr = {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": "ansi_expr"},
                {"dialect": "SNOWFLAKE", "expression": "snow_expr"},
            ]
        }
        assert _extract_expression(expr, "f") == "snow_expr"

    def test_ansi_fallback(self):
        expr = {
            "dialects": [{"dialect": "ANSI_SQL", "expression": "ansi_expr"}]
        }
        assert _extract_expression(expr, "f") == "ansi_expr"

    def test_unsupported_dialect_returns_none_with_warning(self):
        expr = {
            "dialects": [{"dialect": "BIGQUERY", "expression": "bq_expr"}]
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _extract_expression(expr, "my_field")
        assert result is None
        assert len(w) == 1
        assert "my_field" in str(w[0].message)

    def test_missing_expression_raises(self):
        with pytest.raises(OsiConversionError, match="Missing or malformed"):
            _extract_expression(None, "f")

    def test_missing_dialects_raises(self):
        with pytest.raises(OsiConversionError, match="Missing expression"):
            _extract_expression({"dialects": []}, "f")

    def test_empty_dialects_raises(self):
        with pytest.raises(OsiConversionError, match="Missing expression"):
            _extract_expression({"dialects": None}, "f")


# ---------------------------------------------------------------------------
# _convert_named_expr
# ---------------------------------------------------------------------------

class TestConvertNamedExpr:
    def test_basic_conversion(self):
        entry = {
            "name": "col1",
            "expression": {
                "dialects": [{"dialect": "ANSI_SQL", "expression": "col1"}]
            },
            "description": "Column one",
            "ai_context": {"synonyms": ["c1"]},
        }
        result = _convert_named_expr(entry, "field")
        assert result == {
            "name": "col1",
            "expr": "col1",
            "description": "Column one",
            "synonyms": ["c1"],
        }

    def test_missing_name_raises(self):
        with pytest.raises(OsiConversionError, match="Missing required 'name'"):
            _convert_named_expr({"expression": {"dialects": []}}, "field")

    def test_no_description_or_synonyms(self):
        entry = {
            "name": "col1",
            "expression": {
                "dialects": [{"dialect": "ANSI_SQL", "expression": "col1"}]
            },
        }
        result = _convert_named_expr(entry, "field")
        assert "description" not in result
        assert "synonyms" not in result

    def test_unsupported_dialect_returns_none(self):
        entry = {
            "name": "col1",
            "expression": {
                "dialects": [{"dialect": "BIGQUERY", "expression": "x"}]
            },
        }
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert _convert_named_expr(entry, "field") is None


# ---------------------------------------------------------------------------
# _convert_relationship
# ---------------------------------------------------------------------------

class TestConvertRelationship:
    def test_basic_relationship(self):
        rel = {
            "name": "r1",
            "from": "table_a",
            "to": "table_b",
            "from_columns": ["a_id"],
            "to_columns": ["b_id"],
        }
        result = _convert_relationship(rel)
        assert result == {
            "name": "r1",
            "left_table": "table_a",
            "right_table": "table_b",
            "relationship_columns": [
                {"left_column": "a_id", "right_column": "b_id"}
            ],
        }

    def test_multi_column_relationship(self):
        rel = {
            "name": "r1",
            "from": "a",
            "to": "b",
            "from_columns": ["x", "y"],
            "to_columns": ["p", "q"],
        }
        result = _convert_relationship(rel)
        assert len(result["relationship_columns"]) == 2

    def test_missing_name_raises(self):
        with pytest.raises(OsiConversionError, match="Missing required 'name'"):
            _convert_relationship({"from": "a", "to": "b"})

    def test_missing_from_raises(self):
        with pytest.raises(OsiConversionError, match="missing required 'from'"):
            _convert_relationship({"name": "r", "to": "b"})

    def test_missing_to_raises(self):
        with pytest.raises(OsiConversionError, match="missing required 'to'"):
            _convert_relationship({"name": "r", "from": "a"})

    def test_column_count_mismatch_raises(self):
        rel = {
            "name": "r",
            "from": "a",
            "to": "b",
            "from_columns": ["x"],
            "to_columns": ["p", "q"],
        }
        with pytest.raises(OsiConversionError, match="same length"):
            _convert_relationship(rel)

    def test_no_columns(self):
        rel = {"name": "r", "from": "a", "to": "b"}
        result = _convert_relationship(rel)
        assert "relationship_columns" not in result


# ---------------------------------------------------------------------------
# _convert_dataset
# ---------------------------------------------------------------------------

class TestConvertDataset:
    def test_basic_dataset(self):
        ds = {
            "name": "my_table",
            "source": "db.schema.tbl",
            "primary_key": ["id"],
            "unique_keys": [["id"]],
            "description": "A table",
            "ai_context": {"synonyms": ["tbl"]},
            "fields": [
                {
                    "name": "id",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "id"}]
                    },
                    "dimension": {"is_time": False},
                },
                {
                    "name": "created_at",
                    "expression": {
                        "dialects": [
                            {"dialect": "ANSI_SQL", "expression": "created_at"}
                        ]
                    },
                    "dimension": {"is_time": True},
                },
                {
                    "name": "amount",
                    "expression": {
                        "dialects": [
                            {"dialect": "ANSI_SQL", "expression": "amount"}
                        ]
                    },
                },
            ],
        }
        result = _convert_dataset(ds)
        assert result["name"] == "my_table"
        assert result["base_table"] == {
            "database": "DB",
            "schema": "SCHEMA",
            "table": "TBL",
        }
        assert result["primary_key"] == {"columns": ["id"]}
        assert result["unique_keys"] == [{"columns": ["id"]}]
        assert result["description"] == "A table"
        assert result["synonyms"] == ["tbl"]
        assert len(result["dimensions"]) == 1
        assert result["dimensions"][0]["name"] == "id"
        assert len(result["time_dimensions"]) == 1
        assert result["time_dimensions"][0]["name"] == "created_at"
        assert len(result["facts"]) == 1
        assert result["facts"][0]["name"] == "amount"

    def test_missing_name_raises(self):
        with pytest.raises(OsiConversionError, match="Missing required 'name'"):
            _convert_dataset({"source": "db.s.t"})

    def test_no_source(self):
        ds = {
            "name": "t",
            "fields": [
                {
                    "name": "c",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "c"}]
                    },
                    "dimension": {"is_time": False},
                }
            ],
        }
        result = _convert_dataset(ds)
        assert "base_table" not in result

    def test_no_fields(self):
        ds = {"name": "t", "source": "db.s.t"}
        result = _convert_dataset(ds)
        assert "dimensions" not in result
        assert "time_dimensions" not in result
        assert "facts" not in result


# ---------------------------------------------------------------------------
# convert_osi_to_snowflake (end-to-end)
# ---------------------------------------------------------------------------

class TestConvertOsiToSnowflake:
    def test_minimal_model(self):
        osi_yaml = _wrap_osi(_minimal_model())
        result = yaml.safe_load(convert_osi_to_snowflake(osi_yaml))
        assert result["name"] == "test_model"
        assert "tables" in result
        assert result["tables"][0]["name"] == "my_table"

    def test_model_with_description(self):
        osi_yaml = _wrap_osi(_minimal_model(description="A model"))
        result = yaml.safe_load(convert_osi_to_snowflake(osi_yaml))
        assert result["description"] == "A model"

    def test_model_with_relationships(self):
        model = _minimal_model(
            relationships=[
                {
                    "name": "r1",
                    "from": "a",
                    "to": "b",
                    "from_columns": ["x"],
                    "to_columns": ["y"],
                }
            ]
        )
        result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["name"] == "r1"

    def test_model_with_metrics(self):
        model = _minimal_model(
            metrics=[
                {
                    "name": "total",
                    "expression": {
                        "dialects": [
                            {"dialect": "ANSI_SQL", "expression": "SUM(x)"}
                        ]
                    },
                    "description": "Total x",
                }
            ]
        )
        result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert len(result["metrics"]) == 1
        assert result["metrics"][0]["expr"] == "SUM(x)"

    def test_invalid_yaml_root_raises(self):
        with pytest.raises(OsiConversionError, match="expected a mapping"):
            convert_osi_to_snowflake("- a list")

    def test_wrong_version_raises(self):
        bad = yaml.dump({"version": "9.9.9", "semantic_model": [{"name": "m"}]})
        with pytest.raises(OsiConversionError, match="Unsupported Ossie specification"):
            convert_osi_to_snowflake(bad)

    def test_missing_semantic_model_raises(self):
        bad = yaml.dump({"version": "0.2.0.dev0"})
        with pytest.raises(OsiConversionError, match="non-empty list"):
            convert_osi_to_snowflake(bad)

    def test_empty_semantic_model_raises(self):
        bad = yaml.dump({"version": "0.2.0.dev0", "semantic_model": []})
        with pytest.raises(OsiConversionError, match="non-empty list"):
            convert_osi_to_snowflake(bad)

    def test_non_dict_model_entry_raises(self):
        bad = yaml.dump({"version": "0.2.0.dev0", "semantic_model": ["not a dict"]})
        with pytest.raises(OsiConversionError, match="must be mappings"):
            convert_osi_to_snowflake(bad)

    def test_missing_model_name_raises(self):
        bad = yaml.dump({"version": "0.2.0.dev0", "semantic_model": [{"description": "x"}]})
        with pytest.raises(OsiConversionError, match="Missing required 'name'"):
            convert_osi_to_snowflake(bad)

    def test_multiple_models_warns(self):
        multi = yaml.dump(
            {
                "version": "0.2.0.dev0",
                "semantic_model": [
                    _minimal_model(name="first"),
                    _minimal_model(name="second"),
                ],
            }
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(multi))
        assert result["name"] == "first"
        assert any("only the first" in str(warning.message) for warning in w)

    def test_snowflake_dialect_preferred(self):
        model = {
            "name": "m",
            "datasets": [
                {
                    "name": "t",
                    "source": "db.s.t",
                    "fields": [
                        {
                            "name": "c",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "ansi_c"},
                                    {"dialect": "SNOWFLAKE", "expression": "snow_c"},
                                ]
                            },
                            "dimension": {"is_time": False},
                        }
                    ],
                }
            ],
        }
        result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert result["tables"][0]["dimensions"][0]["expr"] == "snow_c"

    def test_fields_with_unsupported_dialect_skipped(self):
        model = {
            "name": "m",
            "datasets": [
                {
                    "name": "t",
                    "source": "db.s.t",
                    "fields": [
                        {
                            "name": "good",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "good"}
                                ]
                            },
                            "dimension": {"is_time": False},
                        },
                        {
                            "name": "bad",
                            "expression": {
                                "dialects": [
                                    {"dialect": "BIGQUERY", "expression": "bad"}
                                ]
                            },
                            "dimension": {"is_time": False},
                        },
                    ],
                }
            ],
        }
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        dims = result["tables"][0]["dimensions"]
        assert len(dims) == 1
        assert dims[0]["name"] == "good"

    def test_subquery_source(self):
        model = {
            "name": "m",
            "datasets": [
                {
                    "name": "t",
                    "source": "SELECT * FROM db.s.t WHERE active = 1",
                    "fields": [
                        {
                            "name": "c",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "c"}
                                ]
                            },
                            "dimension": {"is_time": False},
                        }
                    ],
                }
            ],
        }
        result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert "definition" in result["tables"][0]["base_table"]


# ---------------------------------------------------------------------------
# _warn_dropped_fields (Ossie concepts with no Snowflake counterpart)
# ---------------------------------------------------------------------------

class TestWarnDroppedFields:
    def test_custom_extensions_warned(self):
        source = {"custom_extensions": [{"vendor_name": "X", "data": "{}"}]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "model")
        assert len(w) == 1
        assert "custom_extensions" in str(w[0].message)

    def test_label_warned(self):
        source = {"label": "My Label"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "field 'x'")
        assert len(w) == 1
        assert "label" in str(w[0].message)

    def test_version_warned(self):
        source = {"version": "1.0"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "model")
        assert len(w) == 1
        assert "version" in str(w[0].message)

    def test_extra_dropped_included(self):
        source = {"label": "L"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "field 'x'",
                                 extra_dropped=["ai_context (instructions)"])
        assert len(w) == 1
        msg = str(w[0].message)
        assert "ai_context (instructions)" in msg
        assert "label" in msg

    def test_extra_dropped_only(self):
        source = {}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "model", extra_dropped=["ai_context"])
        assert len(w) == 1
        assert "ai_context" in str(w[0].message)

    def test_no_dropped_fields_no_warning(self):
        source = {"name": "x", "description": "ok"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "field 'x'")
        assert len(w) == 0

    def test_multiple_dropped_fields_single_warning(self):
        source = {"label": "L", "custom_extensions": [{}], "version": "1"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_dropped_fields(source, "model")
        assert len(w) == 1
        msg = str(w[0].message)
        assert "custom_extensions" in msg
        assert "label" in msg
        assert "version" in msg


# ---------------------------------------------------------------------------
# Dropped fields in end-to-end conversion
# ---------------------------------------------------------------------------

class TestDroppedFieldsEndToEnd:
    def test_custom_extensions_dropped_with_warning(self):
        model = _minimal_model(
            custom_extensions=[{"vendor_name": "X", "data": "{}"}]
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert "custom_extensions" not in result
        assert any("custom_extensions" in str(x.message) for x in w)

    def test_model_level_ai_context_dropped_with_warning(self):
        model = _minimal_model(
            ai_context={"instructions": "use this model for analytics"}
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert "ai_context" not in result
        assert any("ai_context" in str(x.message) for x in w)

    def test_relationship_ai_context_dropped_with_warning(self):
        model = _minimal_model(
            relationships=[
                {
                    "name": "r1",
                    "from": "a",
                    "to": "b",
                    "from_columns": ["x"],
                    "to_columns": ["y"],
                    "ai_context": {"synonyms": ["related"]},
                }
            ]
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        rel = result["relationships"][0]
        assert "ai_context" not in rel
        assert "synonyms" not in rel
        assert any("ai_context" in str(x.message) for x in w)

    def test_model_string_ai_context_appended_to_description(self):
        model = _minimal_model(
            description="A model",
            ai_context="use this model for analytics",
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert result["description"] == "A model\nuse this model for analytics"
        assert not any("ai_context" in str(x.message) for x in w)

    def test_model_string_ai_context_becomes_description_when_none(self):
        model = _minimal_model(ai_context="use this model for analytics")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert result["description"] == "use this model for analytics"
        assert not any("ai_context" in str(x.message) for x in w)

    def test_dataset_string_ai_context_appended_to_description(self):
        model = {
            "name": "m",
            "datasets": [
                {
                    "name": "t",
                    "source": "db.s.t",
                    "description": "A table",
                    "ai_context": "contains sales data",
                    "fields": [
                        {
                            "name": "c",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "c"}
                                ]
                            },
                            "dimension": {"is_time": False},
                        }
                    ],
                }
            ],
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        assert result["tables"][0]["description"] == "A table\ncontains sales data"
        assert not any("ai_context" in str(x.message) for x in w)

    def test_field_string_ai_context_appended_to_description(self):
        model = {
            "name": "m",
            "datasets": [
                {
                    "name": "t",
                    "source": "db.s.t",
                    "fields": [
                        {
                            "name": "c",
                            "description": "A column",
                            "ai_context": "always filter on this",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "c"}
                                ]
                            },
                            "dimension": {"is_time": False},
                        }
                    ],
                }
            ],
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        dim = result["tables"][0]["dimensions"][0]
        assert dim["description"] == "A column\nalways filter on this"
        assert not any("ai_context" in str(x.message) for x in w)

    def test_relationship_string_ai_context_dropped_with_warning(self):
        model = _minimal_model(
            relationships=[
                {
                    "name": "r1",
                    "from": "a",
                    "to": "b",
                    "from_columns": ["x"],
                    "to_columns": ["y"],
                    "ai_context": "these tables are related by key",
                }
            ]
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        rel = result["relationships"][0]
        assert "description" not in rel
        assert any("ai_context" in str(x.message) for x in w)

    def test_field_label_dropped_with_warning(self):
        model = {
            "name": "m",
            "datasets": [
                {
                    "name": "t",
                    "source": "db.s.t",
                    "fields": [
                        {
                            "name": "c",
                            "label": "My Column",
                            "expression": {
                                "dialects": [
                                    {"dialect": "ANSI_SQL", "expression": "c"}
                                ]
                            },
                            "dimension": {"is_time": False},
                        }
                    ],
                }
            ],
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = yaml.safe_load(convert_osi_to_snowflake(_wrap_osi(model)))
        dim = result["tables"][0]["dimensions"][0]
        assert "label" not in dim
        assert any("label" in str(x.message) for x in w)
