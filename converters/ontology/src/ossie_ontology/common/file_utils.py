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

import io
import warnings
import zipfile
from pathlib import Path
from typing import Iterable


def iter_json_files_from_dir_in_zip(zf: zipfile.ZipFile, dir_prefix: str) -> Iterable[tuple[str, io.IOBase]]:
    names = zf.namelist()
    base_prefix = dir_prefix.rstrip("/") + "/"
    roots = {n.split("/", 1)[0] for n in names if "/" in n}
    candidate_prefixes = [base_prefix]
    if len(roots) == 1:
        root = next(iter(roots))
        candidate_prefixes.append(f"{root}/{base_prefix}")

    seen = set()
    for name in names:
        if name.endswith("/") or not name.lower().endswith(".json"):
            continue
        if any(name.startswith(p) for p in candidate_prefixes):
            if name in seen:
                continue
            seen.add(name)
            with zf.open(name, "r") as fp:
                yield name, io.BytesIO(fp.read())

def open_top_level_file_from_zip(zf: zipfile.ZipFile, filename: str) -> io.IOBase:
    names = set(zf.namelist())

    def _open_to_io_base(n: str) -> io.IOBase:
        with zf.open(n, "r") as fp:
            return io.BytesIO(fp.read())

    if filename in names:
        return _open_to_io_base(filename)

    roots = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(roots) == 1:
        candidate = f"{next(iter(roots))}/{filename}"
        if candidate in names:
            return _open_to_io_base(candidate)

    raise FileNotFoundError(f"Missing required top-level file: {filename}")


# The directory helpers below mirror the ZIP helpers above, but operate on an
# extracted folder rather than a ZIP archive. Like the ZIP variants, they accept
# either a folder whose top level directly contains the Palantir files, or a
# folder wrapping a single root directory that contains them.

def _resolve_dir_root(base_dir: Path) -> Path:
    """Return the directory that holds the top-level Palantir files.

    Either ``base_dir`` itself, or its single child directory when the export
    was extracted under one wrapping folder (mirrors the single-root handling
    used for ZIP archives).
    """
    entries = list(base_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return base_dir


def iter_json_files_from_dir(base_dir: Path, dir_prefix: str) -> Iterable[tuple[str, io.IOBase]]:
    root = _resolve_dir_root(base_dir)
    data_dir = root / dir_prefix.rstrip("/")
    if not data_dir.is_dir():
        return
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".json":
            # Yield a stable, root-relative name (posix separators) to mirror the
            # ZIP variant and avoid leaking host-specific absolute paths.
            name = path.relative_to(root).as_posix()
            with path.open("rb") as fp:
                yield name, io.BytesIO(fp.read())


def get_top_level_json_file_from_dir(base_dir: Path) -> Path:
    root = _resolve_dir_root(base_dir)
    candidates = [
        p for p in sorted(root.iterdir())
        if p.is_file() and p.suffix.lower() == ".json"
    ]
    if len(candidates) == 0:
        raise ValueError("Directory must contain exactly one top-level JSON file (none found)")
    if len(candidates) > 1:
        raise ValueError("Directory must contain exactly one top-level JSON file (multiple found)")
    return candidates[0]


def validate_dir(base_dir: Path) -> None:
    """Warn when the extracted folder carries no 'data_sets/' directory.

    Not an error. Object types, properties, keys and links all come from the
    ontology JSON, so the ontology that results is structurally complete and
    valid on its own — SDK-extracted exports routinely ship this way.

    What is lost is the weaving: datasets are what tie each concept back to the
    table behind it, so without them the model has no mappings to any source.
    It will build and type-check, and every query against it will return no
    rows. Supply the datasets (or add the mappings by hand) before pointing it
    at data.
    """
    root = _resolve_dir_root(base_dir)
    if not (root / "data_sets").is_dir():
        warnings.warn(
            "Directory contains no 'data_sets' folder. The ontology will be built "
            "from the ontology JSON alone: valid, but with no mappings connecting "
            "its concepts to source tables, so queries against it return nothing.",
            stacklevel=2,
        )
