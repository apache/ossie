"""Tests for validate_unique_names."""

from validate import validate_unique_names


def test_all_unique_names_produce_no_errors():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {"name": "a", "fields": [{"name": "x"}, {"name": "y"}]},
                    {"name": "b", "fields": [{"name": "z"}]},
                ],
                "metrics": [{"name": "m1"}, {"name": "m2"}],
                "relationships": [{"name": "r1"}, {"name": "r2"}],
            }
        ]
    }
    assert validate_unique_names(data) == []


def test_duplicate_dataset_name():
    data = {"semantic_model": [{"name": "m", "datasets": [{"name": "a"}, {"name": "a"}]}]}
    errors = validate_unique_names(data)
    assert errors == ["[Unique] Duplicate dataset name 'a' in model 'm'"]


def test_duplicate_field_name_is_scoped_to_dataset():
    # Same field name in two different datasets is ok, only an in dataset repeat fails.
    data = {
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {"name": "a", "fields": [{"name": "x"}, {"name": "x"}]},
                    {"name": "b", "fields": [{"name": "x"}]},
                ],
            }
        ]
    }
    errors = validate_unique_names(data)
    assert errors == ["[Unique] Duplicate field name 'x' in dataset 'a'"]


def test_duplicate_metric_and_relationship_names():
    data = {
        "semantic_model": [
            {
                "name": "m",
                "metrics": [{"name": "dup"}, {"name": "dup"}],
                "relationships": [{"name": "rel"}, {"name": "rel"}],
            }
        ]
    }
    errors = validate_unique_names(data)
    assert "[Unique] Duplicate metric name 'dup' in model 'm'" in errors
    assert "[Unique] Duplicate relationship name 'rel' in model 'm'" in errors
