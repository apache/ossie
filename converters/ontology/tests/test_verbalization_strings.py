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

"""Tests for the two name normalisations every name in a generated ontology
goes through: ``to_verbalization_string`` for relationship and property names,
``to_pascal_case`` for concept names.

Both what they return and what they say about it: a warning is the only account
the reader gets of why a name in the generated ontology differs from the name in
the export, so it has to describe the substitution that actually happened.
"""

from __future__ import annotations

import pytest

from ossie_ontology.common.utils import to_pascal_case, to_verbalization_string


def _warnings(caplog) -> list[str]:
    return [record.message for record in caplog.records]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("label", "label"),
        ("Mixed Case", "mixed_case"),
        ("dash-separated", "dash_separated"),
        ("collapse___underscores", "collapse_underscores"),
        ("1st_place", "Onest_place"),
        ("9lives", "Ninelives"),
        ("x%y", "x_y"),
        ("2nd-place!", "Twond_place_"),
        ("class", "class_k"),
    ],
)
def test_normalisation(raw: str, expected: str):
    assert to_verbalization_string(raw) == expected


def test_leading_digit_alone_is_not_reported_as_an_unsupported_symbol(caplog):
    """The substitution is real, but it is not a character-class problem.

    Reporting one sends the reader looking for a symbol that was never there.
    """
    with caplog.at_level("WARNING"):
        assert to_verbalization_string("1st_place") == "Onest_place"

    assert _warnings(caplog) == []


def test_unsupported_symbol_is_reported(caplog):
    with caplog.at_level("WARNING"):
        assert to_verbalization_string("x%y") == "x_y"

    assert any("has unsupported symbols" in message for message in _warnings(caplog))


def test_leading_digit_and_unsupported_symbol_together(caplog):
    """A name with both still earns the symbol warning, and both fixes."""
    with caplog.at_level("WARNING"):
        assert to_verbalization_string("2nd-place!") == "Twond_place_"

    assert any("has unsupported symbols" in message for message in _warnings(caplog))


def test_reserved_keyword_is_suffixed_and_reported(caplog):
    with caplog.at_level("WARNING"):
        assert to_verbalization_string("class") == "class_k"

    assert any("reserved keyword" in message for message in _warnings(caplog))


def test_name_that_reduces_to_nothing_raises():
    with pytest.raises(ValueError, match="reduces to an empty identifier"):
        to_verbalization_string("   ")


# ----- concept names ----------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("widget", "Widget"),
        ("net worth", "NetWorth"),
        ("a-b c", "ABC"),
        ("r&d", "RAndd"),  # '&' is not a word separator, so 'd' is not capitalised
        # A leading digit is not a legal identifier and reaches the formula
        # lexer as one, so it is spelt out — as `to_verbalization_string` does
        # for the names it normalises.
        ("3m corp", "ThreeMCorp"),
        ("3rd_party", "ThreeRdParty"),
        ("7 eleven store", "SevenElevenStore"),
        ("9", "Nine"),
        ("123", "One23"),
    ],
)
def test_pascal_case(raw: str, expected: str):
    assert to_pascal_case(raw) == expected


def test_pascal_case_of_nothing_is_nothing():
    assert to_pascal_case("") == ""


def test_pascal_case_never_starts_with_a_digit():
    for raw in ("3m corp", "9lives", "0 day", "123", "42"):
        assert not to_pascal_case(raw)[0].isdigit()
