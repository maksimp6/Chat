import pytest

from scripts.stacked_pr_restack import plan_restack
from scripts.stacked_pr_topology import PullNode, StackTopologyError


def node(number, head, base):
    return PullNode(number=number, head=head, base=base)


def test_restack_plan_is_top_down_from_changed_root():
    nodes = (
        node(877, "root", "master"),
        node(878, "child", "root"),
        node(879, "grandchild", "child"),
        node(880, "leaf", "grandchild"),
    )
    steps = plan_restack(nodes, 877, 877)
    assert [step.pr_number for step in steps] == [878, 879, 880]
    assert [step.parent_head for step in steps] == ["root", "child", "grandchild"]


def test_restack_plan_from_changed_child_touches_only_its_descendants():
    nodes = (
        node(877, "root", "master"),
        node(878, "child", "root"),
        node(879, "grandchild", "child"),
        node(999, "other-root", "master"),
    )
    steps = plan_restack(nodes, 877, 878)
    assert [step.pr_number for step in steps] == [879]


def test_restack_rejects_changed_pr_outside_stack():
    nodes = (
        node(877, "root", "master"),
        node(999, "other-root", "master"),
    )
    with pytest.raises(StackTopologyError, match="outside stack"):
        plan_restack(nodes, 877, 999)
