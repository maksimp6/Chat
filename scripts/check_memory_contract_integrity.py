"""Pinned SHA-256 admission gate for accepted Memory DB tests and legacy source.

CI executes this policy from a separate immutable commit checkout.
Updating accepted contracts requires a new reviewed policy pin.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from typing import Mapping

# Approved: #1054 (core and legacy), #1104 (legacy fast), initial #1106 (core fast).
FROZEN_SHA256: dict[str, str] = {
    "agent_memory/file_memory_db.py": "5d5ee883db178f236bf0486fe5b488a3934af05a117c06138d69c2f4063d00f9",
    "tests/test_memory_explicit_commit_contract.py": "a8de71edaa01dbe7c4b2615818fc7f7fe8c92a699776c6db1a3158b29c4ac56c",
    "tests/test_memory_v1_acceptance.py": "31b402e1c65e4a313df0d1c695fe6846ead855cbfc964f79d275fc7edd6e71b9",
    "tests/test_memory_store.py": "37b6869a108fd028dca17af40a759e0212c78d2afd3da74c0934e16ab6313c37",
    "tests/test_memory_standalone_boundary.py": "1083807db4fb6ec0d0a1fe7dc4ded4279b03574b4e91e91502c3951af5d03522",
    "tests/test_ci_fast_contracts.py": "c14e53f452c9532a8c5ff6390c0347fb3f0b5fb4e520f29c18ad8c8a9f031ca3",
    "tests/test_memory_fast_core_contracts.py": "6144f7d2d104ef93c2908335e991b7edcde5bf9c85c0b40f21aec9a3b1e1c3eb",
    "scripts/run_fast_contracts.py": "5cdb4b2ff663b2982ceffcb843dfb914f2376c3e252a7f1c7a151c786364e389",
    "scripts/check_memory_fast_budget.py": "d7a678dd35889a87dd5bb852f2e097bc0e524f6147d409960b47ecfc9a840d9a",
    "scripts/run_fast_contracts_trusted.py": "d536d6fcf65406e57f4584bacd8edb67fc76f30074d7d1a45926caa60436a395",
    "tests/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "tests/test_ci_memory_trusted_runner.py": "7c8dcbdc05e36704f43ab2e521e379a5c2c6d051f3a14020bf7925d1507bda44",
    "tests/test_ci_develop_workflows.py": "0c56f55f87111f83d14e5e602b03dfb5117d4ce4d0988cc23556e7d16402d4e5",
    "tests/test_ci_platform_changes.py": "b5c46dd508ca3277d79e7fdff07919d1b74875522b908867a4b86c1a4e4b12aa",
    "tests/test_merge_readiness.py": "f9e1e71b90945922a04cdaf9f9fca51e5c2efcb031d8e646d5f35e69d6861302",
    "tests/test_ci_memory_contract_integrity.py": "43dd5ac0875fa027410f60627e6ac0bcd8644399a6ce14388da1819b2cb3f193",
}

FORBIDDEN_IMPORT_HOOKS = ("scripts/__init__.py", "sitecustomize.py", "usercustomize.py")


def verify(root: Path, expected: Mapping[str, str] = FROZEN_SHA256) -> list[str]:
    """Fail closed on missing, symlinked or modified accepted artifacts."""
    errors: list[str] = []
    for relative, approved_digest in sorted(expected.items()):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            errors.append(f"{relative}: missing, symlinked, or not a regular file")
            continue
        if sha256(path.read_bytes()).hexdigest() != approved_digest:
            errors.append(f"{relative}: SHA-256 differs from approved snapshot")
    for relative in FORBIDDEN_IMPORT_HOOKS:
        path = root / relative
        if path.exists() or path.is_symlink():
            errors.append(f"{relative}: unapproved import hook")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    failures = verify(args.root)
    if failures:
        for failure in failures:
            print(f"BLOCKED: {failure}")
        return 1
    print(f"VERIFIED: {len(FROZEN_SHA256)} frozen Memory DB artifacts and legacy implementation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
