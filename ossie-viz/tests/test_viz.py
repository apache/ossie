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

from pathlib import Path

import yaml
from ossie.models import OssieDocument

from ossie_viz.cli import main
from ossie_viz.renderer import build_graph_payload, render_html

ROLE_PLAYING_MODEL = {
    "version": "0.3.0.dev0",
    "name": "telecom_role_playing_test",
    "description": "Synthetic schema testing role-playing dimensions and self-joins",
    "ai_context": {
        "instructions": "Use order_date for bookings and activation_date for revenue recognition."
    },
    "datasets": [
        {
            "name": "orders",
            "source": "iceberg.gold.fact_orders",
            "primary_key": ["order_id"],
            "fields": [
                {"name": "order_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "order_id"}]}},
                {"name": "order_date_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "order_date_id"}]}},
                {"name": "activation_date_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "activation_date_id"}]}},
                {"name": "subscriber_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "subscriber_id"}]}},
                {
                    "name": "amount_egp",
                    "expression": {
                        "dialects": [
                            {"dialect": "OSSIE_SQL_2026", "expression": "amount_egp"},
                            {"dialect": "TRINO", "expression": "CAST(amount_egp AS DECIMAL(18,2))"},
                        ]
                    },
                },
            ],
        },
        {
            "name": "dim_date",
            "source": "iceberg.gold.dim_date",
            "primary_key": ["date_id"],
            "fields": [
                {"name": "date_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "date_id"}]}},
                {"name": "calendar_day", "dimension": {"is_time": True}, "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "calendar_day"}]}},
            ],
        },
        {
            "name": "subscribers",
            "source": "iceberg.gold.dim_subscribers",
            "primary_key": ["subscriber_id"],
            "fields": [
                {"name": "subscriber_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "subscriber_id"}]}},
                {"name": "referrer_subscriber_id", "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "referrer_subscriber_id"}]}},
            ],
        },
    ],
    "relationships": [
        {
            "name": "orders_order_date",
            "from": "orders",
            "to": "dim_date",
            "from_columns": ["order_date_id"],
            "to_columns": ["date_id"],
        },
        {
            "name": "orders_activation_date",
            "from": "orders",
            "to": "dim_date",
            "from_columns": ["activation_date_id"],
            "to_columns": ["date_id"],
        },
        {
            "name": "orders_to_subscribers",
            "from": "orders",
            "to": "subscribers",
            "from_columns": ["subscriber_id"],
            "to_columns": ["subscriber_id"],
        },
        {
            "name": "subscriber_referral_hierarchy",
            "from": "subscribers",
            "to": "subscribers",
            "from_columns": ["referrer_subscriber_id"],
            "to_columns": ["subscriber_id"],
        },
    ],
    "metrics": [
        {
            "name": "total_revenue_egp",
            "description": "Total order revenue",
            "expression": {
                "dialects": [
                    {"dialect": "OSSIE_SQL_2026", "expression": "SUM(orders.amount_egp)"},
                    {"dialect": "CLICKHOUSE", "expression": "sum(orders.amount_egp)"},
                ]
            },
        }
    ],
}


def test_rendered_html_has_zero_silent_omissions() -> None:
    doc = OssieDocument.model_validate(ROLE_PLAYING_MODEL)
    payload = build_graph_payload(doc)
    rendered = render_html(doc)

    # Verify zero third-party external scripts
    assert "<script src=" not in rendered

    # Verify every dataset and field appears in the rendered HTML artifact
    for ds in doc.datasets:
        assert ds.name in rendered
        for fld in ds.fields:
            assert fld.name in rendered

    # Verify role-playing joins and self-joins are preserved on distinct lanes
    rel_by_name = {r["name"]: r for r in payload["relationships"]}
    assert len(rel_by_name) == 4
    assert rel_by_name["orders_order_date"]["pair_total"] == 2
    assert rel_by_name["orders_activation_date"]["pair_total"] == 2
    assert rel_by_name["orders_order_date"]["lane_index"] != rel_by_name["orders_activation_date"]["lane_index"]
    assert rel_by_name["subscriber_referral_hierarchy"]["is_self_join"] is True

    for rel in doc.relationships:
        assert rel.name in rendered

    # Verify metrics and metric-to-dataset references are present
    assert "total_revenue_egp" in rendered
    assert payload["metrics"][0]["referenced_datasets"] == ["orders"]


def test_cli_end_to_end(tmp_path: Path) -> None:
    model_file = tmp_path / "role_playing.ossie.yaml"
    out_file = tmp_path / "role_playing.html"
    model_file.write_text(yaml.safe_dump(ROLE_PLAYING_MODEL, sort_keys=False), encoding="utf-8")

    exit_code = main([str(model_file), "--out", str(out_file)])
    assert exit_code == 0
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "telecom_role_playing_test" in content
    assert "orders_activation_date" in content
