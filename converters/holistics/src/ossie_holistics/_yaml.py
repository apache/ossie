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
resolve to booleans. An AML identifier may be any of them, so a bare
`yaml.safe_load` corrupts them silently. Only the boolean resolver is
narrowed here. No other YAML 1.1/1.2 divergence (e.g. octal/sexagesimal
number parsing) is addressed.

Both directions matter. The loader stops 1.1 bool tokens becoming booleans. The
dumper quotes them on the way out, so the next reader cannot re-resolve them
even if it implements 1.1.
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
    bare `yaml.YAMLError` traceback. `stash.py` holds the same contract for
    malformed `custom_extensions` JSON.
    """
    try:
        return yaml.load(text, Loader=Yaml12Loader)
    except yaml.YAMLError as exc:
        raise ConversionError(f"malformed YAML: {exc}") from exc


def dump(data: object) -> str:
    """Serialise to YAML, preserving insertion order and quoting 1.1 bool tokens.

    `allow_unicode=True` so a non-ASCII value (e.g. a display label) emits as
    a literal character rather than a `\\xXX`/`\\uXXXX` escape. Ossie
    documents are read by people.
    """
    return yaml.dump(
        data,
        Dumper=Yaml12Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


#: Width to fold an issue message at. Wide enough to keep a line meaningful,
#: narrow enough to read in a side-by-side diff.
ISSUE_WIDTH = 96


class IssueDumper(Yaml12Dumper):
    """Dumper that folds a long message instead of running it off the screen."""


def _represent_issue_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Fold a long single-line string, quote a YAML 1.1 bool token, else plain.

    Folded style (`>-`) breaks only at spaces, so a URL stays on one line, and a
    reader joins the lines back with spaces. `load` returns the original string,
    which `tests/test_snapshots.py` asserts.
    """
    if data in _YAML11_ONLY_BOOLS:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    style = ">" if len(data) > ISSUE_WIDTH and "\n" not in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


IssueDumper.add_representer(str, _represent_issue_str)


def dump_issues(issues: list[dict[str, object]]) -> str:
    """The issue log as YAML, one block per issue, long messages folded.

    JSON put each message on one unbroken line, which a reviewer had to scroll
    sideways to read. The same log as folded YAML fits a diff.
    """
    if not issues:
        return "[]\n"
    return yaml.dump(
        issues,
        Dumper=IssueDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=ISSUE_WIDTH,
    )
