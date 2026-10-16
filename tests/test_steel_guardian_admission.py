import pytest

from steel.guardian.admission import Approval, Decision, Limits, Request, admit


def limits(wall=10, memory=1024, cpu=5, output=512, tokens=100):
    return Limits(wall, memory, cpu, output, tokens)


def policy(approved=True, capabilities=frozenset({"inspect"}), ceiling=None):
    return Approval(capabilities, ceiling if ceiling is not None else limits(), approved)


def test_valid_request():
    assert admit(Request(frozenset({"inspect"}), limits()), policy()) is Decision.ALLOW


def test_missing_approval():
    assert admit(Request(frozenset(), limits()), policy(approved=False)) is Decision.MISSING_APPROVAL


def test_capability_escalation():
    assert admit(Request(frozenset({"execute"}), limits()), policy()) is Decision.CAPABILITY_DENIED


@pytest.mark.parametrize("field", ["wall_seconds", "memory_bytes", "cpu_seconds", "output_bytes", "token_count"])
def test_budget_overrun_each_field(field):
    values = vars(limits()) | {field: getattr(limits(), field) + 1}
    assert admit(Request(frozenset(), Limits(**values)), policy()) is Decision.BUDGET_EXCEEDED


@pytest.mark.parametrize("field", ["wall_seconds", "memory_bytes", "cpu_seconds", "output_bytes", "token_count"])
@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10", None])
def test_invalid_request_limit_each_field(field, bad):
    values = vars(limits()) | {field: bad}
    assert admit(Request(frozenset(), Limits(**values)), policy()) is Decision.INVALID_LIMIT


def test_invalid_approval_ceiling():
    assert admit(Request(frozenset(), limits()), policy(ceiling=limits(memory=0))) is Decision.INVALID_LIMIT


@pytest.mark.parametrize("capabilities", [{"inspect"}, frozenset({""}), frozenset({1}), "inspect", None])
def test_invalid_request_capabilities(capabilities):
    assert admit(Request(capabilities, limits()), policy()) is Decision.CAPABILITY_DENIED


@pytest.mark.parametrize("capabilities", [{"inspect"}, frozenset({""}), frozenset({1}), None])
def test_invalid_approval_capabilities(capabilities):
    assert admit(Request(frozenset(), limits()), policy(capabilities=capabilities)) is Decision.CAPABILITY_DENIED


@pytest.mark.parametrize("approval", [None, "yes", Approval(frozenset(), limits(), 1)])
def test_malformed_approval(approval):
    assert admit(Request(frozenset(), limits()), approval) is Decision.MISSING_APPROVAL


def test_malformed_request():
    assert admit(None, policy()) is Decision.INVALID_LIMIT


def test_request_and_ceiling_types():
    assert admit(Request(frozenset(), None), policy()) is Decision.INVALID_LIMIT
    assert admit(Request(frozenset(), limits()), policy(ceiling=None)) is Decision.INVALID_LIMIT
