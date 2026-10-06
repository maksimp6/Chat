"""Plan fail-closed top-down restacks for stacked pull requests."""

from __future__ import annotations

from dataclasses import dataclass

from scripts.stacked_pr_topology import PullNode, StackTopologyError, build_stack


@dataclass(frozen=True, slots=True)
class RestackStep:
    pr_number: int
    child_head: str
    parent_head: str


def plan_restack(
    nodes: tuple[PullNode, ...],
    root_number: int,
    changed_pr_number: int,
) -> tuple[RestackStep, ...]:
    topology = build_stack(nodes, root_number)
    all_nodes = (topology.root, *topology.descendants)
    by_number = {node.number: node for node in all_nodes}
    changed = by_number.get(changed_pr_number)
    if changed is None:
        raise StackTopologyError("changed pull request is outside stack")

    children: dict[str, list[PullNode]] = {}
    for node in topology.descendants:
        children.setdefault(node.base, []).append(node)

    steps: list[RestackStep] = []

    def walk(parent: PullNode) -> None:
        for child in children.get(parent.head, []):
            steps.append(
                RestackStep(
                    pr_number=child.number,
                    child_head=child.head,
                    parent_head=parent.head,
                )
            )
            walk(child)

    walk(changed)
    return tuple(steps)


__all__ = ["RestackStep", "plan_restack"]
