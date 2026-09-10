#!/usr/bin/env python3
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

"""OSI Foundation compliance adapter for implementations.

A thin delegator to ``impl/python/conformance/adapter.py``. The upstream
adapter implements the CLI contract published in
[`../ADAPTER_INTERFACE.md`](../ADAPTER_INTERFACE.md) (``sql --model M
--query-file Q --dialect D``); this file re-exposes it from inside the
compliance suite so the harness can resolve it from
``adapters/osi_python_adapter.py`` without reaching out of the suite.

We deliberately do NOT re-implement any conversion logic here. If a suite
test case needs a YAML / JSON shape the upstream adapter does not
understand, fix it in the upstream adapter (a single source of truth) —
never fork the conversion logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ADAPTER_DIR = Path(__file__).resolve().parent
# adapters/ is at compliance/foundation/adapters/.
# Walk up three levels (adapters → foundation → compliance → repo root),
# then descend into impl/python where the upstream adapter lives.
_REPO_ROOT = _ADAPTER_DIR.parent.parent.parent
_IMPL_PYTHON_ROOT = _REPO_ROOT / "impl" / "python"
_IMPL_PYTHON_CONFORMANCE = _IMPL_PYTHON_ROOT / "conformance"

if not _IMPL_PYTHON_CONFORMANCE.exists():
    sys.stderr.write(
        f"osi_python_adapter: cannot find upstream adapter at "
        f"{_IMPL_PYTHON_CONFORMANCE}; check that impl/python/ is present "
        f"at the OSI repo root.\n",
    )
    sys.exit(2)

# Make the upstream conformance package importable. The upstream adapter
# prepends impl/python/src to sys.path itself, so we only need to expose its
# parent directory here.
sys.path.insert(0, str(_IMPL_PYTHON_ROOT))

from conformance.adapter import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
