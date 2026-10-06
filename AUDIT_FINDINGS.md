# Documentation Drift Audit: maksimp6/Chat master branch

**Date**: 2026-10-06  
**Auditor**: Claude Code  
**Focus Areas**: Memory DB, Secret Store, Chrome/RDC deployment, OAuth, Cloud.ru, CI workflow

## Summary

Found **2 confirmed documentation drifts** and **1 incomplete feature documentation** that require action.

### Confirmed Discrepancies

1. **Memory DB Implementation vs. Documentation** (CRITICAL)
   - **Where**: `docs/memory/overview.md` vs. `agent_memory/file_memory_db.py`, `memory_manager.py`
   - **What the code does**:
     - Merged PRs #856 (#42a7259) and #857 (#b8a5b00) implement FileMemoryDB - a durable, single-file append-only database with verified backup/restore
     - Tests in `tests/test_file_memory_db.py` and `tests/test_memory_backup.py` prove durability
   - **What the docs say**:
     - `docs/memory/overview.md` claims data is stored "in оперативной памяти" (in RAM)
     - Says `memory_manager.py` manages in-memory data
   - **Reality**:
     - `memory_manager.py` still uses legacy SQL (`get_conn()`) for memory storage (creates `global_memory` table)
     - `memory_extractor.py` still uses SQL, not FileMemoryDB
     - New FileMemoryDB exists but is not yet integrated into the application layer
   - **Impact**: Documentation is misleading about current durability model. Users cannot trust described behavior.
   - **Fix**: Update `docs/memory/overview.md` to describe the current state: SQL-backed in-memory extraction + orphaned FileMemoryDB implementation. Document the migration path to full FileMemoryDB adoption (issue #776 Wave 1).

2. **OAuth offline_access Support Not Documented**
   - **Where**: `deploy/chrome-worker/oauth.mjs` vs. architecture/deployment docs
   - **What the code does**:
     - Lines 6-7: `SUPPORTED_SCOPES = ["browser", "offline_access"]`
     - Normalizes and handles offline_access scope for ChatGPT OAuth
     - Tests in `deploy/chrome-worker/oauth.test.mjs` cover offline access end-to-end (#7119e41)
   - **What the docs say**:
     - No mention of offline_access support anywhere in docs/architecture/, docs/platform/, docs/integrations/
     - OAuth documentation is minimal/absent
   - **Impact**: Deployment maintainers don't know this capability exists. Users may assume offline tokens cannot be obtained.
   - **Fix**: Add section to `docs/architecture/chatgpt-browser-tool.md` or create new `docs/platform/oauth-flows.md` documenting offline_access support, ChatGPT token refresh, and scopes.

### Areas With Adequate Documentation

✓ **Secret Store** (#860): `secret_store/core.py` contract matches documented API in `docs/security/cloudru-secret-management.md`  
✓ **RDC Deployment** (#861): SSH deployment lane retired cleanly; no docs reference it  
✓ **Cloud.ru Container Apps**: Detailed documentation in `docs/cloudru-container-apps.md` matches current implementation in `scripts/cloudru_deploy.py`  
✓ **CI Workflow**: Fail-closed merge gate (#496) properly documented in `docs/development/`  
✓ **Backup/Restore**: Contract defined (#780) and implementation proven (#857)

## Recommended Actions

### Immediate (Block Issue #776 Wave 1 Integration)

1. **Create Issue**: "docs(memory): update overview for current SQL-backed model and FileMemoryDB transition"
   - Update `docs/memory/overview.md` to reflect reality
   - Document that FileMemoryDB is implemented but not yet integrated
   - Link to #776 migration plan
2. **Create Doc PR**: "docs(oauth): add offline_access support to ChatGPT OAuth flow"
   - Target: New section in `docs/architecture/chatgpt-browser-tool.md` or standalone `docs/platform/oauth-flows.md`
   - Content: Offline token scope, refresh flow, integration with short-token bridge

### Follow-up (After Issue #776 Wave 1)

- Once `memory_manager.py` and `memory_extractor.py` are migrated to FileMemoryDB, update `docs/memory/overview.md` to reflect durable storage model

## Files Involved

- Code: `agent_memory/file_memory_db.py`, `agent_memory/backup.py`, `memory_manager.py`, `memory_extractor.py`, `deploy/chrome-worker/oauth.mjs`
- Docs: `docs/memory/overview.md`, `docs/architecture/chatgpt-browser-tool.md` (or new), `docs/security/cloudru-secret-management.md`
- Tests: `tests/test_file_memory_db.py`, `tests/test_memory_backup.py`, `deploy/chrome-worker/oauth.test.mjs`
