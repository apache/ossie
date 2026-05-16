"""Unit tests for :mod:`osi.parsing.graph`."""

from __future__ import annotations

from osi.common.identifiers import normalize_identifier
from osi.parsing.graph import Cardinality, build_graph
from osi.parsing.models import Dataset, Field, Relationship, SemanticModel


def _ds(name: str, pk: list[str], fields: list[str]) -> Dataset:
    return Dataset(
        name=name,
        source=f"s.{name}",
        primary_key=pk,
        fields=[Field(name=f, expression=f) for f in fields],
    )


def _rel(
    name: str,
    src: str,
    dst: str,
    src_cols: list[str],
    dst_cols: list[str],
) -> Relationship:
    return Relationship.model_validate(
        {
            "name": name,
            "from": src,
            "to": dst,
            "from_columns": src_cols,
            "to_columns": dst_cols,
        }
    )


class TestCardinalityInference:
    def test_n_to_one_when_rhs_is_pk(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("orders", ["id"], ["id", "customer_id"]),
                _ds("customers", ["id"], ["id"]),
            ],
            relationships=[
                _rel("r", "orders", "customers", ["customer_id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        assert graph.edges[0].cardinality is Cardinality.N_TO_ONE

    def test_one_to_one_when_both_sides_unique(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("a", ["id"], ["id"]),
                _ds("b", ["id"], ["id"]),
            ],
            relationships=[
                _rel("r", "a", "b", ["id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        assert graph.edges[0].cardinality is Cardinality.ONE_TO_ONE

    def test_n_to_n_when_neither_side_unique(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                Dataset(
                    name="a",
                    source="s.a",
                    fields=[Field(name="k", expression="k")],
                ),
                Dataset(
                    name="b",
                    source="s.b",
                    fields=[Field(name="k", expression="k")],
                ),
            ],
            relationships=[_rel("r", "a", "b", ["k"], ["k"])],
        )
        graph = build_graph(model)
        assert graph.edges[0].cardinality is Cardinality.N_TO_N


class TestPathFinding:
    def test_direct_path(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("orders", ["id"], ["id", "customer_id"]),
                _ds("customers", ["id"], ["id"]),
            ],
            relationships=[
                _rel("r", "orders", "customers", ["customer_id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        paths = graph.find_paths(
            normalize_identifier("orders"), normalize_identifier("customers")
        )
        assert len(paths) == 1
        assert len(paths[0]) == 1

    def test_multi_hop_path_discoverable(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("line_items", ["order_id"], ["order_id"]),
                _ds("orders", ["id"], ["id", "customer_id"]),
                _ds("customers", ["id"], ["id"]),
            ],
            relationships=[
                _rel("li_o", "line_items", "orders", ["order_id"], ["id"]),
                _rel("o_c", "orders", "customers", ["customer_id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        paths = graph.find_paths(
            normalize_identifier("line_items"), normalize_identifier("customers")
        )
        assert len(paths) == 1
        assert len(paths[0]) == 2

    def test_no_path_returns_empty(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("a", ["id"], ["id"]),
                _ds("b", ["id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        paths = graph.find_paths(normalize_identifier("a"), normalize_identifier("b"))
        assert paths == ()

    def test_same_endpoint_returns_empty_path(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_ds("a", ["id"], ["id"])],
        )
        graph = build_graph(model)
        paths = graph.find_paths(normalize_identifier("a"), normalize_identifier("a"))
        assert paths == ((),)

    def test_two_paths_between_same_endpoints(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("a", ["id"], ["id", "b_id", "c_id"]),
                _ds("b", ["id"], ["id", "d_id"]),
                _ds("c", ["id"], ["id", "d_id"]),
                _ds("d", ["id"], ["id"]),
            ],
            relationships=[
                _rel("a_b", "a", "b", ["b_id"], ["id"]),
                _rel("a_c", "a", "c", ["c_id"], ["id"]),
                _rel("b_d", "b", "d", ["d_id"], ["id"]),
                _rel("c_d", "c", "d", ["d_id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        paths = graph.find_paths(normalize_identifier("a"), normalize_identifier("d"))
        # Two simple paths: a→b→d and a→c→d
        assert len(paths) == 2


class TestAdjacency:
    def test_neighbors_both_sides(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[
                _ds("orders", ["id"], ["id", "customer_id"]),
                _ds("customers", ["id"], ["id"]),
            ],
            relationships=[
                _rel("r", "orders", "customers", ["customer_id"], ["id"]),
            ],
        )
        graph = build_graph(model)
        assert len(graph.neighbors(normalize_identifier("orders"))) == 1
        assert len(graph.neighbors(normalize_identifier("customers"))) == 1
