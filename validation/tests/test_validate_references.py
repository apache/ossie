"""Tests for relationship and key column checks in validate_references"""

from validate import validate_references


def test_valid_columns_produce_no_findings():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {"name": "store_sales", "fields": [{"name": "ss_customer_sk"}]},
                    {"name": "customer", "fields": [{"name": "c_customer_sk"}]},
                ],
                "relationships": [
                    {
                        "name": "r",
                        "from": "store_sales",
                        "to": "customer",
                        "from_columns": ["ss_customer_sk"],
                        "to_columns": ["c_customer_sk"],
                    }
                ],
            }
        ]
    }
    assert validate_references(data) == []


def test_column_count_mismatch_is_reported():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {
                        "name": "store_sales",
                        "fields": [{"name": "ss_customer_sk"}, {"name": "ss_item_sk"}],
                    },
                    {"name": "customer", "fields": [{"name": "c_customer_sk"}]},
                ],
                "relationships": [
                    {
                        "name": "r",
                        "from": "store_sales",
                        "to": "customer",
                        "from_columns": ["ss_customer_sk", "ss_item_sk"],
                        "to_columns": ["c_customer_sk"],
                    }
                ],
            }
        ]
    }
    findings = validate_references(data)
    assert any("counts must match" in f for f in findings)
    # The mismatch is the only problem, columns themselves are declared fields.
    assert all("not a declared field" not in f for f in findings)


def test_unknown_column_is_warned():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {"name": "store_sales", "fields": [{"name": "ss_customer_sk"}]},
                    {"name": "customer", "fields": [{"name": "c_customer_sk"}]},
                ],
                "relationships": [
                    {
                        "name": "r",
                        "from": "store_sales",
                        "to": "customer",
                        "from_columns": ["typo"],
                        "to_columns": ["c_customer_sk"],
                    }
                ],
            }
        ]
    }
    findings = validate_references(data)
    assert any(
        "Warning" in f and "'typo'" in f and "not a declared field of 'store_sales'" in f
        for f in findings
    )
    # Counts match here, so no count error.
    assert all("counts must match" not in f for f in findings)


def test_unknown_key_columns_are_warned():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {
                        "name": "store_sales",
                        "primary_key": ["ss_item_sk", "ss_ticket_number"],
                        "unique_keys": [["ss_item_sk", "ss_ticket_number"]],
                        "fields": [{"name": "ss_item_sk"}],
                    }
                ],
            }
        ]
    }
    findings = validate_references(data)
    # ss_ticket_number is in primary_key and unique_keys but is not a declared field.
    assert any(
        "primary_key references 'ss_ticket_number', not a declared field" in f
        for f in findings
    )
    assert any(
        "unique_keys[0] references 'ss_ticket_number', not a declared field" in f
        for f in findings
    )
    # ss_item_sk is declared, so it must not be flagged.
    assert all("'ss_item_sk'" not in f for f in findings)


def test_unknown_dataset_skips_column_check():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [{"name": "b", "fields": [{"name": "c"}]}],
                "relationships": [
                    {
                        "name": "r",
                        "from": "missing",
                        "to": "b",
                        "from_columns": ["x"],
                        "to_columns": ["c"],
                    }
                ],
            }
        ]
    }
    findings = validate_references(data)
    # Unknown dataset is reported, but its columns are not flagged as undeclared.
    assert any("unknown dataset 'missing'" in f for f in findings)
    assert all("'x'" not in f for f in findings)
