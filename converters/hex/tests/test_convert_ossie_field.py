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

from typing import Any

import pytest
import yaml
from ossie import OSIDialect

from ossie_hex.ossie_to_hex import convert_ossie_to_hex
from tests.utils import field_ossie, labelled_field_ossie


@pytest.mark.parametrize(
    ("datatype", "is_time", "hex_type", "warning"),
    [
        # Ossie infers the role from a temporal datatype, which is how Hex reads
        # its own types, so an inferred role and an explicit one both agree.
        ("Date", None, "date", None),
        ("Date", True, "date", None),
        ("String", None, "string", None),
        ("String", False, "string", None),
        # A role Hex cannot infer from the type is dropped, as for a year grain
        # kept as an integer.
        ("Integer", True, "number", "is a time dimension"),
        # Ossie can hold a temporal column off the time axis; Hex cannot.
        ("Date", False, "date", "is marked is_time: false"),
        # `Time` is the one Ossie temporal type with no Hex equivalent, so it
        # lands on `other` and the role disagrees without anyone marking it.
        ("Time", None, "other", "is a time dimension"),
        ("Time", False, "other", None),
    ],
)
def test_time_role_survives_only_when_the_hex_type_carries_it(
    datatype: str | None,
    is_time: bool | None,
    hex_type: str,
    warning: str | None,
) -> None:
    files, warnings = convert_ossie_to_hex(
        field_ossie(datatype=datatype, is_time=is_time),
        dialect=OSIDialect.ANSI_SQL,
    )
    dimension = yaml.safe_load(files["orders.yml"])["dimensions"][0]

    assert dimension["type"] == hex_type
    if warning is None:
        assert warnings == []
    else:
        assert len(warnings) == 1
        assert warning in str(warnings[0])


def test_a_field_without_dimension_metadata_has_no_time_role_to_lose() -> None:
    """Every Ossie field becomes a Hex dimension, but only some carry a role.

    A field with no ``dimension`` block never opted out of the time axis, so a
    temporal datatype on it must not be reported as a dropped opt-out.
    """
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: created_at
            datatype: Date
            expression:
              dialects: [{dialect: ANSI_SQL, expression: created_at}]
"""
    _, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL)

    assert warnings == []


def _dimension_for(field_expression: str, *, related: bool = True) -> dict[str, Any]:
    """Convert an `orders.label` field, with `customers` reachable as `buyer`."""
    files, _ = convert_ossie_to_hex(
        labelled_field_ossie(field_expression, related=related),
        dialect=OSIDialect.ANSI_SQL,
        base_model="orders",
    )
    dimensions = yaml.safe_load(files["orders.yml"])["dimensions"]
    return next(d for d in dimensions if d["id"] == "label")


def test_a_dimension_expression_reaching_another_model_uses_the_relation() -> None:
    dimension = _dimension_for("UPPER(customers.name)")

    assert dimension["expr_sql"] == "UPPER(${buyer.name})"


def test_a_dimension_expression_reaching_an_unrelated_model_is_left_verbatim() -> None:
    dimension = _dimension_for("UPPER(customers.name)", related=False)

    assert dimension["expr_sql"] == "UPPER(customers.name)"


def test_a_dimension_that_only_reads_its_own_column_has_no_expression() -> None:
    """Hex derives expr_sql from the dimension ID, so emitting it would be noise."""
    assert "expr_sql" not in _dimension_for("label")
    assert "expr_sql" not in _dimension_for("orders.label")
