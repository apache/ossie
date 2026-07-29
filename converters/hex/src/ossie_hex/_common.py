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

"""Shared helpers for the Apache Ossie <-> Hex converters."""

from __future__ import annotations

import re
from typing import Any

import yaml

# region: Conversion errors, warnings


class ConversionError(Exception):
    """Raised when an input cannot be converted."""


class ConversionWarning:
    """A non-fatal conversion issue reported to the caller / stderr."""

    def __init__(self, message: str) -> None:
        self.message = message

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"ConversionWarning({self.message!r})"


# endregion

# region: Upgrading PyYAML's default YAML 1.1 semantics to YAML 1.2
# preserve boolean-adjacent strings as strings. only treat true/false as booleans.


class _Yaml12Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 boolean semantics."""


class _Yaml12Dumper(yaml.SafeDumper):
    """SafeDumper with YAML 1.2 boolean semantics."""


_YAML12_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
for _cls in (_Yaml12Loader, _Yaml12Dumper):
    _cls.yaml_implicit_resolvers = {
        ch: [(tag, rx) for (tag, rx) in resolvers if tag != "tag:yaml.org,2002:bool"]
        for ch, resolvers in _cls.yaml_implicit_resolvers.items()
    }
    _cls.add_implicit_resolver("tag:yaml.org,2002:bool", _YAML12_BOOL, list("tTfF"))


_YAML11_BOOL_STRS = frozenset(
    variant
    for word in ("y", "n", "yes", "no", "on", "off", "true", "false")
    for variant in (word, word.capitalize(), word.upper())
)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> Any:
    style = "'" if data in _YAML11_BOOL_STRS else None
    if "\n" in data:
        style = "|"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Yaml12Dumper.add_representer(str, _represent_str)


def load_yaml(text: str, what: str = "input") -> Any:
    """Parse a single YAML document with 1.2 boolean semantics."""
    try:
        return yaml.load(text, Loader=_Yaml12Loader)
    except yaml.YAMLError as e:
        raise ConversionError(f"Invalid YAML in {what}: {e}") from e


def load_yaml_all(text: str, what: str = "input") -> list[Any]:
    """Parse all YAML documents in a multi-document stream."""
    try:
        docs = list(yaml.load_all(text, Loader=_Yaml12Loader))
    except yaml.YAMLError as e:
        raise ConversionError(f"Invalid YAML in {what}: {e}") from e
    return [doc for doc in docs if doc is not None]


def dump_yaml(obj: Any) -> str:
    """Serialize to YAML with 1.2 boolean semantics."""
    return yaml.dump(
        obj,
        Dumper=_Yaml12Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


# endregion
