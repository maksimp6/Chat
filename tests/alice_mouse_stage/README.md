# Alice Mouse v10 + Pet Cursor Preview (isolated stage)

Redmi 9 source snapshot, 2026-10-10. No root deployment, APK installation, or cutover.

C v10: fault handling, singleton socket ownership, BTN/SYN recovery. SignedBroker v4: replay and monotonic sequence. Pet cursor: visual-only transparent preview, not input injection. HTTP/C fixtures are test-only.

Termux checks from this directory:

```sh
clang -Wall -Wextra -Werror -O2 -DALICE_DAEMON_SOURCE='"alice_mouse_daemon_v10_candidate.c"' test_alice_mouse_daemon_faults.c -o ./alice-faults && ./alice-faults
clang -Wall -Wextra -Werror -O2 -DALICE_DAEMON_SOURCE='"alice_mouse_daemon_v10_candidate.c"' test_alice_mouse_button_failures.c -o ./alice-buttons && ./alice-buttons
python -m pytest -q test_alice_mouse_signed_broker_v4_candidate.py test_cursor_stage.py
```

Do not install the candidate as production. The live root bridge still accepts plaintext UID-authorized commands and root-owned immutable source plus authenticated end-to-end dispatch remain unverified. Real /dev/uinput UI_DEV_DESTROY and BTN_UP behavior requires a gated physical E2E.
