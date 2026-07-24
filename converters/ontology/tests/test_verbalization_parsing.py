"""Tests for verbalization parsing — the dash-convention text splitting in
``_parse_verbalization`` / ``_split_segment``.

These lock in the intended prefix/postfix/text semantics:
  - a trailing dash (``chain-``) marks a *prefix* attached to the following concept;
  - a segment containing a leading-dash word (``-box``) is a *postfix* group
    attached to the preceding concept — the first word plus any following
    dash-prefixed words (so ``big -box`` -> postfix ``"big box"``).
"""

from __future__ import annotations

import re

import pytest

from osi.model import Concept, Relationship, _parse_verbalization as parse_verbalization


def _rel(name: str, container: Concept, relates: list[tuple[Concept, str | None]], verbalization: str) -> Relationship:
    return Relationship(name, container, relates, verbalizes=[verbalization])


def test_all_possible_postfixes_prefixes():
    store_concept = Concept("Store")
    item_concept = Concept("Item")
    amount_concept = Concept("Amount")
    verbalization = (
        "every chain- super {Store} reports returns of {Item}"
        " big -box for average- {Amount:amt}"
    )
    rel = _rel(
        "chain_report",
        store_concept,
        [(item_concept, None), (amount_concept, "amt")],
        verbalization,
    )
    roles = parse_verbalization(rel, verbalization).roles
    assert len(roles) == 3

    store = roles[0]
    assert store.concept == store_concept
    assert store.name is None
    assert store.preceding_text == "every"
    assert store.prefix == "chain super"
    assert store.following_text == "reports returns of"
    assert store.postfix is None

    item = roles[1]
    assert item.concept == item_concept
    assert item.name is None
    assert item.prefix is None
    assert item.preceding_text is None
    assert item.postfix == "big box"

    amount = roles[2]
    assert amount.concept == amount_concept
    assert amount.name == "amt"
    assert amount.preceding_text is None
    assert amount.prefix == "average"
    assert amount.postfix is None
    assert amount.following_text is None


def test_no_text_decoration():
    verbalization = "{Person} earns {Salary} annually"
    rel = _rel("earns", Concept("Person"), [(Concept("Salary"), None)], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[0].following_text == "earns"
    assert roles[0].prefix is None
    assert roles[0].postfix is None
    assert roles[1].following_text == "annually"


def test_single_word_prefix():
    verbalization = "big- {Store} sells {Item}"
    rel = _rel("sells", Concept("Store"), [(Concept("Item"), None)], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[0].prefix == "big"
    assert roles[0].preceding_text is None


def test_multi_word_prefix_before_first_concept():
    verbalization = "every chain- super {Store}"
    rel = _rel("r", Concept("Store"), [], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[0].preceding_text == "every"
    assert roles[0].prefix == "chain super"


def test_multi_word_postfix_before_end():
    verbalization = "{Item} big -box end"
    rel = _rel("r", Concept("Item"), [], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[0].postfix == "big box"
    assert roles[0].following_text == "end"


def test_postfix_directly_after_last_concept():
    verbalization = "{Person} big -city"
    rel = _rel("r", Concept("Person"), [], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[-1].postfix == "big city"
    assert roles[-1].following_text is None


def test_only_following_text_after_last_concept():
    verbalization = "{Person} is active"
    rel = _rel("r", Concept("Person"), [], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[0].following_text == "is active"
    assert roles[0].postfix is None


def test_ternary_relationship():
    """Three-role verbalization with inter-concept prefix markers.
    A trailing-dashed word between two tokens is a prefix for the *following* concept."""
    verbalization = "{Supplier} delivers- {Item} to- {Warehouse} on {Date}"
    rel = _rel(
        "delivers",
        Concept("Supplier"),
        [(Concept("Item"), None), (Concept("Warehouse"), None), (Concept("Date"), None)],
        verbalization,
    )
    roles = parse_verbalization(rel, verbalization).roles
    assert len(roles) == 4
    assert roles[0].prefix is None
    assert roles[0].postfix is None
    assert roles[1].prefix == "delivers"   # 'delivers-' -> prefix of Item
    assert roles[2].prefix == "to"         # 'to-' -> prefix of Warehouse
    assert roles[2].following_text == "on"
    assert roles[3].prefix is None


def test_verbalization_role_concept_not_in_model_raises():
    """Verbalization says {Invalid} but the relationship role plays Ghost."""
    verbalization = "{Invalid} haunts {Person}"
    with pytest.raises(ValueError, match=re.escape("Invalid")):
        _rel("r", Concept("Ghost"), [(Concept("Person"), None)], verbalization)


def test_builtin_number_concept_resolves():
    """Number(p,s) is a valid concept name with parentheses."""
    number_concept = Concept("Number(12,4)")
    verbalization = "{Order} has total {Number(12,4)}"
    rel = _rel("has_total", Concept("Order"), [(number_concept, None)], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert len(roles) == 2
    assert roles[1].concept.name == "Number(12,4)"


def test_builtin_integer_concept_resolves():
    integer_concept = Concept("Integer")
    verbalization = "{Product} has quantity {Integer}"
    rel = _rel("has_qty", Concept("Product"), [(integer_concept, None)], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert roles[1].concept.name == "Integer"


def test_wrong_concept_name_in_token_raises():
    """Verbalization says {Item} but the relationship role plays Store."""
    verbalization = "{Item}"
    with pytest.raises(ValueError, match="does not match verbalization role"):
        _rel("r", Concept("Store"), [], verbalization)


def test_wrong_role_name_in_token_raises():
    """Verbalization says {Store:wrong} but the relationship role name is unset."""
    verbalization = "{Store:wrong}"
    with pytest.raises(ValueError, match="does not match verbalization role"):
        _rel("r", Concept("Store"), [], verbalization)


def test_same_concept_different_roles_valid():
    """Same concept (Person) playing two roles with distinct names is valid as
    long as the verbalization tokens match the relationship roles."""
    person = Concept("Person")
    verbalization = "{Person} has parent {Person:parent}"
    rel = _rel("parenthood", person, [(person, "parent")], verbalization)
    roles = parse_verbalization(rel, verbalization).roles
    assert len(roles) == 2
    assert roles[0].name is None
    assert roles[1].name == "parent"
    assert roles[0].concept is roles[1].concept