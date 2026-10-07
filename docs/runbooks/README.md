# Operational runbooks

Status: current  
Owner: Infra / Release / Docs

This directory is the canonical home for new operational runbooks. Existing runbooks elsewhere may migrate incrementally to avoid conflicts with active work.

Every runbook must include:

- scope and owner;
- prerequisites and least-privilege permissions;
- safety / destructive-action warning;
- exact commands or UI actions;
- expected machine-verifiable evidence;
- timeout/failure handling;
- rollback or recovery path;
- final live verification;
- related canonical issue/ADR.

A runbook must not claim success from configuration validation alone. For external services, prove the real target resource and exact deployed revision where possible.
