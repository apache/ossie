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

"""YAML codec with the boolean resolver narrowed to YAML 1.2.

PyYAML implements YAML 1.1, in which `on`, `off`, `yes`, `no`, `y` and `n`
resolve to booleans. TML uses such tokens as ordinary strings, so a bare
`yaml.safe_load` corrupts them silently. Only the boolean resolver is
narrowed here — no other YAML 1.1/1.2 divergence (e.g. octal/sexagesimal
number parsing) is addressed.

Both directions matter. The loader stops 1.1 bool tokens becoming booleans; the
dumper quotes them on the way out so the next reader — which may be a 1.1
implementation — cannot re-resolve them.
"""
import re

import yaml

from .errors import ConversionError

#: YAML 1.2 core schema: only these spellings are booleans.
_YAML12_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")

#: Bare scalars YAML 1.1 resolves as booleans and YAML 1.2 does not.
_YAML11_ONLY_BOOLS = frozenset(
    {"y", "Y", "yes", "Yes", "YES", "n", "N", "no", "No", "NO",
     "on", "On", "ON", "off", "Off", "OFF"}
)


class Yaml12Loader(yaml.SafeLoader):
    """SafeLoader with the YAML 1.1 boolean resolver narrowed to the 1.2 set."""


# Drop the inherited bool resolver outright, then reinstate the 1.2-only one.
# Mutating in place would affect SafeLoader itself, so rebuild the mapping.
Yaml12Loader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:bool", _YAML12_BOOL, list("tTfF"))


class Yaml12Dumper(yaml.SafeDumper):
    """SafeDumper that quotes strings a YAML 1.1 reader would take for booleans."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "'" if data in _YAML11_ONLY_BOOLS else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


Yaml12Dumper.add_representer(str, _represent_str)


def load(text: str) -> object:
    """Parse YAML text under YAML 1.2 boolean rules.

    A malformed document raises `ConversionError` naming the failure, never a
    bare `yaml.YAMLError` traceback — the same never-a-bare-traceback contract
    `stash.py` (rule X4) holds for malformed `custom_extensions` JSON.
    """
    try:
        return yaml.load(text, Loader=Yaml12Loader)
    except yaml.YAMLError as exc:
        raise ConversionError(f"malformed YAML: {exc}") from exc


def dump(data: object) -> str:
    """Serialise to YAML, preserving insertion order and quoting 1.1 bool tokens.

    `allow_unicode=True` so a non-ASCII value (e.g. a display label) emits as
    a literal character rather than a `\\xXX`/`\\uXXXX` escape — Ossie
    documents are human-read YAML.
    """
    return yaml.dump(
        data,
        Dumper=Yaml12Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
