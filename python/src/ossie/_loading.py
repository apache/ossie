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

from collections.abc import Hashable
from typing import Any

import yaml
from yaml.constructor import ConstructorError


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate explicit keys, preserving YAML merge overrides."""

    def construct_document(self, node: yaml.Node):
        # Inspect authored mappings before SafeLoader flattens merges, which
        # mutates shared alias nodes in place.
        self._check_unique_keys(node, set())
        return super().construct_document(node)

    def _check_unique_keys(self, node: yaml.Node, visited: set[int]) -> None:
        if id(node) in visited:
            return
        visited.add(id(node))

        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    # Repeated merge directives are allowed. Check their sources
                    # for explicit duplicates, then let SafeLoader expand them.
                    self._check_unique_keys(value_node, visited)
                    continue
                elif key_node.tag == "tag:yaml.org,2002:value":
                    # SafeLoader treats "=" keys as strings while flattening.
                    # Read the scalar without changing the shared node's tag.
                    key = display_key = self.construct_scalar(key_node)
                elif isinstance(key_node, yaml.ScalarNode):
                    key = display_key = self.construct_object(key_node, deep=True)
                else:
                    # Constructing a collection key could flatten a merge before
                    # its duplicates are checked. SafeLoader rejects these too.
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "found an unhashable key",
                        key_node.start_mark,
                    )
                if not isinstance(key, Hashable):
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "found an unhashable key",
                        key_node.start_mark,
                    )
                if key in seen:
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {display_key!r}",
                        key_node.start_mark,
                    )
                seen.add(key)
                self._check_unique_keys(key_node, visited)
                self._check_unique_keys(value_node, visited)
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                self._check_unique_keys(child, visited)


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object without silently discarding an earlier property."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"document contains duplicate key {key!r}")
        result[key] = value
    return result
