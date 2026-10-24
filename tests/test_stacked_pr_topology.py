import pytest

from scripts.stacked_pr_topology import (
    PullNode,
    StackTopologyError,
    build_stack,
    topology_blockers,
)


def node(number, head, base, state="open", absorbed=False):
    return PullNode(number, head, base, state, absorbed)


def test_root_without_children_is_clear():
    topology = build_stack((node(1, "root", "master"),), 1)
    assert topology.descendants == ()
    assert topology_blockers(topology) == ()


def test_open_child_and_grandchild_block_root():
    topology = build_stack(
        (
            node(1, "root", "master"),
            node(2, "child", "root"),
            node(3, "grandchild", "child"),
        ),
        1,
    )
    assert {item.number for item in topology.open_descendants} == {2, 3}
    assert set(topology_blockers(topology)) == {
        "open descendant #2",
        "open descendant #3",
    }


def test_unrelated_open_pr_does_not_block_root():
    topology = build_stack(
        (
            node(1, "root", "master"),
            node(2, "child", "root", state="closed", absorbed=True),
            node(99, "other", "master"),
        ),
        1,
    )
    assert [item.number for item in topology.descendants] == [2]
    assert topology_blockers(topology) == ()


def test_closed_unabsorbed_child_blocks_but_absorbed_child_does_not():
    blocked = build_stack(
        (node(1, "root", "master"), node(2, "child", "root", state="closed")),
        1,
    )
    clear = build_stack(
        (
            node(1, "root", "master"),
            node(2, "child", "root", state="closed", absorbed=True),
        ),
        1,
    )
    assert topology_blockers(blocked) == ("closed descendant #2 delta not absorbed",)
    assert topology_blockers(clear) == ()


def test_root_must_target_protected_branch():
    with pytest.raises(StackTopologyError, match="protected branch"):
        build_stack((node(2, "child", "root"),), 2)


def test_duplicate_head_is_ambiguous_ancestor():
    with pytest.raises(StackTopologyError, match="ambiguous ancestor"):
        build_stack(
            (
                node(1, "root", "master"),
                node(2, "shared", "root"),
                node(3, "shared", "root"),
            ),
            1,
        )


def test_cyclic_or_ambiguous_topology_fails_closed():
    with pytest.raises(StackTopologyError):
        build_stack(
            (
                node(1, "root", "master"),
                node(2, "child", "root"),
                node(3, "loop", "child"),
                node(4, "child", "loop"),
            ),
            1,
        )
