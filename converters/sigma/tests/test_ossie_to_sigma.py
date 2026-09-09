from pathlib import Path

import yaml
from ossie import (
    OssieDataset,
    OssieDialect,
    OssieDialectExpression,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieSemanticModel,
)

import pytest
from ossie import OssieRelationship

from ossie_sigma.converter_issues import ConverterError, ConverterIssueType
from ossie_sigma.ossie_to_sigma import OssieToSigmaConverter, _stable_id
from ossie_sigma.sigma_to_ossie import SigmaToOssieConverter

from .helpers import load_fixture, normalize

EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "examples"


def test_roundtrip_fixture_a_is_byte_identical():
    spec = load_fixture("fixtureA_sigma.json")
    document = SigmaToOssieConverter().convert(spec).output
    reconstructed = OssieToSigmaConverter().convert(document).output
    assert normalize(reconstructed) == normalize(spec)


def test_roundtrip_fixture_b_is_byte_identical():
    spec = load_fixture("fixtureB_sigma.json")
    document = SigmaToOssieConverter().convert(spec).output
    reconstructed = OssieToSigmaConverter().convert(document).output
    assert normalize(reconstructed) == normalize(spec)


def test_foreign_origin_document_synthesizes_valid_spec():
    """An Ossie document never touched by Sigma (no SIGMA custom_extensions) must
    still convert to a structurally valid Sigma spec, with synthesized ids and
    formulas best-effort translated from ANSI SQL."""
    document = OssieDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    result = OssieToSigmaConverter().convert(document)
    spec = result.output

    assert spec["kind"] == "data-model"
    assert spec["pages"]
    element_names = {e["name"] for p in spec["pages"] for e in p["elements"]}
    assert "store_sales" in element_names

    store_sales = next(e for p in spec["pages"] for e in p["elements"] if e["name"] == "store_sales")
    assert all("id" in c and "formula" in c for c in store_sales["columns"])
    # Plain passthrough columns get no explicit `name` (matches Sigma's own convention).
    plain_column = next(c for c in store_sales["columns"] if c["formula"] == "[ss_sold_date_sk]")
    assert "name" not in plain_column

    # Single-dataset metrics are attached to their owning element ...
    assert any(m["name"] == "total_sales" for m in store_sales.get("metrics", []))
    # ... while genuinely cross-dataset metrics are dropped with a recorded issue,
    # not silently discarded and not incorrectly attached to one dataset.
    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.CROSS_DATASET_METRIC_DROPPED in issue_types


def test_ids_are_deterministic_across_repeated_conversions():
    document = OssieDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    spec_1 = OssieToSigmaConverter().convert(document).output
    spec_2 = OssieToSigmaConverter().convert(document).output
    assert normalize(spec_1) == normalize(spec_2)


def test_synthesized_ids_are_stable_across_processes():
    """uuid5 over a fixed namespace, not hash()/uuid4 — a re-export that minted new ids
    would silently orphan every Sigma object referencing the old ones, so this pins the
    exact values rather than only asserting two in-process runs agree (PYTHONHASHSEED
    randomization would not show up in a same-process comparison)."""
    assert _stable_id("element", "store_sales") == "83ae98f0bda0511baf98cd58fd394974"
    assert _stable_id("column", "store_sales", "ss_sold_date_sk") == "48b667ffa9535d08bcc1d0a48b878a99"
    assert _stable_id("metric", "store_sales", "total_sales") == "9d4cc3056b0c5c698664c4803f12dd72"


def test_relationship_ids_are_scoped_by_owning_dataset():
    """Two unrelated relationships sharing a name, on different table pairs, must not
    collide onto the same synthesized Sigma relationship id."""
    document = OssieDocument(
        semantic_model=[
            OssieSemanticModel(
                name="m",
                datasets=[
                    OssieDataset(name="orders", source="db.public.orders"),
                    OssieDataset(name="shipments", source="db.public.shipments"),
                    OssieDataset(name="customers", source="db.public.customers"),
                    OssieDataset(name="carriers", source="db.public.carriers"),
                ],
                relationships=[
                    OssieRelationship(
                        name="Parent", **{"from": "orders"}, to="customers", from_columns=["x"], to_columns=["y"]
                    ),
                    OssieRelationship(
                        name="Parent", **{"from": "shipments"}, to="carriers", from_columns=["x"], to_columns=["y"]
                    ),
                ],
            )
        ]
    )

    spec = OssieToSigmaConverter().convert(document).output
    rel_ids = [
        rel["id"]
        for page in spec["pages"]
        for element in page["elements"]
        for rel in element.get("relationships", [])
    ]
    assert len(rel_ids) == 2
    assert len(set(rel_ids)) == 2, "relationships with the same name on different dataset pairs must not collide"


def test_empty_semantic_model_raises_a_clear_error():
    document = OssieDocument(semantic_model=[])
    with pytest.raises(ConverterError):
        OssieToSigmaConverter().convert(document)


def test_model_level_metadata_round_trips_through_ossie_and_back():
    spec = load_fixture("fixtureA_sigma.json")
    spec.update(
        createdAt="2024-01-01T00:00:00Z",
        createdBy="user-1",
        updatedAt="2024-02-01T00:00:00Z",
        updatedBy="user-2",
        ownerId="user-1",
        url="https://app.sigmacomputing.com/data-model/11111111",
    )
    document = SigmaToOssieConverter().convert(spec).output
    reconstructed = OssieToSigmaConverter().convert(document).output
    for key in ("createdAt", "createdBy", "updatedAt", "updatedBy", "ownerId", "url"):
        assert reconstructed[key] == spec[key]


def test_untranslatable_expression_omits_the_column_instead_of_faking_a_formula():
    """`formula` is required on every Sigma column and the data model API validates the
    whole document before applying any of it, so a placeholder would fail the entire
    upload rather than degrade one column."""
    document = OssieDocument(
        semantic_model=[
            OssieSemanticModel(
                name="m",
                datasets=[
                    OssieDataset(
                        name="orders",
                        source="db.public.orders",
                        fields=[
                            OssieField(
                                name="ok",
                                expression=OssieExpression(
                                    dialects=[OssieDialectExpression(dialect=OssieDialect.ANSI_SQL, expression="amount")]
                                ),
                            ),
                            OssieField(
                                name="untranslatable",
                                expression=OssieExpression(
                                    dialects=[
                                        OssieDialectExpression(
                                            dialect=OssieDialect.ANSI_SQL,
                                            expression="SUM(amount) OVER (PARTITION BY region)",
                                        )
                                    ]
                                ),
                            ),
                            OssieField(
                                name="no_usable_dialect",
                                expression=OssieExpression(
                                    dialects=[OssieDialectExpression(dialect=OssieDialect.MDX, expression="[Measures].[X]")]
                                ),
                            ),
                        ],
                    )
                ],
                metrics=[
                    OssieMetric(
                        name="untranslatable_metric",
                        expression=OssieExpression(
                            dialects=[
                                OssieDialectExpression(
                                    dialect=OssieDialect.ANSI_SQL,
                                    expression="SUM(orders.amount) OVER (PARTITION BY orders.region)",
                                )
                            ]
                        ),
                    )
                ],
            )
        ]
    )

    result = OssieToSigmaConverter().convert(document)
    element = result.output["pages"][0]["elements"][0]

    assert [c["name"] for c in element["columns"]] == ["ok"]
    assert not element.get("metrics")
    assert all(c.get("formula") for c in element["columns"])
    assert (
        sum(1 for i in result.issues if i.issue_type is ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE) == 3
    )


def test_synthesized_spec_carries_a_schema_version():
    """`schemaVersion` is required by the create/update endpoints."""
    document = OssieDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    assert OssieToSigmaConverter().convert(document).output["schemaVersion"] == 1


def test_datatypes_only_ever_emit_the_two_documented_format_kinds():
    document = OssieDocument(
        semantic_model=[
            OssieSemanticModel(
                name="m",
                datasets=[
                    OssieDataset(
                        name="t",
                        source="db.public.t",
                        fields=[
                            OssieField(
                                name=datatype.lower(),
                                datatype=datatype,
                                expression=OssieExpression(
                                    dialects=[
                                        OssieDialectExpression(
                                            dialect=OssieDialect.ANSI_SQL, expression=datatype.lower()
                                        )
                                    ]
                                ),
                            )
                            for datatype in (
                                "String",
                                "Integer",
                                "Decimal",
                                "Float",
                                "Boolean",
                                "Date",
                                "Time",
                                "DateTime",
                                "DateTimeTz",
                            )
                        ],
                    )
                ],
            )
        ]
    )

    columns = OssieToSigmaConverter().convert(document).output["pages"][0]["elements"][0]["columns"]
    kinds = {c["formula"].strip("[]"): c["format"]["kind"] for c in columns if "format" in c}
    assert kinds == {
        "integer": "number",
        "decimal": "number",
        "float": "number",
        "date": "date",
        "datetime": "date",
        "datetimetz": "date",
    }
    # String/Boolean/Time have no Sigma display format; emitting an invented `kind`
    # would be rejected by the data model API for the whole document.
    assert {c["formula"].strip("[]") for c in columns if "format" not in c} == {"string", "boolean", "time"}
