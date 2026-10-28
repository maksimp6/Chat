"""Bounded Redmi 9 artifact receiver and upload-only A1 mini worker.

Accepts one successful exact-head workflow artifact. No shell execution,
MQTT, heating, movement, or print-start operations are implemented.
"""

import argparse
import ftplib
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import cast

API = "https://api.github.com/repos/maksimp6/Chat"
WORKFLOW = ".github/workflows/yorik-redmi9.yml"
FILENAME = "Yorik_A1mini_PLA.gcode.3mf"
MAX_BYTES = 50 * 1024 * 1024


def require_redmi9():
    result = subprocess.run(
        ["getprop", "ro.product.model"], capture_output=True, text=True, check=True
    )
    if result.stdout.strip() != "Redmi 9":
        raise ValueError("worker requires the authorized Redmi 9")


def github_token():
    # Reuse existing authentication locally, never put it in argv or reports.
    path = Path.home() / ".git-credentials"
    for line in path.read_text().splitlines():
        url = urllib.parse.urlsplit(line)
        if url.hostname == "github.com" and url.password:
            return urllib.parse.unquote(url.password)
    raise ValueError("existing GitHub read authentication is missing")


def api_json(path, token):
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_run(run, head):
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("invalid approved HEAD")
    expected = {
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
        "path": WORKFLOW,
    }
    if any(run.get(k) != v for k, v in expected.items()):
        raise ValueError("run is not a successful approved exact-head Yorik workflow")
    repo = run.get("head_repository") or {}
    if repo.get("full_name") != "maksimp6/Chat":
        raise ValueError("foreign repository artifact")


def await_run(head, token, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        runs = api_json("/actions/workflows/yorik-redmi9.yml/runs?per_page=30", token)
        matches = [
            r for r in runs.get("workflow_runs", []) if r.get("head_sha") == head
        ]
        if matches:
            run = matches[0]
            if run.get("status") == "completed":
                validate_run(run, head)
                return int(run["id"])
        time.sleep(min(15, max(0, deadline - time.monotonic())))
    raise TimeoutError("approved workflow did not complete within the bounded wait")


def download_artifact(run_id, token):
    data = api_json(f"/actions/runs/{run_id}/artifacts?per_page=100", token)
    artifacts = [
        a for a in data.get("artifacts", []) if a.get("name") == "yorik-a1mini-redmi9"
    ]
    if len(artifacts) != 1 or artifacts[0].get("expired"):
        raise ValueError("exactly one unexpired Yorik artifact is required")
    artifact_id = int(artifacts[0]["id"])

    # Obtain redirect without forwarding GitHub credentials to the blob host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(
        API + f"/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": "Bearer " + token},
    )
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers["Location"]
    else:
        raise ValueError("artifact API did not return a signed download")
    if urllib.parse.urlsplit(location).scheme != "https":
        raise ValueError("insecure artifact redirect")
    with urllib.request.urlopen(location, timeout=60) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("artifact exceeds size limit")
    return payload


def validate_package(blob, audit, head):
    if len(blob) > MAX_BYTES or len(blob) != audit.get("size_bytes"):
        raise ValueError("package size mismatch")
    digest = hashlib.sha256(blob).hexdigest()
    if digest != audit.get("sha256") or audit.get("head_sha") != head:
        raise ValueError("package hash or HEAD mismatch")
    expected = {
        "filename": FILENAME,
        "printer": "Bambu Lab A1 mini",
        "material": "PLA",
        "nozzle_mm": "0.4",
        "bed": "Textured PEI Plate",
        "state": "SLICED_NOT_PRINTED",
    }
    if any(audit.get(k) != v for k, v in expected.items()):
        raise ValueError("unexpected audited print profile")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("duplicate sliced archive entries")
        if (
            sum(i.file_size for i in archive.infolist()) > MAX_BYTES
            or archive.testzip()
        ):
            raise ValueError("oversized or corrupt sliced archive")
        profile = json.loads(archive.read("Metadata/project_settings.config"))
        gcode = archive.read("Metadata/plate_1.gcode").decode("utf-8")
    expected_profile = {
        "printer_model": "Bambu Lab A1 mini",
        "printer_variant": "0.4",
        "filament_type": ["PLA"],
        "curr_bed_type": "Textured PEI Plate",
        "nozzle_temperature": ["220"],
        "textured_plate_temp": ["60"],
    }
    if any(profile.get(k) != v for k, v in expected_profile.items()):
        raise ValueError("sliced profile disagrees with audit")
    if not gcode.startswith("; HEADER_BLOCK_START") or len(gcode) < 5000:
        raise ValueError("not a real sliced Bambu job")
    validate_gcode(gcode)
    return digest


def validate_gcode(gcode):
    commands = [line.split(";", 1)[0].strip() for line in gcode.splitlines()]
    for command, target in (("M109", 220), ("M190", 60)):
        if not any(
            re.match(rf"^{command}\s+S{target}(?:\s|$)", line) for line in commands
        ):
            raise ValueError("missing audited temperature commands")
    if not any(
        re.match(r"^G1\s", line) and re.search(r"\sE[0-9]+(?:\.[0-9]+)?(?:\s|$)", line)
        for line in commands
    ):
        raise ValueError("missing extrusion commands")


def unpack_handoff(payload, head):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if (
            len(names) != len(set(names))
            or sum(i.file_size for i in archive.infolist()) > MAX_BYTES
        ):
            raise ValueError("duplicate or oversized artifact entries")
        if set(names) != {FILENAME, "audit.json", "preview.png", "yorik_runner.py"}:
            raise ValueError("unexpected artifact entries")
        audit = json.loads(archive.read("audit.json"))
        blob = archive.read(FILENAME)
    digest = validate_package(blob, audit, head)
    # Never execute the worker script supplied by an artifact.
    return blob, audit, digest


def load_config(path):
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("printer config directory must be owner-only 0700")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("printer config must be an owned regular 0600 file")
        config = json.load(stream)
    address = ipaddress.ip_address(config["host"])
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
    ):
        raise ValueError("printer host must be a trusted private LAN IPv4 literal")
    if not re.fullmatch(r"[0-9a-f]{64}", config.get("certificate_sha256", "")):
        raise ValueError("previously trusted printer certificate pin is required")
    if not config.get("access_code"):
        raise ValueError("local printer access code is missing")
    return config


class PinnedContext(ssl.SSLContext):
    def __new__(cls, pin):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self, pin):
        self.check_hostname = False
        self.verify_mode = ssl.CERT_NONE
        self.pin = pin

    def wrap_socket(self, *args, **kwargs):
        connection = super().wrap_socket(*args, **kwargs)
        if (
            hashlib.sha256(
                cast(bytes, connection.getpeercert(binary_form=True))
            ).hexdigest()
            != self.pin
        ):
            connection.close()
            raise ssl.SSLError("printer TLS pin mismatch")
        return connection


class ImplicitFTPS(ftplib.FTP_TLS):
    def connect(self, host="", port=990, timeout=30, source_address=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = self.context.wrap_socket(
            socket.create_connection((host, port), timeout), server_hostname=host
        )
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


def readback(ftp, name):
    data = io.BytesIO()

    def accept(chunk):
        if data.tell() + len(chunk) > MAX_BYTES:
            raise ValueError("printer readback exceeds size limit")
        data.write(chunk)

    try:
        ftp.retrbinary("RETR " + name, accept)
    except ftplib.error_perm as exc:
        if str(exc).startswith("550"):
            return None
        raise
    return data.getvalue()


def stage(ftp, blob, digest):
    name = "Yorik_" + digest[:16] + ".gcode.3mf"
    ftp.cwd("/model")
    existing = readback(ftp, name)
    if existing is not None and existing != blob:
        raise ValueError("refusing to overwrite a different printer file")
    if existing is None:
        try:
            ftp.storbinary("STOR " + name, io.BytesIO(blob))
        except (OSError, ftplib.Error):
            # The write may already have succeeded. Never blindly resend STOR.
            pass
    if readback(ftp, name) != blob:
        raise ValueError("upload is not verified; manual reconciliation required")
    return {
        "state": "STAGED_VERIFIED_NOT_PRINTED",
        "remote_name": name,
        "sha256": digest,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--wait-seconds", type=int, default=1200)
    parser.add_argument("--head", required=True)
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.wait_seconds <= 1200:
        raise ValueError("workflow wait must be bounded to 1..1200 seconds")
    require_redmi9()
    token = github_token()
    if args.run_id is None:
        args.run_id = await_run(args.head, token, args.wait_seconds)
    validate_run(api_json(f"/actions/runs/{args.run_id}", token), args.head)
    blob, audit, digest = unpack_handoff(
        download_artifact(args.run_id, token), args.head
    )
    args.spool.mkdir(mode=0o700, parents=True, exist_ok=True)
    (args.spool / FILENAME).write_bytes(blob)
    (args.spool / "audit.json").write_text(json.dumps(audit, indent=2))
    report = {
        "state": "DOWNLOADED_VERIFIED_NOT_PRINTED",
        "sha256": digest,
        "run_id": args.run_id,
    }
    if args.stage:
        if args.config is None:
            raise ValueError("--stage requires an existing trusted printer config")
        config = load_config(args.config)
        with ImplicitFTPS(context=PinnedContext(config["certificate_sha256"])) as ftp:
            ftp.connect(config["host"])
            ftp.login("bblp", config["access_code"])
            ftp.prot_p()
            report.update(stage(ftp, blob, digest))
    (args.spool / "receipt.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ftplib.Error,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as exc:
        # No exception text: FTPS or auth errors can contain sensitive data.
        print(json.dumps({"state": "BLOCKED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
