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

"""custom_extensions[THOUGHTSPOT] payload handling.

The stash lives in the *Ossie* document, so it is written on the way in and read
on the way out. It follows that the stash can only carry what TML contains.
"""
import json
from typing import Any

from .constants import STASH_VERSION, VENDOR_KEY
from .errors import ConversionError

#: Instance-local identity never travels in a portable document.
_FORBIDDEN_KEYS = frozenset({"guid", "obj_id", "fqn"})


def _object_label(obj: dict) -> str:
    return str(obj.get("name", "<unnamed>"))


def find_forbidden_key(value: Any, forbidden: frozenset[str] | None = None) -> str | None:
    """The first key from `forbidden` found anywhere inside `value`, at any
    depth, or `None`.

    `forbidden` defaults to `_FORBIDDEN_KEYS` (`guid`/`obj_id`/`fqn`). A caller
    with a wider identity vocabulary to check for — this
    package's own `dataset_id`/`custom_file_guid` additions, documented
    identity-shaped keys the default set does not name — passes its own set rather
    than this module maintaining a second, wider copy of its own; the scan
    itself is shared either way, so the two vocabularies cannot drift apart
    the way two independently maintained scans could.

    `write_stash` is the single point every stashed payload passes through,
    so this is the one place the check needs to live for no caller — present
    or future — to bypass it by nesting identity content one level below a
    payload's own top-level keys instead of putting it there directly. A
    value copied wholesale from source data, rather than rebuilt field by
    field, is exactly how that happens in practice — the documented
    ThoughtSpot shape `geo_config.custom_file_guid` naming a custom map is
    one real example.
    """
    names = forbidden if forbidden is not None else _FORBIDDEN_KEYS
    if isinstance(value, dict):
        for key, v in value.items():
            if key in names:
                return key
            found = find_forbidden_key(v, names)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = find_forbidden_key(item, names)
            if found is not None:
                return found
        return None
    return None


def read_stash(obj: dict) -> dict[str, Any]:
    """Return this object's parsed THOUGHTSPOT payload, or {} if it has none."""
    for entry in obj.get("custom_extensions") or []:
        if entry.get("vendor_name") != VENDOR_KEY:
            continue
        raw = entry.get("data")
        if raw is None:
            return {}
        if not isinstance(raw, str):
            # `data` is typed as a string; a nested object is a spec violation.
            raise ConversionError(
                f"custom_extensions data for {_object_label(obj)!r} is "
                f"{type(raw).__name__}, expected a JSON string"
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Name the object; never surface a bare json traceback.
            raise ConversionError(
                f"malformed THOUGHTSPOT custom_extensions payload on "
                f"{_object_label(obj)!r}: {exc}"
            ) from exc
        version = parsed.get("_v") if isinstance(parsed, dict) else None
        if version != STASH_VERSION:
            # An unrecognised shape version is a hard failure, not a
            # partial read — a future payload shape this converter has never
            # seen would otherwise be silently misread as the current one.
            raise ConversionError(
                f"THOUGHTSPOT custom_extensions payload on "
                f"{_object_label(obj)!r} has shape version {version!r}, "
                f"which this converter does not recognise (expected "
                f"{STASH_VERSION!r})"
            )
        return parsed
    return {}


def write_stash(obj: dict, payload: dict[str, Any]) -> dict:
    """Merge `payload` into this object's THOUGHTSPOT entry, returning a new dict.

    Foreign-vendor entries are preserved untouched. An empty resulting
    payload writes nothing at all.
    """
    forbidden_key = find_forbidden_key(payload)
    if forbidden_key is not None:
        # Checked at any depth — see find_forbidden_key.
        raise ConversionError(
            f"refusing to stash instance-local identity key {forbidden_key!r} "
            f"on {_object_label(obj)!r}"
        )

    merged = {**read_stash(obj), **payload}
    if not merged:
        return dict(obj)

    merged["_v"] = STASH_VERSION  # stamp the shape version so a future reader can recognise it
    others = [e for e in obj.get("custom_extensions") or [] if e.get("vendor_name") != VENDOR_KEY]
    out = dict(obj)
    # Exactly one own entry, merged rather than appended.
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
    """Stash-if-present-and-still-current-else-derive.

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
