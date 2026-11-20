#!/usr/bin/env bash
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

# Builds the Apache Ossie source release archive from a git ref, signs it and
# writes its SHA-512 checksum, ready to upload to Apache Trusted Releases (ATR).
# See dev/release/README.md for the full release process.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [-r <git-ref>] [-o <output-dir>] [-u <gpg-key>] [-s] <release>

Builds apache-ossie-<release>-src.tar.gz from <git-ref>, then writes its
detached signature (.asc) and SHA-512 checksum (.sha512).

  <release>  Release version as named in ATR, e.g. 0.3.0-incubating. The sources
             at <git-ref> must declare the same version without the -incubating
             suffix (0.3.0), which Python package versions cannot carry.
  -r <ref>   Git ref to package (default: HEAD). Use the RC tag for a release.
  -o <dir>   Output directory (default: dist/<release> in the repository).
  -u <key>   OpenPGP key to sign with (default: gpg's default key).
  -s         Skip signing, for dry runs.
EOF
}

REF="HEAD"
OUT_DIR=""
GPG_KEY=""
SIGN=true

while getopts "r:o:u:sh" opt; do
  case "$opt" in
    r) REF="$OPTARG" ;;
    o) OUT_DIR="$OPTARG" ;;
    u) GPG_KEY="$OPTARG" ;;
    s) SIGN=false ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

if [ $# -ne 1 ]; then
  usage >&2
  exit 1
fi

RELEASE="$1"
# The release version is part of a file name and of an ATR release path. A
# podling release must carry "incubating" in every artifact name.
if [[ ! "$RELEASE" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.[A-Za-z0-9]+)*-incubating$ ]]; then
  echo "ERROR: '$RELEASE' is not a release version such as 0.3.0-incubating." >&2
  exit 1
fi
VERSION="${RELEASE%-incubating}"
# The archive's single top-level directory must match the archive's base name.
NAME="apache-ossie-${RELEASE}-src"
ARCHIVE="${NAME}.tar.gz"
OUT_DIR="${OUT_DIR:-$REPO_DIR/dist/$RELEASE}"

COMMIT="$(git -C "$REPO_DIR" rev-parse --verify --quiet "${REF}^{commit}")" || {
  echo "ERROR: '$REF' is not a commit in $REPO_DIR." >&2
  exit 1
}

# Every file that records the specification or package version. A release must
# not ship a schema or SDK that still declares a development version.
read_version() {
  local path="$1" content
  content="$(git -C "$REPO_DIR" show "$COMMIT:$path")"
  case "$path" in
    *.json)
      python3 -I -c 'import json, sys; print(json.load(sys.stdin)["properties"]["version"]["const"])' <<<"$content" ;;
    *.toml)
      sed -n 's/^version = "\(.*\)"$/\1/p' <<<"$content" | head -1 ;;
    *.yaml)
      sed -n 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' <<<"$content" | head -1 ;;
    *.py)
      sed -n 's/^ *version: Literal\["\([^"]*\)"\].*/\1/p' <<<"$content" | head -1 ;;
  esac
}

mismatch=false
for path in \
  python/pyproject.toml \
  python/src/ossie/models.py \
  core-spec/spec.yaml \
  core-spec/ossie-schema.json \
  ontology/ontology.json \
  ontology/mapping.json; do
  found="$(read_version "$path")"
  if [ "$found" != "$VERSION" ]; then
    echo "ERROR: $path declares version '${found:-<none>}', expected '$VERSION'." >&2
    mismatch=true
  fi
done
if [ "$mismatch" = true ]; then
  echo "Set the release version in the sources and commit it before building." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
rm -f "$OUT_DIR/$ARCHIVE" "$OUT_DIR/$ARCHIVE.asc" "$OUT_DIR/$ARCHIVE.sha512"

echo "Building $ARCHIVE from $REF ($COMMIT)..."
# git archive packages exactly the committed tree (minus export-ignore paths),
# which is what ATR compares the archive against. Pin line endings so the
# archive does not depend on the release manager's git configuration.
git -C "$REPO_DIR" -c core.autocrlf=false -c core.eol=lf archive \
  --format=tar.gz \
  --prefix="$NAME/" \
  --output="$OUT_DIR/$ARCHIVE" \
  "$COMMIT"

if [ "$SIGN" = true ]; then
  echo "Signing $ARCHIVE..."
  gpg --armor --detach-sign ${GPG_KEY:+--local-user "$GPG_KEY"} \
    --output "$OUT_DIR/$ARCHIVE.asc" "$OUT_DIR/$ARCHIVE"
  gpg --verify "$OUT_DIR/$ARCHIVE.asc" "$OUT_DIR/$ARCHIVE"
fi

if command -v sha512sum &> /dev/null; then
  (cd "$OUT_DIR" && sha512sum "$ARCHIVE" > "$ARCHIVE.sha512")
else
  (cd "$OUT_DIR" && shasum -a 512 "$ARCHIVE" > "$ARCHIVE.sha512")
fi

echo
echo "Source release for Apache Ossie $RELEASE:"
echo "  commit: $COMMIT"
ls -1 "$OUT_DIR" | grep -F "$ARCHIVE" | sed "s|^|  $OUT_DIR/|"
if [ "$SIGN" = false ]; then
  echo "WARNING: not signed; ATR requires a .asc signature for every artifact."
fi
