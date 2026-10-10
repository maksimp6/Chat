# Alice Input integration contract — #1089

This is an isolated Integration & QA-owned test slice, **not a combined driver implementation**. The test compiles the real C keyboard allowlist and enumerates it, then compares every supported Linux keycode against Python Security's root-side signed-command schema. It fails if either dependency is missing or the policies differ; it does not silently skip required checks.

- Baseline `develop` without #1090/#1091: expected RED (dependencies not integrated).
- Temporary combined fixture using exact local Security head `5a48775` and Keyboard head `810738c`: **1 PASS** on Redmi 9; no production code changed.
- When #1090 and #1091 are integrated into `develop`, run this gate on exact PR head in CI. Any future policy drift must fail CI.
- No real `/dev/uinput`, root UID, keyboard events, SELinux changes, RDC changes, merge or cutover.
