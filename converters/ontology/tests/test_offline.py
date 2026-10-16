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

"""The suite runs offline, and says so if it stops being true.

Two mechanisms, asserted here so neither can lapse quietly:

* conftest pins ``RAI_CONFIG_FILE_PATH`` at import. This is not optional --
  ``relationalai`` resolves its config the moment a Model is constructed and
  refuses to start without one -- so the question is never whether there is a
  config, only whose. Unpinned, it searches the CWD, ``~/.rai``, ``~/.snowflake``
  and ``~/.dbt``, and on a contributor's machine it usually finds a real one.

* an autouse fixture refuses every socket connection, so a test that reaches a
  warehouse fails instead of succeeding for whoever has credentials.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
import yaml


def test_rai_config_is_pinned_to_the_offline_fixture():
    pinned = os.environ.get("RAI_CONFIG_FILE_PATH")
    assert pinned, "conftest must pin RAI_CONFIG_FILE_PATH before any Model is built"

    path = Path(pinned)
    assert path.is_file(), f"pinned config does not exist: {path}"
    assert path.parent.name == "config" and path.parents[1].name == "tests", (
        f"the pinned config must be the one in tests/config/, not {path}"
    )


def test_the_offline_config_carries_no_real_credentials():
    """A placeholder that stopped being a placeholder would be a leak.

    The connection block exists only because the schema requires one. If someone
    ever pastes a working account in to debug something, this fails before it is
    committed.
    """
    config = yaml.safe_load(Path(os.environ["RAI_CONFIG_FILE_PATH"]).read_text())

    assert config.get("compiler", {}).get("dry_run") is True
    for name, connection in config.get("connections", {}).items():
        account = str(connection.get("account", ""))
        assert account == "not-a-real-account", (
            f"connection '{name}' names account {account!r}; the offline config "
            "must never carry a reachable account"
        )
        for field in ("user", "password", "warehouse", "role"):
            assert connection.get(field) == "offline", (
                f"connection '{name}' sets {field} to something other than 'offline'"
            )


def test_the_network_guard_refuses_connections():
    """The guard itself works — otherwise the two tests above guard nothing."""
    with pytest.raises(RuntimeError, match="runs offline"):
        socket.create_connection(("example.invalid", 80), timeout=1)

    with pytest.raises(RuntimeError, match="runs offline"):
        socket.socket().connect(("example.invalid", 80))
