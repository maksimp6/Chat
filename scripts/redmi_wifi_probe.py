"""Read-only readiness through the server's Wi-Fi address; no model requests."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request


class ProbeFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def address(value):
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise ProbeFailure("invalid_wifi_address") from None
    if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or str(parsed) == "255.255.255.255":
        raise ProbeFailure("wifi_address_is_not_unicast_lan")
    return str(parsed)


def parse_wifi_ip(output):
    for line in output.splitlines():
        words = line.split()
        if len(words) >= 4 and words[1].split("@")[0] == "wlan0" and words[2] == "inet":
            return address(words[3].split("/")[0])
    raise ProbeFailure("wifi_ipv4_unavailable")


def discover():
    for command in (["/system/bin/ip", "-o", "-4", "addr", "show", "wlan0"],
                    ["su", "-c", "/system/bin/ip -o -4 addr show wlan0"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return parse_wifi_ip(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ProbeFailure):
            pass
    raise ProbeFailure("wifi_ipv4_unavailable")


def check_base(base, assets, timeout=3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    paths = ["/healthz", "/", "/static/chat.js", "/static/style.css"]
    bodies = {}
    for path in paths:
        try:
            with opener.open(base + path, timeout=timeout) as response:
                data = response.read(2 * 1024 * 1024 + 1)
                if response.status != 200 or len(data) > 2 * 1024 * 1024:
                    raise ProbeFailure("http_status_or_body_limit")
                bodies[path] = data
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProbeFailure("http_read_failed") from None
    try:
        health = json.loads(bodies["/healthz"])
    except (ValueError, UnicodeDecodeError):
        raise ProbeFailure("invalid_health_json") from None
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise ProbeFailure("health_not_ready")
    if b"<title>Logos</title>" not in bodies["/"]:
        raise ProbeFailure("wrong_application_html")
    for name in ("chat.js", "style.css"):
        if bodies["/static/" + name] != (Path(assets) / name).read_bytes():
            raise ProbeFailure("static_asset_mismatch")
    return {"status": "ready", "scope": "server_wifi_address", "external_peer_verified": False,
            "checks": paths, "static_sha256": {name: hashlib.sha256(bodies["/static/" + name]).hexdigest()
                                                for name in ("chat.js", "style.css")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--address")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args()
    try:
        ip = address(args.address) if args.address else discover()
        if args.discover:
            print(ip)
            return 0
        if not 1024 <= args.port <= 65535 or args.assets is None:
            raise ProbeFailure("invalid_probe_configuration")
        result = check_base("http://" + ip + ":" + str(args.port), args.assets)
        result["address"] = ip
        print(json.dumps(result))
        return 0
    except (ProbeFailure, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc) if isinstance(exc, ProbeFailure) else "local_assets_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
