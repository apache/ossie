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

# Run Python tests against the full range of supported Python versions.
# Replicates a CI workflow locally in isolated environments.

# The isolated environments leave the project's `.venv` unchanged. Keep `uv`
# up to date so version selectors resolve to stable Python releases rather than
# an older prerelease installation.

set -euo pipefail

cd "$(dirname "$0")/.."

python_versions=(3.11 3.12 3.13 3.14)

uv python install "${python_versions[@]}"

for python_version in "${python_versions[@]}"; do
  uv run --isolated --python "${python_version}" pytest "$@"
done
