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

"""Re-aggregation safety of ``aggregate`` (Semantic 4, §6.1).

The algebra blocks *holistic* re-aggregation of a **discharged** value — one
already aggregated at a finer grain and brought in by :func:`enrich` (the bridge
mid-pipeline plan). It does NOT block:

* a holistic aggregate over a *plain* enriched dimension — that is a single-step
  aggregate over the non-fanned joined rows, well-defined per D-020; or
* a *distributive* re-aggregation of a discharged value (``SUM`` of ``SUM``).

These tests pin the discriminator (they guard the F2 fix: the guard keys off
``is_discharged_aggregate``, not the broader ``from_join_rhs``).
"""

from __future__ import annotations

import pytest
from strategies import aggregate_column, dimension_column, fact_column

from ossie.algebra import AggregateFunction, JoinType, aggregate, enrich, source
from ossie.common.identifiers import normalize_identifier as ident
from ossie.errors import AlgebraError, ErrorCode


def _orders() -> object:
    """orders(oid PK, cust_id dim join-key, amount fact)."""
    return source(
        primary_key=frozenset({ident("oid")}),
        dimension_columns=[dimension_column(ident("oid")), dimension_column(ident("cust_id"))],
        fact_columns=[fact_column(ident("amount"))],
    )


def _orders_enriched_with_customer_region() -> object:
    """orders N:1 customers, bringing in ``region`` as a plain enriched dim."""
    customers = source(
        primary_key=frozenset({ident("cid")}),
        dimension_columns=[dimension_column(ident("cid")), dimension_column(ident("region"))],
    )
    return enrich(
        _orders(),
        customers,
        parent_keys=(ident("cust_id"),),
        child_keys=(ident("cid"),),
        join_type=JoinType.LEFT,
    )


def _orders_enriched_with_discharged_total() -> object:
    """orders N:1 (returns pre-aggregated to ckey), bringing in a discharged SUM."""
    returns = source(
        primary_key=frozenset({ident("rid")}),
        dimension_columns=[dimension_column(ident("rid")), dimension_column(ident("ckey"))],
        fact_columns=[fact_column(ident("amt"))],
    )
    returns_by_customer = aggregate(
        returns,
        frozenset({ident("ckey")}),
        [aggregate_column(ident("tot"), function=AggregateFunction.SUM, over=ident("amt"))],
    )
    return enrich(
        _orders(),
        returns_by_customer,
        parent_keys=(ident("cust_id"),),
        child_keys=(ident("ckey"),),
        join_type=JoinType.LEFT,
    )


def test_holistic_over_plain_enriched_dim_is_allowed() -> None:
    # COUNT(DISTINCT region) grouped by cust_id: region is enriched (N:1) but
    # NOT discharged, so this single-step holistic is well-defined (D-020).
    state = _orders_enriched_with_customer_region()
    out = aggregate(
        state,
        frozenset({ident("cust_id")}),
        [
            aggregate_column(
                ident("n_regions"),
                function=AggregateFunction.COUNT_DISTINCT,
                over=ident("region"),
            )
        ],
    )
    assert out.grain == frozenset({ident("cust_id")})
    assert ident("n_regions") in out.column_names


def test_holistic_over_discharged_aggregate_raises_e4001() -> None:
    state = _orders_enriched_with_discharged_total()
    with pytest.raises(AlgebraError) as excinfo:
        aggregate(
            state,
            frozenset({ident("cust_id")}),
            [
                aggregate_column(
                    ident("n"),
                    function=AggregateFunction.COUNT_DISTINCT,
                    over=ident("tot"),
                )
            ],
        )
    assert excinfo.value.code is ErrorCode.E4001_EXPLOSION_UNSAFE


def test_distributive_over_discharged_aggregate_is_allowed() -> None:
    # SUM of a discharged SUM is distributive and therefore safe to re-aggregate.
    state = _orders_enriched_with_discharged_total()
    out = aggregate(
        state,
        frozenset({ident("cust_id")}),
        [aggregate_column(ident("s"), function=AggregateFunction.SUM, over=ident("tot"))],
    )
    assert ident("s") in out.column_names
