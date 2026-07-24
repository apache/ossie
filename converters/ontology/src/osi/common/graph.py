from __future__ import annotations

import warnings
from collections import defaultdict
from typing import TypeVar

T = TypeVar("T")


def topological_sort(nodes: list[T], edges: list[tuple[T, T]]) -> list[T]:
    order = _topological_sort(nodes, edges)
    if order is None:
        raise ValueError("The graph contains a cycle")
    return order


def topological_sort_break_cycles(nodes: list[T], edges: list[tuple[T, T]]) -> tuple[list[T], list[tuple[T, T]]]:
    order, removed_edges = _topological_sort_break_cycles(nodes, edges)
    # `order` should always exist; defensive check:
    if order is None:
        raise ValueError("Could not break cycles to obtain a topological order")

    return order, removed_edges


def is_acyclic_graph(nodes: list[T], edges: list[tuple[T, T]]) -> bool:
    return _topological_sort(nodes, edges) is not None


def _find_cycle_closing_edge_index(
        nodes: list[T],
        edge_list: defaultdict[T, list[tuple[T, int]]],
        active: list[bool],
        remaining_set: set[T],
) -> int | None:
    """
    Find a cycle in the active subgraph induced by remaining_set and return the
    index of a "cycle-closing" edge (a back-edge u->v where v is on the recursion stack).
    """
    visited: set[T] = set()
    on_stack: set[T] = set()

    def dfs(u: T) -> int | None:
        visited.add(u)
        on_stack.add(u)

        for v, eidx in edge_list.get(u, []):
            if not active[eidx]:
                continue
            if v not in remaining_set:
                continue

            if v not in visited:
                found = dfs(v)
                if found is not None:
                    return found
            elif v in on_stack:
                # Back-edge found: u -> v closes a directed cycle
                return eidx

        on_stack.remove(u)
        return None

    for start in nodes:
        if start in remaining_set and start not in visited:
            found = dfs(start)
            if found is not None:
                return found

    return None


def _topological_sort_break_cycles(nodes: list[T], edges: list[tuple[T, T]]) -> tuple[list[T] | None, list[tuple[T, T]]]:
    """
    Returns (topological_order, removed_edges).

    Strategy:
      - Run a Kahn-like process.
      - When it gets stuck, detect a real cycle in the remaining subgraph via DFS
        and remove the cycle-closing edge (back-edge) from that cycle.
      - Continue until all nodes can be processed.
      - Then run a clean topological sort once on the pruned edge list.
    """
    node_set = set(nodes)

    edge_list: defaultdict[T, list[tuple[T, int]]] = defaultdict(list)
    active = [True] * len(edges)

    in_degree: dict[T, int] = {n: 0 for n in nodes}
    for idx, (src, tgt) in enumerate(edges):
        if src not in node_set or tgt not in node_set:
            active[idx] = False
            continue
        edge_list[src].append((tgt, idx))
        in_degree[tgt] += 1

    processed: set[T] = set()
    removed_edges: list[tuple[T, T]] = []

    work: list[T] = [n for n in nodes if in_degree.get(n, 0) == 0]

    while len(processed) < len(nodes):
        if work:
            n = work.pop()
            if n in processed:
                continue
            processed.add(n)

            for neighbour, eidx in edge_list.get(n, []):
                if not active[eidx]:
                    continue
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    work.append(neighbour)
            continue

        remaining_set = {n for n in nodes if n not in processed}

        edge_idx = _find_cycle_closing_edge_index(
            nodes=nodes,
            edge_list=edge_list,
            active=active,
            remaining_set=remaining_set,
        )
        if edge_idx is None:
            raise ValueError("Cycle suspected but could not identify a cycle edge to remove")

        src, tgt = edges[edge_idx]
        active[edge_idx] = False
        removed_edges.append((src, tgt))
        warnings.warn(f"Cycle detected: removing cycle-closing edge {src!r} -> {tgt!r}")

        # Update in_degree to reflect edge removal
        in_degree[tgt] -= 1
        if in_degree[tgt] == 0:
            work.append(tgt)

    cleaned_edges = [e for i, e in enumerate(edges) if active[i]]
    order = _topological_sort(nodes, cleaned_edges)
    if order is None:
        raise ValueError("Graph is still cyclic after cycle-breaking edge removals")

    return order, removed_edges


def _topological_sort(nodes: list[T], edges: list[tuple[T, T]]) -> list[T] | None:
    order = []

    # simple implementation of Kahn's Algorithm

    # index edges
    edge_list = defaultdict(list)
    for src, tgt in edges:
        edge_list[src].append(tgt)

    # compute in_degree of nodes
    in_degree = dict()
    for _, tgt in edges:
        if tgt in in_degree:
            in_degree[tgt] = in_degree[tgt] + 1
        else:
            in_degree[tgt] = 1

    # start the working list with nodes that don't have incoming edges
    work = list(filter(lambda n: n not in in_degree, nodes))
    while work:
        n = work.pop()
        order.append(n)
        for neighbour in edge_list[n]:
            new_in_degree = in_degree[neighbour] - 1
            in_degree[neighbour] = new_in_degree
            if new_in_degree == 0:
                work.append(neighbour)

    # all nodes sorted, return the order
    if len(order) == len(nodes):
        return order

    # some nodes were not sorted, so the graph is cyclic, return None
    return None
