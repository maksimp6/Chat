from invocation_context import InvocationContext


def test_context_round_trip():
    context = InvocationContext(
        session_id="s1",
        conversation_id="c1",
        invocation_id="i1",
        trace_id="t1",
        user_id="u1",
        metadata={"source": "test"},
    )
    restored = InvocationContext.from_dict(context.as_dict())
    assert restored == context


def test_create_generates_unique_invocation_and_trace_ids():
    first = InvocationContext.create("s1", "c1")
    second = InvocationContext.create("s1", "c1")

    assert first.invocation_id != second.invocation_id
    assert first.trace_id != second.trace_id
    assert first.session_id == second.session_id == "s1"
    assert first.conversation_id == second.conversation_id == "c1"


def test_child_metadata_does_not_mutate_context():
    context = InvocationContext("s1", "c1", "i1", "t1", metadata={"source": "test"})
    child = context.child_metadata(step=1)
    assert child == {"source": "test", "step": 1}
    assert context.metadata == {"source": "test"}


def test_metadata_cannot_be_mutated_through_context():
    context = InvocationContext("s1", "c1", "i1", "t1", metadata={"source": "test"})

    try:
        context.metadata["step"] = 1
    except TypeError:
        pass
    else:
        raise AssertionError("InvocationContext metadata must be immutable")


def test_required_identifiers_are_validated():
    for kwargs in (
        {"session_id": "", "conversation_id": "c1", "invocation_id": "i1", "trace_id": "t1"},
        {"session_id": "s1", "conversation_id": "", "invocation_id": "i1", "trace_id": "t1"},
        {"session_id": "s1", "conversation_id": "c1", "invocation_id": "", "trace_id": "t1"},
        {"session_id": "s1", "conversation_id": "c1", "invocation_id": "i1", "trace_id": ""},
    ):
        try:
            InvocationContext(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("Missing identifiers must be rejected")
