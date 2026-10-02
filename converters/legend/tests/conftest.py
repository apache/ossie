"""Test fixtures for Legend converter tests."""

import pytest
import yaml


@pytest.fixture
def simple_osi_model():
    """Simple OSI model with one dataset."""
    return {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "simple_model",
                "description": "A simple test model",
                "datasets": [
                    {
                        "name": "users",
                        "source": "mydb.public.users",
                        "description": "User accounts",
                        "primary_key": ["user_id"],
                        "fields": [
                            {
                                "name": "user_id",
                                "expression": {
                                    "dialects": [
                                        {
                                            "dialect": "ANSI_SQL",
                                            "expression": "user_id",
                                        }
                                    ]
                                },
                                "dimension": {"is_time": False},
                                "description": "Unique user identifier",
                            },
                            {
                                "name": "email",
                                "expression": {
                                    "dialects": [
                                        {
                                            "dialect": "ANSI_SQL",
                                            "expression": "email",
                                        }
                                    ]
                                },
                                "description": "User email",
                            },
                            {
                                "name": "created_at",
                                "expression": {
                                    "dialects": [
                                        {
                                            "dialect": "ANSI_SQL",
                                            "expression": "created_at",
                                        }
                                    ]
                                },
                                "dimension": {"is_time": True},
                                "description": "Account creation timestamp",
                            },
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def complex_osi_model():
    """Complex OSI model with multiple datasets and joins."""
    return {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "ecommerce",
                "description": "E-commerce semantic model",
                "datasets": [
                    {
                        "name": "customers",
                        "source": "warehouse.public.customers",
                        "description": "Customer master data",
                        "primary_key": ["customer_id"],
                        "fields": [
                            {
                                "name": "customer_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "customer_id"}
                                    ]
                                },
                                "dimension": {"is_time": False},
                            },
                            {
                                "name": "email",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "email"}
                                    ]
                                },
                            },
                            {
                                "name": "country",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "country"}
                                    ]
                                },
                            },
                        ],
                    },
                    {
                        "name": "orders",
                        "source": "warehouse.public.orders",
                        "description": "Customer orders",
                        "primary_key": ["order_id"],
                        "fields": [
                            {
                                "name": "order_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "order_id"}
                                    ]
                                },
                                "dimension": {"is_time": False},
                            },
                            {
                                "name": "customer_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "customer_id"}
                                    ]
                                },
                            },
                            {
                                "name": "order_date",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "order_date"}
                                    ]
                                },
                                "dimension": {"is_time": True},
                            },
                            {
                                "name": "amount",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "amount"}
                                    ]
                                },
                            },
                        ],
                    },
                    {
                        "name": "products",
                        "source": "warehouse.public.products",
                        "description": "Product catalog",
                        "primary_key": ["product_id"],
                        "fields": [
                            {
                                "name": "product_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "product_id"}
                                    ]
                                },
                            },
                            {
                                "name": "product_name",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "product_name"}
                                    ]
                                },
                            },
                            {
                                "name": "category",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "category"}
                                    ]
                                },
                            },
                        ],
                    },
                ],
                "join_paths": [
                    {
                        "name": "orders_to_customers",
                        "from": "orders",
                        "to": "customers",
                        "from_columns": ["customer_id"],
                        "to_columns": ["customer_id"],
                    },
                    {
                        "name": "orders_to_products",
                        "from": "orders",
                        "to": "products",
                        "from_columns": ["product_id"],
                        "to_columns": ["product_id"],
                    },
                ],
            }
        ],
    }


@pytest.fixture
def osi_with_custom_extensions():
    """OSI model with custom FINOS extensions for type hints."""
    return {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "typed_model",
                "datasets": [
                    {
                        "name": "transactions",
                        "source": "db.transactions",
                        "fields": [
                            {
                                "name": "transaction_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "transaction_id"}
                                    ]
                                },
                                "custom_extensions": [
                                    {
                                        "vendor_name": "FINOS",
                                        "data": '{"type": "BIGINT"}',
                                    }
                                ],
                            },
                            {
                                "name": "price",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "price"}
                                    ]
                                },
                                "custom_extensions": [
                                    {
                                        "vendor_name": "FINOS",
                                        "data": '{"type": "DECIMAL(18,4)"}',
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def ontology_osi_model():
    """OSI model that includes ontology concepts and relationships."""
    return {
        "version": "0.1.2",
        "semantic_model": [
            {
                "name": "hr_model",
                "datasets": [],
            }
        ],
        "ontology": [
            {
                "concept": "Person",
                "relationships": [
                    {
                        "name": "earns",
                        "roles": [{"player": "Salary"}],
                        "multiplicity": "ManyToOne",
                    },
                    {
                        "name": "parent_of",
                        "roles": [{"player": "Person", "name": "child"}],
                    },
                ],
            },
            {
                "concept": "Employee",
                "extends": ["Person"],
                "relationships": [
                    {
                        "name": "works_in",
                        "roles": [
                            {"player": "Department"},
                            {"player": "Region", "name": "region"},
                        ],
                        "multiplicity": "ManyToOne",
                    }
                ],
            },
            {
                "concept": "Salary",
                "extends": ["Decimal"],
            },
        ],
    }
