"""Fail-closed, file-native print job lifecycle for an authorized printer adapter.

This module performs no network or physical printer actions. Integration with
Bambu Studio (#1101), preflight (#1109) and device transport remains explicit.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

SHA = re.compile(r"^[a-f0-9]{64}$")
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
BASENAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
STEPS = (
    "REQUESTED", "CAD_READY", "GEOMETRY_VERIFIED", "SLICED", "AUDITED",
    "STAGED", "READY_FOR_USER",
)
TERMINAL = {"COMPLETE", "BLOCKED", "FAILED", "START_UNKNOWN"}


class PrintGateError(ValueError):
    """The requested transition lacks trustworthy evidence or authorization."""


class StartTransport(Protocol):
    def start_once(self, filename: str) -> None:
        """Send exactly one authorized start command; never retry ambiguity."""


def _sha(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise PrintGateError("invalid SHA-256 digest")
    return value


def _basename(value: object, *, sliced: bool = False) -> str:
    if not isinstance(value, str) or not BASENAME.fullmatch(value):
        raise PrintGateError("invalid artifact basename")
    if sliced and not value.endswith(".gcode.3mf"):
        raise PrintGateError("printer requires a sliced .gcode.3mf")
    return value


def _material(value: str) -> str:
    if value not in {"PLA", "PETG"}:
        raise PrintGateError("unsupported material")
    return value


def _owner(owner: str) -> str:
    if not isinstance(owner, str) or not 0 < len(owner.strip()) <= 256:
        raise PrintGateError("trusted owner identity is required")
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


class FileJobStore:
    """Isolated owner-scoped atomic JSON snapshots; no SQL or secret storage."""

    def __init__(self, root: Path):
        if root.is_symlink():
            raise PrintGateError("state directory may not be a symlink")
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _folder(self, owner: str) -> Path:
        folder = self.root / _owner(owner)
        if folder.is_symlink():
            raise PrintGateError("owner folder may not be a symlink")
        folder.mkdir(mode=0o700, exist_ok=True)
        return folder

    def _path(self, owner: str, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise PrintGateError("invalid print job id")
        return self._folder(owner) / (job_id + ".json")

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        fd, name = tempfile.mkstemp(prefix=".print-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def create(self, owner: str, source_ref: str, source_sha256: str, material: str) -> dict:
        """Owner comes from server auth, not model-supplied tool arguments."""
        if not isinstance(source_ref, str) or not 0 < len(source_ref) <= 250:
            raise PrintGateError("source artifact reference required")
        if ".." in source_ref or any(x in source_ref for x in ("\x00", "\n", "\r")):
            raise PrintGateError("invalid source artifact reference")
        _sha(source_sha256)
        _material(material)
        job_id = uuid.uuid4().hex
        data: dict[str, Any] = {
            "schema_version": 1,
            "job_id": job_id,
            "owner_hash": _owner(owner),
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "material": material,
            "machine": "Bambu Lab A1 mini",
            "nozzle_mm": 0.4,
            "state": "REQUESTED",
            "revision": 0,
            "events": [{"state": "REQUESTED", "at": int(time.time())}],
            "artifact": None,
            "start_grant_digest": None,
            "video": None,
        }
        path = self._path(owner, job_id)
        if path.exists():
            raise PrintGateError("duplicate job id")
        self._write(path, data)
        return data

    def read(self, owner: str, job_id: str) -> dict:
        path = self._path(owner, job_id)
        if path.is_symlink():
            raise PrintGateError("job path may not be a symlink")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PrintGateError("missing or corrupted print job") from exc
        if data.get("job_id") != job_id or data.get("owner_hash") != _owner(owner):
            raise PrintGateError("print job identity mismatch")
        return data

    def mutate(self, owner: str, job_id: str, change: Callable[[dict], None]) -> dict:
        path = self._path(owner, job_id)
        lock = path.with_suffix(".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            data = self.read(owner, job_id)
            change(data)
            data["revision"] += 1
            self._write(path, data)
            return data
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _event(data: dict, state: str, proof: Mapping[str, object] | None = None) -> None:
    data["state"] = state
    # Only sanitized evidence is recorded: never include full credentials or tokens.
    evidence = {
        key: proof[key]
        for key in ("artifact_sha256", "remote_sha256", "report_sha256", "filename")
        if proof is not None and key in proof
    }
    data["events"].append({"state": state, "at": int(time.time()), "proof": evidence})


class PrintWorkflow:
    def __init__(self, store: FileJobStore):
        self.store = store

    def advance(self, owner: str, job_id: str, target: str, proof: Mapping[str, object]) -> dict:
        """Only one sequential transition; never skip audit, readback or consent."""
        if target not in STEPS[1:]:
            raise PrintGateError("unsupported automatic transition")
        if not isinstance(proof, Mapping):
            raise PrintGateError("evidence required")

        def transition(data: dict) -> None:
            current = STEPS.index(data["state"]) if data["state"] in STEPS else -1
            if current < 0 or current + 1 >= len(STEPS) or STEPS[current + 1] != target:
                raise PrintGateError("out of order, replayed or terminal transition")
            if target == "GEOMETRY_VERIFIED":
                _sha(proof.get("report_sha256"))
                if proof.get("geometry_passed") is not True:
                    raise PrintGateError("geometry not independently verified")
            elif target == "SLICED":
                name = _basename(proof.get("filename"), sliced=True)
                artifact_sha = _sha(proof.get("artifact_sha256"))
                if proof.get("material") != data["material"] or proof.get("machine") != data["machine"]:
                    raise PrintGateError("wrong printer/material")
                if proof.get("nozzle_mm") != 0.4:
                    raise PrintGateError("nozzle mismatch")
                data["artifact"] = {"filename": name, "sha256": artifact_sha}
            elif target == "AUDITED":
                if (
                    proof.get("result") != "ready_for_review"
                    or proof.get("single_plate") is not True
                    or _sha(proof.get("artifact_sha256")) != data["artifact"]["sha256"]
                ):
                    raise PrintGateError("plate preflight rejected artifact")
                _sha(proof.get("report_sha256"))
            elif target == "STAGED":
                if (
                    proof.get("readback_verified") is not True
                    or _sha(proof.get("remote_sha256")) != data["artifact"]["sha256"]
                    or _basename(proof.get("filename"), sliced=True)
                    != data["artifact"]["filename"]
                ):
                    raise PrintGateError("FTPS readback has not proved matching bytes")
            elif target == "READY_FOR_USER":
                if proof.get("printer_idle") is not True:
                    raise PrintGateError("printer is busy or status unavailable")
            _event(data, target, proof)

        return self.store.mutate(owner, job_id, transition)

    @staticmethod
    def sign_start_grant(
        job: Mapping[str, object],
        *,
        operator_id: str,
        key: bytes,
        expires_at: int,
        nonce: str,
    ) -> dict:
        """Trusted authenticated UI only. Do not register this as an LLM tool."""
        if job.get("state") != "READY_FOR_USER":
            raise PrintGateError("job not ready for operator approval")
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise PrintGateError("authenticated operator required")
        if len(key) < 32 or not JOB_ID.fullmatch(nonce):
            raise PrintGateError("invalid signing key or random nonce")
        now = int(time.time())
        if not now < expires_at <= now + 300:
            raise PrintGateError("approval expiry exceeds five minutes")
        grant = {
            "job_id": job["job_id"],
            "artifact_sha256": job["artifact"]["sha256"],
            "operator_id": operator_id,
            "expires_at": expires_at,
            "nonce": nonce,
        }
        payload = json.dumps(grant, sort_keys=True, separators=(",", ":")).encode()
        grant["signature"] = hmac.new(key, payload, hashlib.sha256).hexdigest()
        return grant

    def redeem_start(self, owner: str, job_id: str, grant: Mapping[str, object], key: bytes) -> dict:
        """Consume grant durably BEFORE any external start side-effect."""
        if len(key) < 32 or not isinstance(grant, Mapping):
            raise PrintGateError("invalid trusted start authorization")
        payload_data = {x: grant.get(x) for x in ("job_id", "artifact_sha256", "operator_id", "expires_at", "nonce")}
        payload = json.dumps(payload_data, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
        if not isinstance(grant.get("signature"), str) or not hmac.compare_digest(signature, grant["signature"]):
            raise PrintGateError("invalid start approval signature")
        if not isinstance(grant.get("expires_at"), int) or not time.time() < grant["expires_at"]:
            raise PrintGateError("expired start approval")
        if grant.get("job_id") != job_id or not JOB_ID.fullmatch(str(grant.get("nonce"))):
            raise PrintGateError("approval refers to another print job")

        def transition(data: dict) -> None:
            if data["state"] != "READY_FOR_USER" or data["artifact"]["sha256"] != grant.get("artifact_sha256"):
                raise PrintGateError("approval replay, model mismatch or wrong job stage")
            data["start_grant_digest"] = hashlib.sha256(payload).hexdigest()
            _event(data, "START_PENDING")

        return self.store.mutate(owner, job_id, transition)

    def mark_start_result(self, owner: str, job_id: str, *, observed_printing: bool) -> dict:
        """Ambiguous errors cannot trigger an automatic retry of start."""
        def change(data: dict) -> None:
            if data["state"] != "START_PENDING":
                raise PrintGateError("no consumed start authorization")
            _event(data, "PRINTING" if observed_printing else "START_UNKNOWN")

        return self.store.mutate(owner, job_id, change)

    def finish(self, owner: str, job_id: str, *, telemetry_finished: bool) -> dict:
        if not telemetry_finished:
            raise PrintGateError("printer has not confirmed finished job")

        def change(data: dict) -> None:
            if data["state"] != "PRINTING":
                raise PrintGateError("not an active confirmed print")
            _event(data, "COMPLETE")

        return self.store.mutate(owner, job_id, change)

    def add_video(self, owner: str, job_id: str, video: Mapping[str, object]) -> dict:
        def change(data: dict) -> None:
            if data["state"] != "COMPLETE":
                raise PrintGateError("video can be linked only to a finished print")
            if data["video"] is not None:
                raise PrintGateError("video already linked: no duplicate overwrite")
            if video.get("status") != "verified_upload":
                raise PrintGateError("video upload must be verified")
            _sha(video.get("sha256"))
            if not isinstance(video.get("drive_id"), str) or not video["drive_id"]:
                raise PrintGateError("Drive file id required")
            data["video"] = {
                "drive_id": video["drive_id"],
                "sha256": video["sha256"],
                "bytes": video["bytes"],
            }
            _event(data, "COMPLETE")

        return self.store.mutate(owner, job_id, change)
