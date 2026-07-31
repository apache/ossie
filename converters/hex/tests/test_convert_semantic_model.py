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

import pytest
from ossie import OSIDialect

from ossie_hex.ossie_to_hex.convert_ossie_semantic_model import (
    convert_ossie_semantic_model,
)
from ossie_hex.ossie_to_hex.load_ossie_document import load_ossie_document
from ossie_hex.util.errors import ConversionError
from tests.utils import one_metric_ossie


def test_rejects_an_unknown_base_model() -> None:
    """A name that matches no dataset would silently swallow the metrics it takes."""
    document, _ = load_ossie_document(one_metric_ossie("COUNT(*)"))

    with pytest.raises(ConversionError, match="--base-model 'nope'"):
        convert_ossie_semantic_model(
            document.semantic_model[0],
            OSIDialect.ANSI_SQL,
            base_model="nope",
            warnings=[],
        )
