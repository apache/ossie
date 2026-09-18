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

"""Shared test helpers."""

import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

# The canonical example models live at the repository root, not inside this converter.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"
VALIDATOR = REPO_ROOT / "validation" / "validate.py"
SCHEMA = REPO_ROOT / "core-spec" / "ossie-schema.json"


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def ossie_doc(model):
    """Place a single semantic model in a minimal valid Apache Ossie document."""
    return {"version": "0.2.0.dev0", **model}
