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

"""The `HOLISTICS` entry in `custom_extensions`.

Every AML property the core specification has no field for travels here. The
entry's `data` is a single JSON-encoded string, never a nested object, because
the specification requires that:

    custom_extensions:
    - vendor_name: HOLISTICS
      data: '{"_v": 1, "aml_type": "TableModel", "table_name": "`ecommerce`.`orders`"}'

`_v` is the shape version. `read` fails on a version it does not know rather
than misreading a shape it has never seen.

A foreign vendor's entry on the same object passes through untouched, and an
object with nothing to stash gets no `HOLISTICS` entry at all.
"""
from __future__ import annotations

import json
from typing import Any

from .errors import ConversionError

VENDOR = "HOLISTICS"

#: The only shape this converter writes, and the only one it reads.
VERSION = 1


def entry(data: dict[str, Any]) -> dict[str, str] | None:
    """One `custom_extensions` entry, or None when there is nothing to carry."""
    if not data:
        return None
    payload = {"_v": VERSION}
    payload.update(data)
    return {"vendor_name": VENDOR, "data": json.dumps(payload, sort_keys=True)}


def attach(target: dict[str, Any], data: dict[str, Any]) -> None:
    """Append the `HOLISTICS` entry to `target['custom_extensions']`, if any."""
    stashed = entry(data)
    if stashed is None:
        return
    target.setdefault("custom_extensions", []).append(stashed)


def read(obj: Any) -> dict[str, Any]:
    """The stashed payload on an Ossie object, `{}` when there is none.

    `_v` is stripped from the result, so a caller reads AML properties by their
    own names and never sees the bookkeeping key.
    """
    if not isinstance(obj, dict):
        return {}
    extensions = obj.get("custom_extensions")
    if not isinstance(extensions, list):
        return {}
    for extension in extensions:
        if not isinstance(extension, dict) or extension.get("vendor_name") != VENDOR:
            continue
        raw = extension.get("data")
        if not isinstance(raw, str):
            raise ConversionError(f"{VENDOR} custom_extensions data is not a string")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConversionError(f"{VENDOR} custom_extensions data is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConversionError(f"{VENDOR} custom_extensions data is not a JSON object")
        version = payload.pop("_v", None)
        if version != VERSION:
            raise ConversionError(
                f"{VENDOR} custom_extensions data has _v={version!r}, "
                f"and this converter writes and reads _v={VERSION}"
            )
        return payload
    return {}
