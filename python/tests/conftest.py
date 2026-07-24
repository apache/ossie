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

from ossie import OSIDialect, OSIExpression


def _expression_data(value: str = "value") -> dict:
    return {"dialects": [{"dialect": OSIDialect.ANSI_SQL, "expression": value}]}


def _expression(value: str = "value") -> OSIExpression:
    return OSIExpression.model_validate(_expression_data(value))


@pytest.fixture
def document_data() -> dict:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "typed_model",
                "datasets": [
                    {
                        "name": "events",
                        "source": "catalog.schema.events",
                        "fields": [
                            {
                                "name": "occurred_at",
                                "expression": _expression_data("occurred_at"),
                                "dimension": {},
                                "datatype": "DateTimeTz",
                            }
                        ],
                    }
                ],
                "metrics": [
                    {
                        "name": "revenue",
                        "expression": _expression_data("SUM(events.revenue)"),
                        "datatype": "Decimal",
                    }
                ],
            }
        ],
    }
