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

"""Construct's __post_init__ validation invariants.

A DIRECT or PASSTHROUGH row with no template would pass every other check and
the coverage gate, and only surface when something later tries to emit it -
so this is enforced at construction time instead.
"""
import pytest

from ossie_thoughtspot.expressions import Classification, Construct, Variant


def test_direct_construct_requires_a_template():
    with pytest.raises(ValueError, match="SUM\\(expr\\).*direct.*template"):
        Construct(spec_name="SUM(expr)", classification=Classification.DIRECT, template=None)


def test_direct_construct_rejects_an_empty_string_template():
    with pytest.raises(ValueError, match="template"):
        Construct(spec_name="SUM(expr)", classification=Classification.DIRECT, template="")


def test_passthrough_construct_requires_a_template():
    with pytest.raises(ValueError, match="template"):
        Construct(
            spec_name="STDDEV_POP(expr)",
            classification=Classification.PASSTHROUGH,
            variant=Variant.NUMBER_AGGREGATE,
            template=None,
        )


def test_unmappable_construct_needs_no_template():
    # Should not raise: UNMAPPABLE is the one classification allowed no template.
    Construct(spec_name="EXISTS_IN()", classification=Classification.UNMAPPABLE)


def test_direct_construct_with_a_template_is_valid():
    # Should not raise.
    Construct(spec_name="ABS(x)", classification=Classification.DIRECT, template="abs ( {0} )")


def test_passthrough_construct_with_a_template_and_variant_is_valid():
    # Should not raise.
    Construct(
        spec_name="STDDEV_POP(expr)",
        classification=Classification.PASSTHROUGH,
        template='sql_number_aggregate_op ( "STDDEV_POP({0})" , {0} )',
        variant=Variant.NUMBER_AGGREGATE,
    )
