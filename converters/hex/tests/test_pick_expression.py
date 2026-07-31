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

from ossie import OSIDialect, OSIDialectExpression, OSIExpression

from ossie_hex.util.pick_expression import pick_expression


def test_pick_expression_prefers_requested_then_ansi_dialect() -> None:
    expression = OSIExpression(
        dialects=[
            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="ansi_expr"),
            OSIDialectExpression(
                dialect=OSIDialect.SNOWFLAKE, expression="snowflake_expr"
            ),
        ]
    )

    assert (
        pick_expression(expression, preferred=OSIDialect.SNOWFLAKE) == "snowflake_expr"
    )
    assert pick_expression(expression, preferred=OSIDialect.BIGQUERY) == "ansi_expr"


def test_pick_expression_falls_back_to_first_or_none() -> None:
    expression = OSIExpression(
        dialects=[
            OSIDialectExpression(
                dialect=OSIDialect.SNOWFLAKE, expression="snowflake_expr"
            )
        ]
    )

    assert (
        pick_expression(expression, preferred=OSIDialect.BIGQUERY) == "snowflake_expr"
    )
    assert pick_expression(None) is None
