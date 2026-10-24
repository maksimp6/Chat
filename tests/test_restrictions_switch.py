"""LOGOS_UNRESTRICTED lifts the built-in restrictions; off by default."""

import os
from decimal import Decimal

import pytest

from core.restrictions import restrictions_lifted
from economy.budget_controller import BudgetLimitExceeded, CloudBudgetGuard, CloudBudgetLimits
from provider_accounts.quotas import ProviderQuotaExceeded, reserve_request
from remote_runtime import ssh_settings
from remote_runtime.ssh_settings import SSHRuntimeError, assert_operation_allowed
from tool_providers import filesystem
from tool_providers.universal_tool_platform import tool_call_requires_approval


@pytest.fixture()
def unrestricted(monkeypatch):
    monkeypatch.setenv("LOGOS_UNRESTRICTED", "true")


@pytest.fixture()
def restricted(monkeypatch):
    monkeypatch.delenv("LOGOS_UNRESTRICTED", raising=False)


def test_switch_is_off_unless_explicitly_set(restricted):
    assert restrictions_lifted() is False


def test_switch_accepts_only_affirmative_values(monkeypatch):
    monkeypatch.setenv("LOGOS_UNRESTRICTED", "no")
    assert restrictions_lifted() is False
    monkeypatch.setenv("LOGOS_UNRESTRICTED", "1")
    assert restrictions_lifted() is True


def test_approval_gate_is_enforced_by_default(restricted):
    assert tool_call_requires_approval({"requires_approval": True}, {}) is True


def test_approval_gate_is_lifted_with_the_switch(unrestricted):
    assert tool_call_requires_approval({"requires_approval": True}, {}) is False


def test_protected_files_are_refused_by_default(restricted):
    with pytest.raises(PermissionError):
        filesystem._refuse_protected(os.path.join(filesystem.BASE_DIR, ".env"))


def test_protected_files_are_readable_with_the_switch(unrestricted):
    filesystem._refuse_protected(os.path.join(filesystem.BASE_DIR, ".env"))


def test_project_boundary_holds_by_default(restricted):
    with pytest.raises(PermissionError):
        filesystem._get_abs_path("../outside-the-project.txt")


def test_project_boundary_is_lifted_with_the_switch(unrestricted):
    target = filesystem._get_abs_path("../outside-the-project.txt")
    assert not target.startswith(filesystem.BASE_DIR + os.sep)


def test_anonymous_quota_call_is_refused_by_default(restricted, monkeypatch):
    monkeypatch.delenv("LOGOS_QUOTA_REQUIRE_IDENTITY", raising=False)
    with pytest.raises(ProviderQuotaExceeded):
        reserve_request(None)


def test_anonymous_quota_call_passes_with_the_switch(unrestricted):
    assert reserve_request(None) is None


class _Provider:
    def costs_summary(self, *, period, group_by):
        return {"total_cost": "500", "currency": "RUB", "provider": "cloudru"}


def test_cloud_budget_block_applies_by_default(restricted):
    guard = CloudBudgetGuard(_Provider(), CloudBudgetLimits(Decimal("100")))
    with pytest.raises(BudgetLimitExceeded):
        guard.enforce("create_vm")


def test_cloud_budget_block_is_lifted_with_the_switch(unrestricted):
    guard = CloudBudgetGuard(_Provider(), CloudBudgetLimits(Decimal("100")))
    assert guard.enforce("create_vm")["status"] == "block"


_SSH_SETTINGS = {
    "enabled": True,
    "read_only": False,
    "allow_command_execution": True,
    "allow_write_operations": True,
    "command_allowlist": [],
    "allow_privileged_operations": False,
    "approval_required_for_privileged": True,
}


def test_ssh_allowlist_applies_by_default(restricted, monkeypatch):
    monkeypatch.setattr(ssh_settings, "get_settings", lambda: dict(_SSH_SETTINGS))
    with pytest.raises(SSHRuntimeError):
        assert_operation_allowed("execute", command="uname -a")


def test_ssh_allowlist_is_lifted_with_the_switch(unrestricted, monkeypatch):
    monkeypatch.setattr(ssh_settings, "get_settings", lambda: dict(_SSH_SETTINGS))
    assert_operation_allowed("execute", command="uname -a; id")


def test_ssh_owner_enable_switch_still_applies_with_the_restriction_switch(unrestricted, monkeypatch):
    disabled = dict(_SSH_SETTINGS, enabled=False)
    monkeypatch.setattr(ssh_settings, "get_settings", lambda: disabled)
    with pytest.raises(SSHRuntimeError):
        assert_operation_allowed("execute", command="uname -a")
