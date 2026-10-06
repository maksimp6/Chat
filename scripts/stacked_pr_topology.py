"""Deterministic stacked-PR topology model for merge admission."""

from __future__ import annotations

from dataclasses import dataclass


class StackTopologyError(ValueError):
    """Fail-closed topology error."""


@dataclass(frozen=True, slots=True)
class PullNode:
    number: int
    head: str
    base: str
    state: str = "open"
    absorbed: bool = False


@dataclass(frozen=True, slots=True)
class StackTopology:
    root: PullNode
    descendants: tuple[PullNode, ...]
    open_descendants: tuple[PullNode, ...]
    unabsorbed_closed: tuple[PullNode, ...]



@dataclass(frozen=True, slots=True)
class AncestorChain:
    current: PullNode
    ancestors: tuple[PullNode, ...]
    root: PullNode


def build_ancestor_chain(
    nodes: tuple[PullNode, ...],
    pr_number: int,
    target: str = "master",
) -> AncestorChain:
    by_number = {node.number: node for node in nodes}
    current = by_number.get(pr_number)
    if current is None:
        raise StackTopologyError("pull request not found")

    heads: dict[str, list[PullNode]] = {}
    for node in nodes:
        heads.setdefault(node.head, []).append(node)

    ancestors: list[PullNode] = []
    seen = {current.number}
    cursor = current
    while cursor.base != target:
        owners = heads.get(cursor.base, [])
        if not owners:
            raise StackTopologyError("ancestor chain broken")
        if len(owners) != 1:
            raise StackTopologyError("ambiguous ancestor")
        parent = owners[0]
        if parent.number in seen:
            raise StackTopologyError("stack topology cycle")
        if parent.state != "open":
            raise StackTopologyError("stale ancestor")
        ancestors.append(parent)
        seen.add(parent.number)
        cursor = parent

    return AncestorChain(current=current, ancestors=tuple(ancestors), root=cursor)


def build_stack(
    nodes: tuple[PullNode, ...],
    root_number: int,
    target: str = "master",
) -> StackTopology:
    by_number = {node.number: node for node in nodes}
    if len(by_number) != len(nodes):
        raise StackTopologyError("duplicate pull request number")

    root = by_number.get(root_number)
    if root is None:
        raise StackTopologyError("root pull request not found")
    if root.base != target:
        raise StackTopologyError("root pull request must target protected branch")

    heads: dict[str, list[PullNode]] = {}
    for node in nodes:
        heads.setdefault(node.head, []).append(node)
    ambiguous_heads = {head for head, owners in heads.items() if len(owners) > 1}
    if ambiguous_heads:
        raise StackTopologyError("ambiguous ancestor")

    children: dict[str, list[PullNode]] = {}
    for node in nodes:
        children.setdefault(node.base, []).append(node)

    descendants: list[PullNode] = []
    visiting: set[int] = set()
    visited: set[int] = set()

    def walk(parent: PullNode) -> None:
        if parent.number in visiting:
            raise StackTopologyError("stack topology cycle")
        visiting.add(parent.number)
        for child in children.get(parent.head, []):
            if child.number == parent.number or child.number in visiting:
                raise StackTopologyError("stack topology cycle")
            if child.number in visited:
                continue
            descendants.append(child)
            walk(child)
            visited.add(child.number)
        visiting.remove(parent.number)

    walk(root)
    return StackTopology(
        root=root,
        descendants=tuple(descendants),
        open_descendants=tuple(node for node in descendants if node.state == "open"),
        unabsorbed_closed=tuple(
            node for node in descendants if node.state == "closed" and not node.absorbed
        ),
    )


def topology_blockers(topology: StackTopology) -> tuple[str, ...]:
    blockers = [f"open descendant #{node.number}" for node in topology.open_descendants]
    blockers.extend(
        f"closed descendant #{node.number} delta not absorbed"
        for node in topology.unabsorbed_closed
    )
    return tuple(blockers)


__all__ = [
    "AncestorChain",
    "PullNode",
    "StackTopology",
    "StackTopologyError",
    "build_ancestor_chain",
    "build_stack",
    "topology_blockers",
]
