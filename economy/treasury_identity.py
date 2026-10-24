"""Shared local identity for Logos data and accounting; no visitor authentication."""

from core.env import env_get


class TreasuryIdentityError(ValueError):
    """Compatibility error type for existing callers."""


def get_current_owner_id(*, required: bool = True) -> str:
    """Use one installation identity for every browser, CLI and MCP client."""
    configured = str(env_get("LOGOS_OWNER_ID") or "").strip()
    if configured:
        return configured
    from storage.db import get_store
    from kristall import KristallError

    try:
        store = get_store()
    except KristallError:
        return "default"
    key = "installation/shared-owner-id"
    with store.transaction() as tx:
        selected = tx.get(key)
        if selected:
            return str(selected)
        # Preserve a lone existing ledger instead of silently showing a fresh zero balance.
        owners = sorted({
            str(account["owner_id"])
            for account in store.items(prefix="treasury/account/").values()
            if account.get("owner_id")
        })
        selected = owners[0] if len(owners) == 1 else "default"
        tx.set(key, selected)
    return selected
