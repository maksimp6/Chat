"""Shared local identity for Logos data and accounting; no visitor authentication."""

import os
from economy.host import bound, get_store


class TreasuryIdentityError(ValueError):
    """Compatibility error type for existing callers."""


def get_current_owner_id(*, required: bool = True) -> str | None:
    """Use one installation identity for every browser, CLI and MCP client."""
    configured = str(os.environ.get("LOGOS_OWNER_ID") or "").strip()
    if configured:
        return configured

    try:
        store = get_store()
    except bound.KristallError:
        if required:
            raise
        # Do not substitute the "default" owner for a store that failed to open.
        return None
    key = "installation/shared-owner-id"
    with store.transaction() as tx:
        selected = tx.get(key)
        if selected:
            return str(selected)
        # Preserve a lone existing ledger instead of silently showing a fresh zero balance.
        owners = sorted(
            {
                str(account["owner_id"])
                for account in store.items(prefix="treasury/account/").values()
                if account.get("owner_id")
            }
        )
        selected = owners[0] if len(owners) == 1 else "default"
        tx.set(key, selected)
    return selected
