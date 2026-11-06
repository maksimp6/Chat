"""Let's Encrypt certificate for the home names, through ACME HTTP-01 on port 80.

home_server.py runs ``ensure()`` twice a day and serves the challenge files from
``CHALLENGES`` at ``/.well-known/acme-challenge/<token>``. RSA signing, the CSR and
the keys are done by the ``openssl`` program Termux already has; no packages.

    python scripts/home/acme_cert.py     # issue or renew now, print the outcome

Requesting a certificate accepts the Let's Encrypt Subscriber Agreement (owner,
05.11.2026: "make ssl for chat domain"). No contact e-mail is sent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
NAMES = ("chat.maxxxpavlov.ru", "maxxxpavlov.ru", "www.maxxxpavlov.ru", "tunnel.maxxxpavlov.ru")
ROOT = Path.home() / ".config/acme"
CHALLENGES = ROOT / "challenges"
ACCOUNT_KEY = ROOT / "account.key"
KEY = ROOT / "domain.key"
CERT = ROOT / "fullchain.pem"
STATUS = ROOT / "status.json"
RENEW_DAYS = 30
TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _openssl(*args: str, data: bytes | None = None) -> bytes:
    done = subprocess.run(["openssl", *args], input=data, capture_output=True)
    if done.returncode:
        raise RuntimeError(f"openssl {args[0]}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def _key(path: Path) -> None:
    if not path.exists():
        _openssl("genrsa", "-out", str(path), "2048")
        path.chmod(0o600)


def _jwk() -> dict[str, str]:
    modulus = _openssl("rsa", "-in", str(ACCOUNT_KEY), "-noout", "-modulus")
    n = bytes.fromhex(modulus.decode("ascii").strip().split("=", 1)[1])
    return {"e": _b64((65537).to_bytes(3, "big")), "kty": "RSA", "n": _b64(n)}


class Acme:
    def __init__(self) -> None:
        self.kid: str | None = None
        self.nonce: str | None = None
        self.directory = self._request(DIRECTORY)[0]
        self.jwk = _jwk()
        self.thumbprint = _b64(
            hashlib.sha256(json.dumps(self.jwk, sort_keys=True, separators=(",", ":")).encode()).digest()
        )

    def _request(self, url: str, body: bytes | None = None) -> tuple[dict, dict, bytes]:
        request = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
        if body is not None:
            request.add_header("Content-Type", "application/jose+json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw, headers = response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, headers = error.read(), dict(error.headers)
            self.nonce = headers.get("Replay-Nonce") or self.nonce
            raise RuntimeError(f"{url}: {error.code} {raw.decode(errors='replace')[:400]}") from None
        self.nonce = headers.get("Replay-Nonce") or self.nonce
        try:
            data = json.loads(raw) if raw[:1] == b"{" else {}
        except ValueError:
            data = {}
        return data, headers, raw

    def post(self, url: str, payload: object | None) -> tuple[dict, dict, bytes]:
        """A signed request; payload None is POST-as-GET."""
        if not self.nonce:
            request = urllib.request.Request(self.directory["newNonce"], method="HEAD")
            with urllib.request.urlopen(request, timeout=30) as response:
                self.nonce = response.headers["Replay-Nonce"]
        protected = {"alg": "RS256", "nonce": self.nonce, "url": url}
        protected.update({"kid": self.kid} if self.kid else {"jwk": self.jwk})
        self.nonce = None
        head = _b64(json.dumps(protected).encode())
        body = "" if payload is None else _b64(json.dumps(payload).encode())
        signature = _openssl("dgst", "-sha256", "-sign", str(ACCOUNT_KEY), data=f"{head}.{body}".encode())
        envelope = json.dumps({"protected": head, "payload": body, "signature": _b64(signature)})
        return self._request(url, envelope.encode())

    def poll(self, url: str, until: str) -> dict:
        for _ in range(60):
            data = self.post(url, None)[0]
            if data.get("status") == until:
                return data
            if data.get("status") == "invalid":
                raise RuntimeError(f"{url}: invalid {json.dumps(data, ensure_ascii=False)[:600]}")
            time.sleep(3)
        raise RuntimeError(f"{url}: still {data.get('status')}")


def days_left() -> float | None:
    try:
        pem = CERT.read_text(encoding="ascii")
    except OSError:
        return None
    first = pem.split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
    end = _openssl("x509", "-noout", "-enddate", data=first.encode()).decode().strip().split("=", 1)[1]
    return (ssl.cert_time_to_seconds(end) - time.time()) / 86400


def _covers() -> bool:
    try:
        text = _openssl("x509", "-noout", "-ext", "subjectAltName", "-in", str(CERT)).decode()
    except (OSError, RuntimeError):
        return False
    return all(f"DNS:{name}" in text for name in NAMES)


def issue() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    ROOT.chmod(0o700)
    CHALLENGES.mkdir(exist_ok=True)
    _key(ACCOUNT_KEY)
    _key(KEY)
    acme = Acme()
    _, headers, _ = acme.post(acme.directory["newAccount"], {"termsOfServiceAgreed": True})
    acme.kid = headers["Location"]
    order, headers, _ = acme.post(
        acme.directory["newOrder"], {"identifiers": [{"type": "dns", "value": n} for n in NAMES]}
    )
    order_url = headers["Location"]
    written: list[Path] = []
    try:
        for url in order["authorizations"]:
            authz = acme.post(url, None)[0]
            if authz.get("status") == "valid":
                continue
            challenge = next(c for c in authz["challenges"] if c["type"] == "http-01")
            if not TOKEN.fullmatch(challenge["token"]):
                raise RuntimeError("unexpected challenge token")
            path = CHALLENGES / challenge["token"]
            path.write_text(f"{challenge['token']}.{acme.thumbprint}", encoding="ascii")
            written.append(path)
            acme.post(challenge["url"], {})
            acme.poll(url, "valid")
        san = ",".join(f"DNS:{n}" for n in NAMES)
        csr = _openssl(
            "req", "-new", "-key", str(KEY), "-subj", f"/CN={NAMES[0]}",
            "-addext", f"subjectAltName={san}", "-outform", "DER",
        )
        acme.post(order["finalize"], {"csr": _b64(csr)})
        order = acme.poll(order_url, "valid")
        pem = acme.post(order["certificate"], None)[2]
    finally:
        for path in written:
            path.unlink(missing_ok=True)
    temporary = CERT.with_suffix(".next")
    temporary.write_bytes(pem)
    temporary.chmod(0o600)
    os.replace(temporary, CERT)


def ensure() -> bool:
    """Issue when there is no certificate, it lacks a name or ends within RENEW_DAYS.
    Returns True when a new certificate was written; the outcome goes to STATUS."""
    renewed = False
    try:
        left = days_left()
        if left is None or left < RENEW_DAYS or not _covers():
            issue()
            renewed = True
            left = days_left()
        status = {"ok": True, "days_left": round(left or 0, 1), "names": list(NAMES)}
    except Exception as exc:  # the outcome is shown on the home page
        status = {"ok": False, "error": str(exc)[:800], "names": list(NAMES)}
    status["checked"] = time.strftime("%d.%m.%Y %H:%M")
    ROOT.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "certificate:", json.dumps(status, ensure_ascii=False), flush=True)
    return renewed


if __name__ == "__main__":
    ensure()
