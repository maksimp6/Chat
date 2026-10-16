import pytest

from steel.guardian.admission import Approval, Decision, Limits, Request, admit


def limits(wall=10, memory=1024, cpu=5, output=512, tokens=100):
    return Limits(wall, memory, cpu, output, tokens)


def policy(approved=True, capabilities=frozenset({"inspect"}), ceiling=None):
    return Approval(capabilities, ceiling if ceiling is not None else limits(), approved)


def test_valid_request():
    assert admit(Request(frozenset({"inspect"}), limits()), policy()) is Decision.ALLOW


def test_missing_approval():
    assert (
        admit(Request(frozenset(), limits()), policy(approved=False)) is Decision.MISSING_APPROVAL
    )


def test_capability_escalation_with_known_capability():
    request = Request(frozenset({"inspect"}), limits())
    approval = policy(capabilities=frozenset())
    assert admit(request, approval) is Decision.CAPABILITY_DENIED


def test_unknown_capability_is_denied_even_when_approved():
    request = Request(frozenset({"unknown"}), limits())
    approval = policy(capabilities=frozenset({"unknown"}))
    assert admit(request, approval) is Decision.MISSING_APPROVAL


@pytest.mark.parametrize(
    "field", ["wall_seconds", "memory_bytes", "cpu_seconds", "output_bytes", "token_count"]
)
def test_budget_overrun_each_field(field):
    values = vars(limits()) | {field: getattr(limits(), field) + 1}
    assert admit(Request(frozenset(), Limits(**values)), policy()) is Decision.BUDGET_EXCEEDED


@pytest.mark.parametrize(
    "field", ["wall_seconds", "memory_bytes", "cpu_seconds", "output_bytes", "token_count"]
)
@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10", None])
def test_invalid_request_limit_each_field(field, bad):
    values = vars(limits()) | {field: bad}
    assert admit(Request(frozenset(), Limits(**values)), policy()) is Decision.INVALID_LIMIT


def test_invalid_approval_ceiling():
    approval = Approval(frozenset({"inspect"}), limits(memory=0), True)
    assert admit(Request(frozenset(), limits()), approval) is Decision.INVALID_LIMIT


@pytest.mark.parametrize(
    "capabilities", [{"inspect"}, frozenset({""}), frozenset({1}), "inspect", None]
)
def test_invalid_request_capabilities(capabilities):
    assert admit(Request(capabilities, limits()), policy()) is Decision.CAPABILITY_DENIED


@pytest.mark.parametrize("capabilities", [{"inspect"}, frozenset({""}), frozenset({1}), None])
def test_invalid_approval_capabilities(capabilities):
    approval = Approval(capabilities, limits(), True)
    assert admit(Request(frozenset(), limits()), approval) is Decision.MISSING_APPROVAL


@pytest.mark.parametrize("approval", [None, "yes", Approval(frozenset(), limits(), 1)])
def test_malformed_approval(approval):
    assert admit(Request(frozenset(), limits()), approval) is Decision.MISSING_APPROVAL


def test_malformed_request():
    assert admit(None, policy()) is Decision.INVALID_LIMIT


def test_invalid_request_limits_type():
    assert admit(Request(frozenset(), None), policy()) is Decision.INVALID_LIMIT


def test_invalid_approval_ceiling_type():
    approval = Approval(frozenset({"inspect"}), None, True)
    assert admit(Request(frozenset(), limits()), approval) is Decision.INVALID_LIMIT
