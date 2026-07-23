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

"""Entry points for loading a semantido SemanticLayer.

semantido's source of truth is live Python (decorated SQLAlchemy models),
so the primary loader imports a module and syncs the layer. A file-based
path is not provided in this converter: semantido's JSON export is a
rendering of the same layer, and consumers who have the JSON already have
a semantic model they can transform directly.
"""

import importlib
import sys
from pathlib import Path

from semantido import SemanticDeclarativeBase
from semantido.generators.semantic_layer import SemanticLayer


def load_from_module(module_path: str, sys_path: str | None = None) -> SemanticLayer:
    """Import a module of decorated models and return the synced layer.

    Args:
        module_path: Dotted module path, e.g. ``models.emir_reporting``.
        sys_path: Optional directory prepended to ``sys.path`` first, so
            model packages can be loaded without installation.
    """
    if sys_path:
        sys.path.insert(0, str(Path(sys_path).resolve()))
    importlib.import_module(module_path)
    return SemanticDeclarativeBase.sync_semantic_layer()
