# C17 development standard

Applies to new or substantially modified C sources, native bridges, IPC, parsers and test harnesses. Platform toolchain exceptions must be documented in the PR. This standard supplements [AGENTS.md](../../AGENTS.md) and [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Build and ABI

- Default to ISO C17 with `-std=c17 -Wall -Wextra -Wpedantic -Werror` for new C code; use GCC and Clang on supported host targets. Do not suppress warnings globally.
- Specify ownership, lifetime, thread-safety, error returns and buffer lengths for public APIs. Document C ABI layout/packing/endian assumptions when crossing JNI or IPC boundaries.
- Avoid implementation-defined assumptions about pointer width, `char` signedness, alignment, overflow and atomics. Keep Android NDK-specific flags explicit.
- Test debug/optimized builds; run ASan and UBSan on supported host platforms. Sanitizers do not substitute for actual device E2E.

## Safety

- Validate input lengths and integer ranges *before* pointer arithmetic, allocation or parsing; check `SIZE_MAX` bounds. Initialize state and use bounded APIs.
- Never use `gets`; prohibit unbounded `strcpy`, `strcat`, `sprintf` on untrusted data. `strncpy` is not automatically safe.
- Explicitly define malloc/free and descriptor ownership, transfer and cleanup on every failure. Check short I/O, EINTR/EAGAIN/EOF, cancellation and syscall errors.
- Do not expose credentials or private buffers in logs, traces or error messages. Reject malformed IPC/protocol input; validate message version and replay/session state.
- For concurrency, state synchronization primitives, memory ordering and teardown behavior. Exercise multi-process shared-state, races, replay, crash and signal/fault injection.

## Evidence and performance

- Tests: deterministic unit and negative tests; integration for each real boundary; authorized on-target E2E for Android/native/socket paths. Provide precise steps, environment and exact SHA.
- Add fuzz harnesses for new externally supplied byte parsers. Include static analysis when applicable. TSan is conditional on platform support and not a universal gate.
- Benchmark on established persistent TCP connections separately from TCP connect/TLS/reauth/reconnect. Record both metrics; setup is **excluded only from steady-state latency**, never hidden.
- Report warm-up, payload, concurrency, duration, throughput, p50/p95/p99, CPU, RSS, baseline, variance and regression threshold; use optimized builds and comparable machines.
- CI and independent review remain mandatory. No merge/cutover or RDC interruption from benchmarks.

## Gate rollout

`bash scripts/check_c17.sh` runs a compiler-and-sanitizer smoke suite; CI invokes it for C-standard/test changes. This is a foundation only, not proof of sanitizer coverage across all native production code. Enroll existing native targets individually when discovered, without silently breaking unrelated PRs.
