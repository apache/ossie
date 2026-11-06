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
from ossie import OssieSemanticModel

_YAML_MODEL = """\
version: 0.2.0.dev0
name: sales
datasets:
  - name: orders
    source: production.orders
"""


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (_YAML_MODEL + "name: other\n", "name"),
        (_YAML_MODEL + "    source: staging.orders\n", "source"),
        (
            _YAML_MODEL.replace(
                "source: production.orders", "source: []\n    source: production.orders"
            ),
            "source",
        ),
        (_YAML_MODEL + '    "source": staging.orders\n', "source"),
        (_YAML_MODEL + "    !!str source: staging.orders\n", "source"),
        (
            _YAML_MODEL
            + "datasets: [{name: customers, source: production.customers}]\n",
            "datasets",
        ),
        (_YAML_MODEL + "ai_context: {note: first, note: second}\n", "note"),
        (_YAML_MODEL + "ai_context: {metadata: {yes: first, true: second}}\n", True),
        (_YAML_MODEL + "ai_context: {metadata: {1: first, 01: second}}\n", 1),
        (_YAML_MODEL + 'ai_context: {=: first, "=": second}\n', "="),
        (_YAML_MODEL + "ai_context: {<<: {note: first, note: second}}\n", "note"),
        (
            _YAML_MODEL + "ai_context: {<<: {note: first}, <<: {note: a, note: b}}\n",
            "note",
        ),
        (
            _YAML_MODEL + "ai_context: {<<: {note: a, note: b}, <<: {note: last}}\n",
            "note",
        ),
        (
            _YAML_MODEL + "ai_context: {note: first, <<: {}, <<: {}, note: last}\n",
            "note",
        ),
        (
            _YAML_MODEL + 'ai_context: {<<: {}, "<<": first, !!str <<: last}\n',
            "<<",
        ),
        (
            _YAML_MODEL
            + """\
metrics:
  - name: revenue
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(orders.amount)
          expression: AVG(orders.amount)
""",
            "expression",
        ),
    ],
)
def test_yaml_rejects_duplicate_keys(text: str, key: str | bool | int) -> None:
    with pytest.raises(
        yaml.constructor.ConstructorError, match="duplicate key"
    ) as error:
        OssieSemanticModel.from_ossie_yaml(text)

    assert f"duplicate key {key!r}" in str(error.value)
    assert error.value.context_mark is not None
    assert error.value.problem_mark is not None


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (
            (
                '{"version":"0.2.0.dev0","name":"sales","name":"other",'
                '"datasets":[{"name":"orders","source":"orders"}]}'
            ),
            "name",
        ),
        (
            (
                '{"version":"0.2.0.dev0","name":"sales","datasets":['
                '{"name":"orders","source":"production.orders",'
                '"source":"staging.orders"}]}'
            ),
            "source",
        ),
        (
            (
                '{"version":"0.2.0.dev0","name":"sales","datasets":['
                '{"name":"orders","source":[],"source":"orders"}]}'
            ),
            "source",
        ),
        (
            (
                '{"version":"0.2.0.dev0","name":"sales","datasets":['
                '{"name":"orders","source":"orders","\\u0073ource":"other"}]}'
            ),
            "source",
        ),
        (
            (
                '{"version":"0.2.0.dev0","name":"sales","datasets":[],'
                '"datasets":[{"name":"orders","source":"orders"}]}'
            ),
            "datasets",
        ),
        (
            (
                '{"version":"0.2.0.dev0","name":"sales",'
                '"datasets":[{"name":"orders","source":"orders"}],'
                '"ai_context":{"metadata":{"note":1,"note":2}}}'
            ),
            "note",
        ),
        (
            (
                '{"version":"0.2.0.dev0","name":"sales",'
                '"datasets":[{"name":"orders","source":"orders"}],'
                '"metrics":[{"name":"revenue","expression":{"dialects":['
                '{"dialect":"ANSI_SQL","expression":"SUM(orders.amount)",'
                '"expression":"AVG(orders.amount)"}]}}]}'
            ),
            "expression",
        ),
    ],
)
@pytest.mark.parametrize("format_name", ["json", "yaml"])
def test_json_rejects_duplicate_keys(text: str, key: str, format_name: str) -> None:
    # JSON is also accepted by the YAML entry point.
    parse = getattr(OssieSemanticModel, f"from_ossie_{format_name}")
    with pytest.raises(
        (ValueError, yaml.constructor.ConstructorError), match="duplicate key"
    ) as error:
        parse(text)

    assert key in str(error.value)


def test_yaml_duplicate_key_diagnostic_identifies_both_locations() -> None:
    with pytest.raises(yaml.constructor.ConstructorError) as error:
        OssieSemanticModel.from_ossie_yaml(_YAML_MODEL + "    source: staging.orders\n")

    assert error.value.context_mark.line == 3
    assert error.value.problem_mark.line == 5
    assert error.value.problem_mark.column == 4


def test_yaml_merge_overrides_and_reused_aliases_are_preserved() -> None:
    text = (
        _YAML_MODEL
        + """\
ai_context:
  first: &first {note: original, other: kept}
  second: &second {<<: *first, note: overridden}
  third: {<<: *second}
  fourth: *second
  sequence: {<<: [*second, *first]}
  literal: {<<: *first, "<<": literal}
  numeric: {"1": string, 1: number}
"""
    )
    document = OssieSemanticModel.from_ossie_yaml(text)
    metadata = document.ai_context.model_dump()

    assert metadata["first"] == {"note": "original", "other": "kept"}
    for name in ("second", "third", "fourth", "sequence"):
        assert metadata[name] == {"note": "overridden", "other": "kept"}
    assert metadata["literal"] == {"note": "original", "other": "kept", "<<": "literal"}
    assert metadata["numeric"] == {"1": "string", 1: "number"}


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ("{<<: {note: first}, <<: {note: second}}", "second"),
        ("{<<: [{note: first}, {note: second}]}", "first"),
        ("{note: local, <<: {note: first}, <<: {note: second}}", "local"),
        ("{<<: {note: first}, note: local, <<: {note: second}}", "local"),
        ("{<<: {note: first}, <<: {note: second}, note: local}", "local"),
    ],
)
def test_yaml_merge_precedence_is_preserved(mapping: str, expected: str) -> None:
    text = _YAML_MODEL + f"ai_context: {mapping}\n"

    document = OssieSemanticModel.from_ossie_yaml(text)

    assert document.ai_context.note == expected
    assert document.ai_context.model_dump(exclude_unset=True) == yaml.safe_load(text)[
        "ai_context"
    ]


def test_yaml_repeated_merge_aliases_are_preserved() -> None:
    text = _YAML_MODEL + """\
ai_context:
  first: &first {note: original, other: kept}
  second: &second {note: replaced}
  merged: &merged {<<: *first, <<: *second}
  reused: *merged
  sequence: {<<: [*second, *first]}
  literal: {<<: *first, <<: *second, "<<": literal}
"""
    document = OssieSemanticModel.from_ossie_yaml(text)

    assert document.ai_context.model_dump(exclude_unset=True) == yaml.safe_load(text)[
        "ai_context"
    ]
    for name in ("merged", "reused", "sequence"):
        assert getattr(document.ai_context, name) == {"note": "replaced", "other": "kept"}


@pytest.mark.parametrize("key", ["=", '!!value "="'])
def test_yaml_value_keys_are_preserved_in_mappings_and_merges(key: str) -> None:
    text = _YAML_MODEL + (
        f"ai_context:\n  original: &original {{{key}: first}}\n"
        f"  merged: {{<<: *original, {key}: second}}\n"
        "  reused: *original\n"
    )

    document = OssieSemanticModel.from_ossie_yaml(text)
    assert document.ai_context.original == {"=": "first"}
    assert document.ai_context.merged == {"=": "second"}
    assert document.ai_context.reused == {"=": "first"}


def test_same_key_in_separate_objects_is_preserved() -> None:
    data = {
        "version": "0.2.0.dev0",
        "name": "sales",
        "datasets": [
            {"name": "orders", "source": "production.orders"},
            {"name": "customers", "source": "production.customers"},
        ],
        "ai_context": {"first": {"note": 1}, "second": {"note": 2}},
    }
    for parse, text in (
        (OssieSemanticModel.from_ossie_json, json.dumps(data)),
        (OssieSemanticModel.from_ossie_yaml, yaml.safe_dump(data)),
    ):
        document = parse(text)
        assert json.loads(document.to_ossie_json()) == data


def test_json_entry_point_still_requires_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        OssieSemanticModel.from_ossie_json(_YAML_MODEL)


@pytest.mark.parametrize("key", ["[first, second]", "{first: second}"])
def test_yaml_collection_keys_still_raise_a_constructor_error(key: str) -> None:
    text = _YAML_MODEL + f"ai_context: {{{key}: value}}\n"

    with pytest.raises(yaml.constructor.ConstructorError, match="unhashable key"):
        OssieSemanticModel.from_ossie_yaml(text)


def test_json_number_and_boolean_types_are_preserved() -> None:
    text = (
        '{"version":"0.2.0.dev0","name":"sales",'
        '"datasets":[{"name":"orders","source":"orders"}],'
        '"ai_context":{"number":1e3,"flag":true,"missing":null}}'
    )

    document = OssieSemanticModel.from_ossie_json(text)
    assert document.ai_context.number == 1000.0
    assert isinstance(document.ai_context.number, float)
    assert document.ai_context.flag is True
    assert document.ai_context.missing is None
