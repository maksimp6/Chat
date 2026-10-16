from steel.guardian.admission import Approval, Decision, Limits, Request, admit


def limits(wall=10, memory=1024, cpu=5, output=512, tokens=100):
    return Limits(wall, memory, cpu, output, tokens)


def policy(approved=True, capabilities=frozenset({"inspect"}), ceiling=None):
    return Approval(capabilities, ceiling or limits(), approved)


def test_valid_request():
    assert admit(Request(frozenset({"inspect"}), limits()), policy()) is Decision.ALLOW


def test_missing_approval():
    assert admit(Request(frozenset(), limits()), policy(approved=False)) is Decision.MISSING_APPROVAL


def test_capability_escalation():
    assert admit(Request(frozenset({"execute"}), limits()), policy()) is Decision.CAPABILITY_DENIED


def test_budget_overrun():
    assert admit(Request(frozenset(), limits(wall=11)), policy()) is Decision.BUDGET_EXCEEDED


def test_zero_and_negative_limits():
    for value in (0, -1):
        assert admit(Request(frozenset(), limits(wall=value)), policy()) is Decision.INVALID_LIMIT


def test_boolean_limit_rejected():
    assert admit(Request(frozenset(), limits(wall=True)), policy()) is Decision.INVALID_LIMIT


def test_invalid_approval_ceiling():
    assert admit(Request(frozenset(), limits()), policy(ceiling=limits(memory=0))) is Decision.INVALID_LIMIT
