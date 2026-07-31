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

import json

import pytest
import yaml
from ossie import OSIDialect

from ossie_hex.ossie_to_hex import convert_ossie_to_hex
from ossie_hex.util.yaml import load_yaml


@pytest.mark.parametrize(
    ("source", "base_key"),
    [
        ("orders", "base_sql_table"),
        ("public.orders", "base_sql_table"),
        ("analytics.public.orders", "base_sql_table"),
        ('"Order Items".orders', "base_sql_table"),
        ("`proj-1`.ds.orders", "base_sql_table"),
        ("db . schema . orders", "base_sql_table"),
        ("SELECT 1 AS x", "base_sql_query"),
        ("WITH t AS (SELECT 1 AS x) SELECT * FROM t", "base_sql_query"),
        ("(SELECT 1 AS x)", "base_sql_query"),
        ("-- daily events\nSELECT 1 AS x", "base_sql_query"),
        ("/* daily events */ SELECT 1 AS x", "base_sql_query"),
        ("FROM raw.events SELECT id", "base_sql_query"),
        ("TABLE orders", "base_sql_query"),
        ("VALUES (1), (2)", "base_sql_query"),
        ("read_parquet('s3://bucket/f.parquet')", "base_sql_query"),
    ],
)
def test_export_classifies_source_without_hex_extension(
    source: str, base_key: str
) -> None:
    # A dataset from another tool carries no HEX extension, so the source kind has to
    # be recovered from the source text itself.
    ossie = f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: {json.dumps(source)}
"""
    files, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    resource = load_yaml(files["orders.yml"])
    assert resource.get(base_key) == source
    assert warnings == []


def test_export_is_deterministic_for_synthesized_key_dimensions() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: facts
        source: s.facts
        primary_key: [k_alpha, k_bravo, k_charlie, k_delta, k_echo]
        fields:
          - name: v
            expression:
              dialects: [{dialect: ANSI_SQL, expression: v}]
            dimension: {}
"""
    files, _ = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)
    ids = [d["id"] for d in yaml.safe_load(files["facts.yml"])["dimensions"]]

    # Declaration order, not set-iteration order, which varies per process.
    assert ids == ["v_", "k_alpha", "k_bravo", "k_charlie", "k_delta", "k_echo"]
