"""A tool call's owner comes from the host, never from a mapping a caller sends."""

from runtime.dispatcher import TOOL_EXECUTE_OPERATION, RuntimeDispatcher
from tool_providers.registry import ToolRegistry
from tool_providers.universal_tool_platform import UniversalToolExecutor


class RecordingExecutor(UniversalToolExecutor):
    def __init__(self):
        super().__init__(ToolRegistry())
        self.owners = []

    def execute(self, call, **kwargs):
        self.owners.append(call.user_id)
        return {"success": True}


def test_a_mapping_cannot_name_the_owner_of_a_runtime_tool_call(tmp_path):
    executor = RecordingExecutor()
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("unowned", namespace="test", root=str(tmp_path))
    dispatcher.register_tool_executor("unowned", executor)

    dispatcher.dispatch(
        "unowned",
        TOOL_EXECUTE_OPERATION,
        {"call": {"tool_name": "partner.list", "arguments": {}, "user_id": "victim"}},
    )

    assert executor.owners == [None]


def test_an_owned_runtime_runs_its_tool_calls_as_its_owner(tmp_path):
    executor = RecordingExecutor()
    dispatcher = RuntimeDispatcher()
    dispatcher.register_runtime("owned", owner_id="owner-a", namespace="test", root=str(tmp_path))
    dispatcher.register_tool_executor("owned", executor)

    dispatcher.dispatch(
        "owned",
        TOOL_EXECUTE_OPERATION,
        {"call": {"tool_name": "partner.list", "arguments": {}, "user_id": "owner-a"}},
    )

    assert executor.owners == ["owner-a"]
