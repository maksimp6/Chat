from invocation_context import InvocationContext


def test_context_round_trip():
    context = InvocationContext(
        session_id="s1",
        conversation_id="c1",
        invocation_id="i1",
        trace_id="t1",
        metadata={"source": "test"},
    )
    restored = InvocationContext.from_dict(context.as_dict())
    assert restored == context


def test_child_metadata_does_not_mutate_context():
    context = InvocationContext("s1", "c1", "i1", "t1", {"source": "test"})
    child = context.child_metadata(step=1)
    assert child == {"source": "test", "step": 1}
    assert context.metadata == {"source": "test"}
