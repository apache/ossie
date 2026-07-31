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

from __future__ import annotations

from typing import Any

from ..hex_types import HexModel, HexResource, HexView, parse_hex_resource
from ..util.yaml import dump_yaml


def hex_resource_to_yaml(resource: HexResource | dict[str, Any]) -> str:
    """Serialize a Hex resource to YAML text."""
    parsed_resource = (
        resource
        if isinstance(resource, (HexModel, HexView))
        else parse_hex_resource(resource)
    )
    data = parsed_resource.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
        exclude_unset=True,
    )
    if isinstance(parsed_resource, HexView):
        # only for View, type is optional for Model
        data = {"id": data.pop("id"), "type": "view", **data}
    return dump_yaml(_compact_hex_resource(data))


def _compact_hex_resource(data: dict[str, Any]) -> dict[str, Any]:
    """Omit derived defaults for cleaner Hex YAML output."""
    out = dict(data)
    dims = out.get("dimensions")
    if isinstance(dims, list):
        out["dimensions"] = [_compact_dimension(d) for d in dims]
    relations = out.get("relations")
    if isinstance(relations, list):
        out["relations"] = [_compact_relation(r) for r in relations]
    return out


def _compact_dimension(dim: dict[str, Any]) -> dict[str, Any]:
    out = dict(dim)
    if out.get("expr_sql") == out.get("id") and not out.get("expr_calc"):
        out.pop("expr_sql", None)
    return out


def _compact_relation(relation: dict[str, Any]) -> dict[str, Any]:
    out = dict(relation)
    if out.get("target") == out.get("id"):
        out.pop("target", None)
    return out
