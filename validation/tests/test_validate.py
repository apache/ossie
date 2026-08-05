from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

validate_references = _VALIDATE.validate_references


def _document_with_relationship(from_columns: list[str], to_columns: list[str]) -> dict:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {"name": "orders", "source": "db.s.orders"},
                    {"name": "customers", "source": "db.s.customers"},
                ],
                "relationships": [
                    {
                        "name": "orders_to_customers",
                        "from": "orders",
                        "to": "customers",
                        "from_columns": from_columns,
                        "to_columns": to_columns,
                    }
                ],
            }
        ],
    }


def test_validate_references_rejects_mismatched_relationship_column_counts() -> None:
    errors = validate_references(
        _document_with_relationship(
            from_columns=["customer_id", "region_id"],
            to_columns=["id"],
        )
    )

    assert errors == [
        "[Relationship] Relationship 'orders_to_customers' in model 'm' has "
        "2 from_columns but 1 to_columns"
    ]


def test_validate_references_accepts_matching_relationship_column_counts() -> None:
    errors = validate_references(
        _document_with_relationship(
            from_columns=["customer_id", "region_id"],
            to_columns=["id", "region_id"],
        )
    )

    assert errors == []
