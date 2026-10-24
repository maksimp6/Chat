# Replay fence — single-use signed input sessions

An independent RED test found that calling `ProtectedVerifier.provision()` again
for an existing epoch/session reset its replay sequence to zero, accepting an
already executed signed key-down packet for a second time. A backend failure
followed by re-provisioning, or a newly constructed verifier with the same
`SessionAuthority`, also replayed the old packet.

`SessionAuthority` now records one claimed (epoch, session) under its lock.
`ProtectedVerifier.provision()` rejects any second claim of that pair,
including concurrent attempts from multiple verifier instances, **before**
resetting the local sequence. A failed handoff must not consume the claim.
To recover, authenticated Live Server code must call
`SessionAuthority.authorize()` again to mint a fresh epoch and session;
the signer resets its sequence when the session changes. The 2-second CI gate
and independent security tests cover duplicate claims, failed backend recovery,
new verifier construction, concurrent claims, and fresh-epoch acceptance.

**Scope boundary:** This fence works only for verifier instances sharing the
same trusted in-memory `SessionAuthority`. It is NOT a durable or cross-process
replay fence. A root-owned persistent authority/epoch ledger or authenticated
one-time root handoff must reject recycled sessions after process crash,
snapshot restore, or peer restart. Rotation must revoke the previously active
root verifier before untrusted input is permitted. No real /dev/uinput, root
service, daemon or SELinux changes are made here. This draft is not approved
for production cutover.
