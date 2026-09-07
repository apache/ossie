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

from ossie_thoughtspot.issues import IssueLog, Severity
from ossie_thoughtspot.keys import Relationship, derive_keys


def rel(name, to_columns, cardinality="MANY_TO_ONE", residual=False):
    return Relationship(
        name=name,
        to_dataset="customers",
        to_columns=to_columns,
        cardinality=cardinality,
        has_residual_predicates=residual,
    )


def test_single_qualifying_relationship_yields_a_primary_key():
    log = IssueLog()
    pk, uniques = derive_keys("customers", [rel("r1", ["customer_id"])], log)
    assert pk == ["customer_id"]
    assert uniques == [["customer_id"]]
    assert log.as_dicts() == []


def test_disagreeing_qualifying_relationships_yield_unique_keys_and_no_primary_key():
    # Choosing one would be a guess; both are real join targets.
    log = IssueLog()
    pk, uniques = derive_keys(
        "customers", [rel("by_id", ["customer_id"]), rel("by_email", ["email"])], log
    )
    assert pk is None
    assert sorted(uniques) == [["customer_id"], ["email"]]


def test_residual_predicate_relationship_is_not_key_evidence():
    # The equality columns alone are not unique — the narrowing makes it to-one.
    log = IssueLog()
    pk, uniques = derive_keys("customers", [rel("asof", ["ccy"], residual=True)], log)
    assert pk is None
    assert uniques == []


def test_many_to_many_is_not_key_evidence():
    log = IssueLog()
    pk, uniques = derive_keys("customers", [rel("bridge", ["c_id"], cardinality="MANY_TO_MANY")], log)
    assert pk is None
    assert uniques == []


def test_a_disqualified_sibling_raises_an_issue_naming_it():
    # I1: "ccy" does not cover the derived key ("customer_id"), so
    # upstream's to_columns coverage check (validate.py:159-165) genuinely
    # will warn here — the claim is correct and must be present.
    log = IssueLog()
    pk, uniques = derive_keys(
        "customers", [rel("by_id", ["customer_id"]), rel("asof", ["ccy"], residual=True)], log
    )
    assert pk == ["customer_id"]
    issues = log.as_dicts()
    assert len(issues) == 1
    assert issues[0]["severity"] == Severity.WARNING.value
    assert "asof" in issues[0]["message"]
    assert "coverage warning" in issues[0]["message"]


def test_residual_join_whose_columns_cover_the_key_has_no_upstream_warning_claim():
    # I1: the canonical SCD-2 shape — a residual (as-of) join whose
    # to_columns exactly covers the derived key. Upstream's coverage check
    # (validate.py:159-165) passes clean here, so the message must not
    # predict a warning that will not fire.
    log = IssueLog()
    pk, uniques = derive_keys(
        "customers",
        [rel("by_id", ["customer_id"]), rel("scd2_asof", ["customer_id"], residual=True)],
        log,
    )
    assert pk == ["customer_id"]
    issues = log.as_dicts()
    assert len(issues) == 1
    assert issues[0]["severity"] == Severity.WARNING.value
    assert "scd2_asof" in issues[0]["message"]
    assert "coverage warning" not in issues[0]["message"]


def test_column_order_within_a_composite_key_is_preserved():
    log = IssueLog()
    pk, _ = derive_keys("customers", [rel("r", ["region", "customer_id"])], log)
    assert pk == ["region", "customer_id"]


def test_empty_to_columns_yields_no_key_and_raises_an_error():
    # I1: an empty to_columns is a hard schema failure (minItems: 1) — such a
    # relationship cannot be emitted at all, so there is no upstream check
    # left to run and no coverage warning to predict. This is ERROR, not
    # WARNING, and the remedy must not claim it is "Expected".
    log = IssueLog()
    pk, uniques = derive_keys("customers", [rel("blank", [])], log)
    assert pk is None
    assert uniques == []
    issues = log.as_dicts()
    assert len(issues) == 1
    assert "blank" in issues[0]["message"]
    assert issues[0]["severity"] == Severity.ERROR.value
    assert "coverage warning" not in issues[0]["message"]
    assert not issues[0]["remedy"].lower().startswith("expected")


def test_agreeing_qualifying_relationships_collapse_to_one_unique_key():
    # A dimension joined from several fact tables on the same foreign key is
    # a common shape — it must not be mistaken for disagreement.
    log = IssueLog()
    pk, uniques = derive_keys(
        "customers", [rel("from_orders", ["customer_id"]), rel("from_invoices", ["customer_id"])], log
    )
    assert pk == ["customer_id"]
    assert uniques == [["customer_id"]]
    assert log.as_dicts() == []
