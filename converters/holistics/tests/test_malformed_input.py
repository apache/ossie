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

"""A malformed Ossie document is reported, not crashed on.

`to-aml` reads documents this converter did not write, so its input is a
boundary rather than a known shape. The CLI catches `ConversionError`, `OSError`
and `UnicodeDecodeError`, so anything else reaches the user as a traceback.

Two sibling reviews asked for exactly this. apache/ossie#407 found a converter
that checked `datasets` was present but not that it was a list of mappings, and
apache/ossie#297 found several direct `element["id"]` reads that raised
`KeyError` on a hand-edited document.
"""
from __future__ import annotations

import pytest

from ossie_holistics import ossie_to_aml
from ossie_holistics.errors import ConversionError

VERSION = "0.2.0.dev0"
EXPRESSION = {"dialects": [{"dialect": "ANSI_SQL", "expression": "x"}]}


def document(**overrides):
    base = {"version": VERSION, "name": "m", "datasets": [{"name": "d", "source": "s"}]}
    base.update(overrides)
    return base


MALFORMED = {
    "datasets is a mapping": document(datasets={"a": {}}),
    "datasets is a string": document(datasets="nope"),
    "datasets is empty": document(datasets=[]),
    "datasets is absent": {"version": VERSION, "name": "m"},
    "a dataset is a string": document(datasets=["nope"]),
    "a dataset has no name": document(datasets=[{"source": "s"}]),
    "a dataset name is not a string": document(datasets=[{"name": 7, "source": "s"}]),
    "fields is a mapping": document(datasets=[{"name": "d", "source": "s", "fields": {}}]),
    "a field has no name": document(
        datasets=[{"name": "d", "source": "s", "fields": [{"expression": EXPRESSION}]}]
    ),
    "a metric has no name": document(metrics=[{"expression": EXPRESSION}]),
    "a metric is a string": document(metrics=["nope"]),
    "the document is a list": [],
    "the document has no name": {"version": VERSION, "datasets": [{"name": "d", "source": "s"}]},
    "an expression is a string": document(
        datasets=[{"name": "d", "source": "s", "fields": [{"name": "x", "expression": "SUM(x)"}]}]
    ),
    "a dialect entry is a string": document(
        datasets=[
            {
                "name": "d",
                "source": "s",
                "fields": [{"name": "x", "expression": {"dialects": ["ANSI_SQL"]}}],
            }
        ]
    ),
    "dialects is a string": document(
        datasets=[
            {
                "name": "d",
                "source": "s",
                "fields": [{"name": "x", "expression": {"dialects": "ANSI_SQL"}}],
            }
        ]
    ),
}


def joined(from_columns, to_columns):
    """Two datasets and one relationship pairing the column lists given."""
    column = {"name": "id", "expression": EXPRESSION}
    return document(
        datasets=[
            {"name": "orders", "source": "db.orders", "fields": [column]},
            {"name": "users", "source": "db.users", "fields": [column]},
        ],
        relationships=[
            {
                "name": "r",
                "from": "orders",
                "to": "users",
                "from_columns": from_columns,
                "to_columns": to_columns,
            }
        ],
    )


@pytest.mark.parametrize("payload", MALFORMED.values(), ids=list(MALFORMED))
def test_a_malformed_document_raises_conversion_error(payload):
    with pytest.raises(ConversionError):
        ossie_to_aml.convert(payload, "ANSI_SQL", data_source_name="probe")


@pytest.mark.parametrize(
    "from_columns,to_columns",
    [([], []), (["id"], []), ([], ["id"]), (["a", "b", "c"], ["a", "b"])],
    ids=["both empty", "no to_columns", "no from_columns", "unequal lengths"],
)
def test_relationship_columns_must_pair_up(from_columns, to_columns):
    """AML joins a column to a column, so the two lists have to line up.

    An unequal pair has no reading: a three-column key against two target
    columns would join on a condition nobody wrote.
    """
    with pytest.raises(ConversionError, match="from_columns"):
        ossie_to_aml.convert(
            joined(from_columns, to_columns), "ANSI_SQL", data_source_name="probe"
        )
