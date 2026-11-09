"""The home's QUIC links and the SOCKS5 proxy on the Redmi 9.

Run from the Logos checkout in Termux; the script also writes its Termux:Boot hook:

    python scripts/home/tunnel.py

Owner, 06.11.2026: "build tunnel.maxxxpavlov.ru with quic support so my server can connect
there and use it to access lan, also to run socks5 proxy on redmi and proxify traffic without
tcp overhead", then "socks is used only on lan". So there are two links, each one way:

- The owner's server connects to UDP port 443 here, tunnel.maxxxpavlov.ru, and reaches the
  home network. The certificate is the one acme_cert.py keeps fresh; sing-box reloads it.
- Port 1080 is the SOCKS5 proxy for the home network. What it carries goes to the owner's
  server over QUIC and leaves from there, so SOCKS never crosses the internet.

sing-box speaks Hysteria2 on both: each proxied TCP connection is a QUIC stream and each UDP
packet a QUIC datagram, so TCP never runs inside TCP. Hysteria2 carries connections from its
client to its server only, hence the two links. tunnel_server_setup.sh sets up the far end.

Russian filtering drops QUIC leaving the country only for forbidden domain names (Heitmann et
al., "On Russia's Early Introduction of QUIC SNI Censorship", FOCI 2026), and SERVER_NAME is
not one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acme_cert  # noqa: E402 (sibling script)

QUIC_PORT = 443
SOCKS_PORT = 1080
ROUTER = "192.168.10.1"
# Who may use the proxy: the home network, and programs on this phone.
HOME_NETWORK = ["192.168.10.0/24", "127.0.0.0/8"]
ROOT = Path.home() / ".config/tunnel"
PRIVATE_KEY = ROOT / "key.pem"
PUBLIC_KEY = ROOT / "public.pem"
SERVER_KEY = ROOT / "server.pem"
CONFIG = ROOT / "config.json"
BOOT_HOOK = Path.home() / ".termux/boot/75-tunnel"
LOG = Path.home() / ".tunnel.log"
# The owner's server (vm4800173.firstbyte.club): its address, the public half of its key, and
# its own certificate, the only one trusted on that link.
SERVER_ADDRESS = "157.228.161.121"
SERVER_NAME = "maxxxpavlov.online"
SERVER_PUBLIC_KEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VuAyEABbOob3Ivz6D5OLLdhV0p6hSpztpJ9HUDbtO55KYuQQU=\n"
    "-----END PUBLIC KEY-----\n"
)
SERVER_CERTIFICATE = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBsDCCAVagAwIBAgIUBTqgQUhGV+i5jLAujJIudXM4OZgwCgYIKoZIzj0EAwIw\n"
    "HTEbMBkGA1UEAwwSbWF4eHhwYXZsb3Yub25saW5lMCAXDTI2MTEwNjE0MDkwNVoY\n"
    "DzIxMjYxMDEzMTQwOTA1WjAdMRswGQYDVQQDDBJtYXh4eHBhdmxvdi5vbmxpbmUw\n"
    "WTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAARQcniJKQmXt4ne3GArBzdGSwSA0v0O\n"
    "SRyI7tl55FQdWkFS3AbBNodBuGMJNO3Z8njImX7TeOZnCJ+btvEhEUIwo3IwcDAd\n"
    "BgNVHQ4EFgQU9nVu9RD+RElKA42AoQBF9facu1UwHwYDVR0jBBgwFoAU9nVu9RD+\n"
    "RElKA42AoQBF9facu1UwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHREEFjAUghJtYXh4\n"
    "eHBhdmxvdi5vbmxpbmUwCgYIKoZIzj0EAwIDSAAwRQIgDe05xw5fCqU+aL5dTFVb\n"
    "bBaN0EzM/TxVJYowF+ypOjQCIQCJi9B4MeIEIUvZV48emElM5l5GGCs1kEBFmm1j\n"
    "AlGQrA==\n"
    "-----END CERTIFICATE-----\n"
)


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def _openssl(*args: str) -> bytes:
    return subprocess.run(["openssl", *args], check=True, capture_output=True).stdout


def _password() -> str:
    """The secret shared with the server. Each side derives it from its own private key and the
    other's public key, so setting it up exchanges only public keys. After that it is sent only
    inside TLS, at login."""
    ROOT.mkdir(parents=True, exist_ok=True)
    ROOT.chmod(0o700)
    if not PRIVATE_KEY.exists():
        _openssl("genpkey", "-algorithm", "X25519", "-out", str(PRIVATE_KEY))
        PRIVATE_KEY.chmod(0o600)
    _openssl("pkey", "-in", str(PRIVATE_KEY), "-pubout", "-out", str(PUBLIC_KEY))
    SERVER_KEY.write_text(SERVER_PUBLIC_KEY, encoding="ascii")
    shared = _openssl(
        "pkeyutl", "-derive", "-inkey", str(PRIVATE_KEY), "-peerkey", str(SERVER_KEY)
    )
    return hashlib.sha256(shared).hexdigest()


def _config(password: str) -> dict:
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {"servers": [{"type": "udp", "tag": "router", "server": ROUTER}]},
        "inbounds": [
            {
                "type": "hysteria2",
                "tag": "quic",
                "listen": "0.0.0.0",
                "listen_port": QUIC_PORT,
                "users": [{"name": "server", "password": password}],
                "tls": {
                    "enabled": True,
                    "alpn": ["h3"],
                    "certificate_path": str(acme_cert.CERT),
                    "key_path": str(acme_cert.KEY),
                },
            },
            {
                "type": "socks",
                "tag": "socks",
                "listen": "0.0.0.0",
                "listen_port": SOCKS_PORT,
            },
        ],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
            {
                "type": "hysteria2",
                "tag": "server",
                "server": SERVER_ADDRESS,
                "server_port": QUIC_PORT,
                "password": password,
                "tls": {
                    "enabled": True,
                    "server_name": SERVER_NAME,
                    "alpn": ["h3"],
                    "certificate": SERVER_CERTIFICATE.splitlines(),
                },
            },
        ],
        "route": {
            "default_domain_resolver": "router",
            # No password on the proxy, so it answers the home network and nothing else, such
            # as the phone's mobile interface. A home address stays at home.
            "rules": [
                {
                    "inbound": ["socks"],
                    "source_ip_cidr": HOME_NETWORK,
                    "ip_is_private": True,
                    "action": "route",
                    "outbound": "direct",
                },
                {
                    "inbound": ["socks"],
                    "source_ip_cidr": HOME_NETWORK,
                    "action": "route",
                    "outbound": "server",
                },
                {"inbound": ["socks"], "action": "reject"},
            ],
        },
    }


def _binary() -> str:
    """sing-box from the Termux repository, installed on the first start."""
    found = shutil.which("sing-box")
    if found:
        return found
    # The package lists on the phone may be older than the files on the mirror.
    subprocess.run(["apt-get", "update"], check=False, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["apt-get", "install", "-y", "sing-box"], check=True, stdin=subprocess.DEVNULL
    )
    return shutil.which("sing-box") or "sing-box"


def _changed(script: Path, loaded: float) -> bool:
    try:
        return script.stat().st_mtime != loaded
    except OSError:  # a deploy is replacing the file right now
        return False


def _run(binary: str, script: Path, loaded: float) -> None:
    """Run sing-box until it stops or this script changes."""
    server = subprocess.Popen(
        [binary, "run", "--disable-color", "-c", str(CONFIG)], stdin=subprocess.DEVNULL
    )
    try:
        while not _changed(script, loaded):
            if server.poll() is not None:
                log(f"sing-box stopped with code {server.returncode}")
                time.sleep(30)
                return
            time.sleep(5)
    finally:
        server.terminate()
        server.wait()


def main() -> None:
    script = Path(__file__).resolve()
    loaded = script.stat().st_mtime
    BOOT_HOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOT_HOOK.write_text(
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        f"{sys.executable} {script} >>{LOG} 2>&1 </dev/null &\n",
        encoding="utf-8",
    )
    BOOT_HOOK.chmod(0o700)
    # SystemExit runs the finally in _run, so sing-box stops with this process.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    binary = _binary()
    config = _config(_password())
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    # A sing-box left by a killed run would hold the ports. pkill -x does not find it on this phone.
    subprocess.run(["pkill", "-f", "sing-box run"], check=False)
    log(f"tunnel starting on UDP {QUIC_PORT}, SOCKS5 on {SOCKS_PORT}")
    while not _changed(script, loaded):
        _run(binary, script, loaded)
    log("script changed, restarting")
    os.execv(sys.executable, [sys.executable, str(script)])


if __name__ == "__main__":
    main()
