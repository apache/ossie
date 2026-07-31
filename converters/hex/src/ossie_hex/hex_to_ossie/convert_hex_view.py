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

from __future__ import annotations

from ..hex_types import HEX_VENDOR, HexView, HexViewStash
from ..util.errors import ConversionWarning


def convert_hex_view(view: HexView) -> tuple[HexViewStash, list[ConversionWarning]]:
    """Record a Hex view for the semantic model to carry through Ossie.

    Ossie models the data, not the entry points onto it, so a view is kept whole
    rather than converted and comes back verbatim on import.
    """
    warnings = [
        ConversionWarning(
            f"view '{view.id}' has no Ossie core equivalent; "
            f"preserved in custom_extensions[{HEX_VENDOR}]"
        )
    ]
    return HexViewStash(resource=view), warnings
