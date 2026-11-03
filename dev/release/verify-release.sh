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

# Verifies an Apache Ossie source release candidate: checksum, signature,
# archive layout, LICENSE/NOTICE/DISCLAIMER, binary files and license headers,
# and optionally the test suites and a comparison with the git tag.
# See dev/release/README.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_KEYS="https://downloads.apache.org/incubator/ossie/KEYS"

usage() {
  cat <<EOF
Usage: $(basename "$0") [-k <keys>] [-g <git-ref>] [-n] [-t] <dir> <release>

Verifies apache-ossie-<release>-src.tar.gz and its .asc and .sha512 files in
<dir>.

  <dir>      Directory holding the downloaded release candidate files.
  <release>  Release version as named in ATR, e.g. 0.3.0-incubating.
  -k <keys>  KEYS file path or URL to verify the signature against
             (default: $DEFAULT_KEYS).
  -g <ref>   Also check that the archive holds exactly the files of <ref>
             (e.g. the RC tag) in this git clone, less export-ignore paths.
  -n         Skip the signature check, for unsigned dry-run archives.
  -t         Also run the test suites from the extracted archive (needs uv).
EOF
}

KEYS="$DEFAULT_KEYS"
GIT_REF=""
CHECK_SIGNATURE=true
RUN_TESTS=false

while getopts "k:g:nth" opt; do
  case "$opt" in
    k) KEYS="$OPTARG" ;;
    g) GIT_REF="$OPTARG" ;;
    n) CHECK_SIGNATURE=false ;;
    t) RUN_TESTS=true ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

if [ $# -ne 2 ]; then
  usage >&2
  exit 1
fi

DIR="$(cd "$1" && pwd)"
RELEASE="$2"
if [[ "$RELEASE" != *-incubating ]]; then
  echo "ERROR: '$RELEASE' is not a podling release version such as 0.3.0-incubating." >&2
  exit 1
fi
NAME="apache-ossie-${RELEASE}-src"
ARCHIVE="${NAME}.tar.gz"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

failures=0
pass() { echo "  OK    $*"; }
fail() { echo "  FAIL  $*"; failures=$((failures + 1)); }
warn() { echo "  WARN  $*"; }

echo "Verifying $ARCHIVE in $DIR"

echo "Files"
for f in "$ARCHIVE" "$ARCHIVE.sha512" "$ARCHIVE.asc"; do
  if [ "$f" = "$ARCHIVE.asc" ] && [ "$CHECK_SIGNATURE" = false ]; then
    continue
  fi
  if [ -f "$DIR/$f" ]; then pass "$f"; else fail "$f is missing"; fi
done
if [ ! -f "$DIR/$ARCHIVE" ]; then
  echo "Cannot continue without $ARCHIVE."
  exit 1
fi

echo "Checksum"
expected="$(awk '{print $1; exit}' "$DIR/$ARCHIVE.sha512" 2>/dev/null || true)"
if command -v sha512sum &> /dev/null; then
  actual="$(sha512sum "$DIR/$ARCHIVE" | awk '{print $1}')"
else
  actual="$(shasum -a 512 "$DIR/$ARCHIVE" | awk '{print $1}')"
fi
if [ -n "$expected" ] && [ "$expected" = "$actual" ]; then
  pass "SHA-512 matches"
else
  fail "SHA-512 does not match $ARCHIVE.sha512"
fi

if [ "$CHECK_SIGNATURE" = true ]; then
  echo "Signature"
  # Use a throwaway keyring so only the project's published KEYS are trusted
  # and the voter's own keyring is left untouched.
  export GNUPGHOME="$WORK_DIR/gnupg"
  mkdir -m 700 "$GNUPGHOME"
  if [[ "$KEYS" =~ ^https?:// ]]; then
    curl -sSfL -o "$WORK_DIR/KEYS" "$KEYS" || {
      fail "could not download $KEYS (pass -k with the KEYS file from ATR)"
      KEYS=""
    }
  else
    cp "$KEYS" "$WORK_DIR/KEYS"
  fi
  if [ -n "$KEYS" ]; then
    gpg --batch --quiet --import "$WORK_DIR/KEYS" 2> /dev/null || true
    if gpg --batch --verify "$DIR/$ARCHIVE.asc" "$DIR/$ARCHIVE" 2> "$WORK_DIR/gpg.log"; then
      pass "$(grep -m1 'Good signature' "$WORK_DIR/gpg.log" | sed 's/^gpg: //')"
    else
      sed 's/^/        /' "$WORK_DIR/gpg.log"
      fail "signature does not verify against $KEYS"
    fi
  fi
fi

echo "Archive layout"
mkdir "$WORK_DIR/src"
tar -xzf "$DIR/$ARCHIVE" -C "$WORK_DIR/src"
roots="$(ls -A "$WORK_DIR/src")"
if [ "$roots" = "$NAME" ]; then
  pass "single top-level directory $NAME/"
else
  fail "expected only $NAME/ at the top level, found: $(echo "$roots" | tr '\n' ' ')"
fi
SRC="$WORK_DIR/src/$NAME"
if [ ! -d "$SRC" ]; then
  echo "Cannot continue without $NAME/."
  exit 1
fi
links="$(find "$SRC" -type l)"
if [ -z "$links" ]; then pass "no symbolic links"; else fail "symbolic links: $links"; fi

echo "Legal files"
for f in LICENSE NOTICE DISCLAIMER; do
  if [ -f "$SRC/$f" ]; then pass "$f"; else fail "$f is missing from $NAME/"; fi
done
if [ -f "$SRC/NOTICE" ]; then
  notice="$(tr -s '[:space:]' ' ' < "$SRC/NOTICE")"
  for line in \
    "Apache Ossie (incubating)" \
    "The Apache Software Foundation" \
    "This product includes software developed at The Apache Software Foundation"; do
    if [[ "$notice" == *"$line"* ]]; then pass "NOTICE: $line"; else fail "NOTICE lacks: $line"; fi
  done
  year="$(date +%Y)"
  if [[ ! "$notice" =~ Copyright\ ([0-9]{4}-)?$year\  ]]; then
    warn "NOTICE copyright does not include $year"
  fi
fi

echo "Binary files"
# Source releases must not contain compiled code; images are expected.
binaries=""
while IFS= read -r -d '' f; do
  case "$f" in
    *.png|*.jpg|*.jpeg|*.gif|*.ico|*.svg) continue ;;
  esac
  if ! grep -Iq . "$f"; then
    binaries="$binaries ${f#"$SRC/"}"
  fi
done < <(find "$SRC" -type f -size +0 -print0)
if [ -z "$binaries" ]; then pass "no binary files other than images"; else fail "binary files:$binaries"; fi

if [ -n "$GIT_REF" ]; then
  echo "Git comparison"
  mkdir "$WORK_DIR/git"
  if git -C "$REPO_DIR" -c core.autocrlf=false -c core.eol=lf archive --format=tar "$GIT_REF" \
      | tar -xf - -C "$WORK_DIR/git"; then
    # Compared before RAT and the tests run, as both write into the extracted tree.
    if diff -r "$WORK_DIR/git" "$SRC" > "$WORK_DIR/diff.log"; then
      pass "archive matches $GIT_REF ($(git -C "$REPO_DIR" rev-parse "$GIT_REF^{commit}"))"
    else
      sed 's/^/        /' "$WORK_DIR/diff.log" | head -40
      fail "archive differs from $GIT_REF"
    fi
  else
    fail "could not read $GIT_REF from $REPO_DIR"
  fi
fi

echo "License headers (Apache RAT)"
if "$SRC/dev/check-license" > "$WORK_DIR/rat.log" 2>&1; then
  pass "all files have license headers"
else
  # Drop JVM and logging noise that newer JDKs print around RAT's report.
  grep -v -e 'three-letter time zone ID' -e '^SLF4J' "$WORK_DIR/rat.log" | sed 's/^/        /'
  fail "Apache RAT reported files without license headers"
fi

if [ "$RUN_TESTS" = true ]; then
  echo "Tests"
  run() {
    if (cd "$SRC" && "$@") > "$WORK_DIR/test.log" 2>&1; then
      pass "$*"
    else
      tail -20 "$WORK_DIR/test.log" | sed 's/^/        /'
      fail "$*"
    fi
  }
  run uv run --with pytest --with jsonschema --with ./python -m pytest python/tests/
  run uv run --with pytest --with pyyaml --with jsonschema --with sqlglot -m pytest validation/tests/
  run uv run validation/test_validate.py
  run uv run scripts/generate-spec-types.py --check
  run uv run validation/validate.py examples/tpcds_semantic_model.yaml
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "All checks passed for $ARCHIVE."
else
  echo "$failures check(s) failed for $ARCHIVE."
  exit 1
fi
