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

"""Shape-based round-trip properties for the ThoughtSpot <-> Ossie converter.

The package's existing hypothesis properties generate NAMES (adversarial
unicode, display-name collisions, warehouse names). Every structural defect
found in review was a SHAPE: a table aliased to itself, two datasets sharing a
SQL View, a parenthesised join condition, case-colliding document names. A name
generator cannot reach any of them.

The invariant asserted here is the package's own stated contract, which is
generic enough to hold over any shape: NOTHING IS SILENTLY DROPPED. Every field
present in the input must appear in the output, or an issue must name it.

Run: .venv/bin/python -m pytest <this file> -q
"""
import json

import pytest


from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ossie_thoughtspot import ossie_to_thoughtspot, tml, tml_to_ossie
from ossie_thoughtspot.constants import DIALECT

_SETTINGS = settings(
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# Identifiers are deliberately BORING here: names are the other properties' job,
# and letting them vary would re-find name defects instead of shape ones.
_ident = st.sampled_from(["alpha", "beta", "gamma", "delta", "epsilon"])


def _stash(**payload):
    return [{"vendor_name": "THOUGHTSPOT", "data": json.dumps({"_v": 1, **payload})}]


@st.composite
def _documents(draw):
    """An Ossie document whose SHAPE varies: dataset count, table vs SQL view,
    aliasing onto a shared source, relationships, and already-aggregated metrics.
    """
    n_datasets = draw(st.integers(min_value=1, max_value=4))
    # How many distinct warehouse objects the datasets sit on. Fewer than
    # n_datasets means aliasing -- a self-join, or two aliases over one SQL View.
    n_sources = draw(st.integers(min_value=1, max_value=n_datasets))
    kinds = draw(st.lists(st.sampled_from(["table", "sql_view"]),
                          min_size=n_sources, max_size=n_sources))

    datasets, field_names = [], []
    for index in range(n_datasets):
        source_index = index % n_sources
        kind = kinds[source_index]
        # Case-varying source names: on a case-insensitive filesystem two of
        # these are ONE file, which is the defect the casefold fix addressed.
        case_style = draw(st.sampled_from(["upper", "lower", "mixed"]))
        base = f"SRC_{source_index}"
        source_name = {"upper": base.upper(), "lower": base.lower(),
                       "mixed": base.capitalize()}[case_style]
        dataset_name = f"ds_{index}"
        column = f"{draw(_ident)}_{index}"
        field_names.append((dataset_name, column))

        payload = {"connection_name": "C", "tml_name": source_name, "alias": dataset_name}
        if kind == "sql_view":
            payload["sql_output_columns"] = {column: column}
            source = "SELECT 1"
        else:
            source = f"DB.SCH.{source_name}"

        datasets.append({
            "name": dataset_name,
            "source": source,
            "fields": [{
                "name": column,
                "expression": {"dialects": [
                    {"dialect": DIALECT, "expression": f"[{dataset_name}::{column}]"}
                ]},
            }],
            "custom_extensions": _stash(**payload),
        })

    document = {"version": "0.2.0.dev0", "name": "m", "datasets": datasets}

    # Relationships between consecutive datasets, sometimes composite.
    if n_datasets > 1 and draw(st.booleans()):
        relationships = []
        for index in range(n_datasets - 1):
            from_ds, from_col = field_names[index]
            to_ds, to_col = field_names[index + 1]
            composite = draw(st.booleans())
            relationships.append({
                "name": f"rel_{index}",
                "from": from_ds, "to": to_ds,
                "from_columns": [from_col] * (2 if composite else 1),
                "to_columns": [to_col] * (2 if composite else 1),
            })
        document["relationships"] = relationships

    # An already-aggregated metric, the shape that caused the double-aggregation.
    if draw(st.booleans()):
        ds_name, col = field_names[0]
        aggregate = draw(st.sampled_from(["group_sum", "sum", "group_aggregate"]))
        inner = f"[{ds_name}::{col}]"
        expression = (
            f"group_aggregate ( sum ( {inner} ) , query_groups ( ) , query_filters ( ) )"
            if aggregate == "group_aggregate"
            else f"{aggregate} ( {inner} )"
        )
        document["metrics"] = [{
            "name": "m_0",
            "expression": {"dialects": [{"dialect": DIALECT, "expression": expression}]},
        }]

    return document


def _issue_text(log):
    return " ".join(issue["message"] for issue in log.as_dicts())


class TestNothingIsSilentlyDropped:
    """Ossie -> TML -> Ossie must preserve every field, or say which it lost."""

    @_SETTINGS
    @given(document=_documents())
    def test_every_field_survives_or_is_named_in_an_issue(self, document):
        try:
            forward = ossie_to_thoughtspot.convert(document)
        except Exception as exc:  # a crash is itself a finding
            pytest.fail(f"to-tml raised {type(exc).__name__}: {exc}\n{json.dumps(document, indent=2)}")

        back = tml_to_ossie.convert(forward.documents)
        surviving = {
            field["name"]
            for dataset in back.model.get("datasets") or []
            for field in dataset.get("fields") or []
        }
        reported = _issue_text(forward.issues) + " " + _issue_text(back.issues)

        lost = [
            column
            for dataset in document["datasets"]
            for field in dataset["fields"]
            for column in [field["name"]]
            if column not in surviving and column not in reported
        ]
        assert not lost, (
            f"fields vanished with no issue naming them: {lost}\n"
            f"document:\n{json.dumps(document, indent=2)}"
        )

    @_SETTINGS
    @given(document=_documents())
    def test_no_two_emitted_documents_share_a_filename(self, document):
        result = ossie_to_thoughtspot.convert(document)
        names = [name for name, _ in tml.dump_document_set(result.documents)]
        folded = [name.casefold() for name in names]
        assert len(folded) == len(set(folded)), (
            f"two documents would be written to one path: {names}\n"
            f"document:\n{json.dumps(document, indent=2)}"
        )

    @_SETTINGS
    @given(document=_documents())
    def test_a_declared_relationship_survives_or_is_reported(self, document):
        relationships = document.get("relationships") or []
        if not relationships:
            return
        forward = ossie_to_thoughtspot.convert(document)
        back = tml_to_ossie.convert(forward.documents)
        # Compared by STRUCTURE, not by name: a TML join carries no name of its
        # own, so the return leg legitimately re-derives `<from>_to_<to>`. (The
        # Ossie-authored name IS silently lost for an inline join -- a separate,
        # smaller finding -- but that is not what this property is about.)
        surviving = {
            (r.get("from"), r.get("to"), tuple(r.get("from_columns") or []),
             tuple(r.get("to_columns") or []))
            for r in back.model.get("relationships") or []
        }
        reported = _issue_text(forward.issues) + " " + _issue_text(back.issues)
        lost = [
            r["name"] for r in relationships
            if (r["from"], r["to"], tuple(r["from_columns"]), tuple(r["to_columns"])) not in surviving
            and r["name"] not in reported
            and r["from"] not in reported
        ]
        assert not lost, (
            f"relationships vanished with no issue naming them: {lost}\n"
            f"document:\n{json.dumps(document, indent=2)}"
        )
