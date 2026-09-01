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

"""custom_extensions[THOUGHTSPOT] payload handling — rules X1-X9.

The stash lives in the *Ossie* document, so it is written on the way in and read
on the way out. Rule X9 follows from that: it can only carry what TML contains.
"""
import json
from typing import Any

from .constants import STASH_VERSION, VENDOR_KEY
from .errors import ConversionError

#: X8 — instance-local identity never travels in a portable document.
_FORBIDDEN_KEYS = frozenset({"guid", "obj_id", "fqn"})


def _object_label(obj: dict) -> str:
    return str(obj.get("name", "<unnamed>"))


def read_stash(obj: dict) -> dict[str, Any]:
    """Return this object's parsed THOUGHTSPOT payload, or {} if it has none."""
    for entry in obj.get("custom_extensions") or []:
        if entry.get("vendor_name") != VENDOR_KEY:
            continue
        raw = entry.get("data")
        if raw is None:
            return {}
        if not isinstance(raw, str):
            # X2: `data` is typed as a string; a nested object is a spec violation.
            raise ConversionError(
                f"custom_extensions data for {_object_label(obj)!r} is "
                f"{type(raw).__name__}, expected a JSON string"
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # X4: name the object; never surface a bare json traceback.
            raise ConversionError(
                f"malformed THOUGHTSPOT custom_extensions payload on "
                f"{_object_label(obj)!r}: {exc}"
            ) from exc
    return {}


def write_stash(obj: dict, payload: dict[str, Any]) -> dict:
    """Merge `payload` into this object's THOUGHTSPOT entry, returning a new dict.

    Foreign-vendor entries are preserved untouched (X7). An empty resulting
    payload writes nothing at all (X6).
    """
    forbidden = _FORBIDDEN_KEYS & set(payload)
    if forbidden:
        # X8.
        raise ConversionError(
            f"refusing to stash instance-local identity key(s) "
            f"{sorted(forbidden)} on {_object_label(obj)!r}"
        )

    merged = {**read_stash(obj), **payload}
    if not merged:
        return dict(obj)

    merged["_v"] = STASH_VERSION  # X3
    others = [e for e in obj.get("custom_extensions") or [] if e.get("vendor_name") != VENDOR_KEY]
    out = dict(obj)
    # X1: exactly one own entry, merged rather than appended.
    out["custom_extensions"] = [
        *others,
        {"vendor_name": VENDOR_KEY, "data": json.dumps(merged, sort_keys=True)},
    ]
    return out


def restore(
    payload: dict[str, Any],
    key: str,
    derived: Any,
    *,
    witness: Any = None,
    witness_key: str | None = None,
) -> Any:
    """Rule X5 — stash-if-present-and-still-current-else-derive.

    `witness` is the live Ossie value and `witness_key` names the copy recorded
    alongside the stashed value. When they disagree the Ossie document has been
    edited since the stash was written, so the stash is stale for this key and
    `derived` wins. Without a witness this degrades to stash-if-present, which is
    correct only for values nothing downstream can edit.
    """
    if key not in payload:
        return derived
    if witness_key is not None and payload.get(witness_key) != witness:
        return derived
    return payload[key]
