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

"""Unit coverage for the ``enrich`` operator (N:1 join).

Pins two behaviours:

* the happy path — an N:1 enrich preserves the parent grain and brings the
  one-side's columns in as ``from_join_rhs`` values; and
* the **fan-trap rule** (Semantic 2, §6.1): enriching with a child that is not
  unique on the join keys would duplicate parent rows, so it is rejected with
  the algebra's fan-out signal ``E4001_EXPLOSION_UNSAFE``. That code — not the
  engine-wide M:N opt-out ``E3011`` — is what the algebra raises; the planner
  maps it to the user-facing ``E_UNSAFE_REAGGREGATION`` /
  ``E_FAN_OUT_IN_SCALAR_QUERY`` per the query shape.
"""

from __future__ import annotations

import pytest
from strategies import dimension_column, fact_column

from ossie.algebra import JoinType, enrich, source
from ossie.common.identifiers import normalize_identifier as ident
from ossie.errors import AlgebraError, ErrorCode


def _orders() -> object:
    """orders(oid PK, cust_id dim join-key, amount fact)."""
    return source(
        primary_key=frozenset({ident("oid")}),
        dimension_columns=[dimension_column(ident("oid")), dimension_column(ident("cust_id"))],
        fact_columns=[fact_column(ident("amount"))],
    )


def test_enrich_n1_preserves_grain_and_adds_rhs_columns() -> None:
    orders = _orders()
    customers = source(
        primary_key=frozenset({ident("cid")}),
        dimension_columns=[dimension_column(ident("cid")), dimension_column(ident("region"))],
    )

    out = enrich(
        orders,
        customers,
        parent_keys=(ident("cust_id"),),
        child_keys=(ident("cid"),),
        join_type=JoinType.LEFT,
    )

    assert out.grain == orders.grain  # N:1 preserves the many-side grain
    assert ident("region") in out.column_names  # one-side column brought in
    assert out.column(ident("region")).from_join_rhs is True


def test_enrich_fan_trap_raises_e4001() -> None:
    orders = _orders()
    # returns is keyed by rid; cust_id2 is neither the PK nor a declared unique
    # key, so returns is NOT unique on the join key -> joining would fan out.
    returns = source(
        primary_key=frozenset({ident("rid")}),
        dimension_columns=[dimension_column(ident("rid")), dimension_column(ident("cust_id2"))],
    )

    with pytest.raises(AlgebraError) as excinfo:
        enrich(
            orders,
            returns,
            parent_keys=(ident("cust_id"),),
            child_keys=(ident("cust_id2"),),
            join_type=JoinType.LEFT,
        )

    assert excinfo.value.code is ErrorCode.E4001_EXPLOSION_UNSAFE


def test_enrich_on_declared_unique_key_is_allowed() -> None:
    """A child unique on the join key via a declared UK (not the PK) is safe."""
    orders = _orders()
    # customers PK is a surrogate sid, but email is a declared unique key.
    customers = source(
        primary_key=frozenset({ident("sid")}),
        dimension_columns=[
            dimension_column(ident("sid")),
            dimension_column(ident("email")),
            dimension_column(ident("region")),
        ],
        unique_keys=[frozenset({ident("email")})],
    )

    out = enrich(
        orders,
        customers,
        parent_keys=(ident("cust_id"),),
        child_keys=(ident("email"),),
        join_type=JoinType.LEFT,
    )
    assert out.grain == orders.grain
    assert ident("region") in out.column_names
