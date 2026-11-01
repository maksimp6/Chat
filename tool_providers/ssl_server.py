"""SSL and web-server tools of the shared tool registry (category ``ssl``).

``SSL_TOOLS`` holds six tools that let the model look after HTTPS on a server:

- ``ssl_cert_check`` opens a TLS connection from the Logos host and reports the certificate that
  host presents (subject, issuer, names, validity, days left, chain verdict). Read-only.
- ``ssl_server_status`` runs one fixed read-only report on an SSH Runtime target: web-server
  services, ports 80 and 443, the certbot certificate list, uptime and disk.
- ``ssl_server_config_test`` checks the web-server configuration without applying it.
- ``ssl_cert_issue`` and ``ssl_cert_renew`` run certbot on the target; both need the owner's approval.
- ``ssl_server_service`` reloads, restarts, starts or stops the web server; it needs approval too.

Server commands go through ``ssh_runtime_exec``, so the SSH Runtime's own targets, users and
restrictions apply. Every value that reaches a command line is validated and shell-quoted.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import shlex
import socket
import ssl
from typing import Any

from tool_providers.filesystem import resolves_to_link_local
from tool_providers.runtime_tools import ssh_runtime_exec
from tool_providers.schema import object_schema

_TRANSPORTS = ["responses_api", "mcp"]

_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_RE = re.compile(rf"^{_HOST_LABEL}(?:\.{_HOST_LABEL})*$")
_DOMAIN_RE = re.compile(rf"^(?:\*\.)?{_HOST_LABEL}(?:\.{_HOST_LABEL})+$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}$")

_SERVICES = ("nginx", "caddy", "apache2", "httpd")
_SERVICE_ACTIONS = ("reload", "restart", "start", "stop")
_AUTHENTICATORS = ("nginx", "apache", "standalone")

_STATUS_SCRIPT = r"""
S=""; [ "$(id -u)" = 0 ] || S="sudo -n"
echo "== host"; hostname; uptime
echo "== services"
for s in nginx caddy apache2 httpd; do
  state=$(systemctl is-active "$s" 2>/dev/null) && echo "$s: $state"
done
echo "== listening on 80 and 443"
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ':(80|443)[[:space:]]' || echo "nothing listens"
echo "== certificates"
if command -v certbot >/dev/null 2>&1; then $S certbot certificates 2>&1 | head -80; else echo "certbot is not installed"; fi
echo "== disk"; df -h / | tail -1
"""

_CONFIG_TEST = {
    "nginx": "nginx -t 2>&1",
    "apache2": "apache2ctl configtest 2>&1",
    "httpd": "apachectl configtest 2>&1",
    "caddy": "caddy validate --config /etc/caddy/Caddyfile 2>&1",
}


def _fail(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _host(value: Any) -> str:
    host = _text(value).lower().rstrip(".")
    if not host or len(host) > 253 or not _HOST_RE.match(host):
        raise ValueError("host must be a DNS name such as example.com")
    return host


def _port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("port must be an integer from 1 to 65535")
    return value


def _one_of(value: Any, allowed: tuple[str, ...], name: str) -> str:
    text = _text(value)
    if text not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}")
    return text


def _timeout(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 300:
        raise ValueError("timeout_seconds must be an integer from 1 to 300")
    return value


def _names(cert: Any) -> list[str]:
    from cryptography import x509

    try:
        extension = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    return [str(name) for name in extension.value.get_values_for_type(x509.DNSName)]


def _name_text(name: Any) -> str:
    return name.rfc4514_string() if name is not None else ""


def _handshake(host: str, port: int, server_name: str, timeout: float, verify: bool):
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            return tls.getpeercert(binary_form=True), tls.version(), tls.cipher()


def ssl_cert_check(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    """Report the certificate a host presents over TLS."""
    from cryptography import x509

    try:
        host = _host(args.get("host"))
        port = _port(args.get("port"))
        server_name = _host(args["server_name"]) if args.get("server_name") else host
        timeout = _timeout(args.get("timeout_seconds"), 10)
    except ValueError as exc:
        return _fail(str(exc))
    if resolves_to_link_local(f"https://{host}"):
        return _fail("host resolves to a link-local address")

    verified, verify_error = True, None
    try:
        try:
            der, version, cipher = _handshake(host, port, server_name, timeout, True)
        except ssl.SSLCertVerificationError as exc:
            verified, verify_error = False, exc.verify_message or str(exc)
            der, version, cipher = _handshake(host, port, server_name, timeout, False)
    except (OSError, ssl.SSLError) as exc:
        return _fail(f"TLS connection to {host}:{port} failed: {exc}")

    cert = x509.load_der_x509_certificate(der)
    now = datetime.datetime.now(datetime.timezone.utc)
    not_after = cert.not_valid_after_utc
    return {
        "success": True,
        "host": host,
        "port": port,
        "server_name": server_name,
        "chain_verified": verified,
        "verify_error": verify_error,
        "subject": _name_text(cert.subject),
        "issuer": _name_text(cert.issuer),
        "names": _names(cert),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": not_after.isoformat(),
        "days_left": (not_after - now).days,
        "expired": not_after < now,
        "serial": format(cert.serial_number, "x"),
        "sha256_fingerprint": hashlib.sha256(der).hexdigest(),
        "protocol": version,
        "cipher": cipher[0] if cipher else None,
    }


def _remote(args: dict, cfg: dict | None, command: str, timeout: int) -> dict[str, Any]:
    target = _text(args.get("target"))
    if not target:
        return _fail("target is required")
    return ssh_runtime_exec(
        {"target": target, "command": command, "timeout_seconds": timeout}, cfg
    )


def ssl_server_status(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    try:
        timeout = _timeout(args.get("timeout_seconds"), 60)
    except ValueError as exc:
        return _fail(str(exc))
    return _remote(args, cfg, _STATUS_SCRIPT, timeout)


def ssl_server_config_test(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    try:
        service = _one_of(args.get("service"), _SERVICES, "service")
        timeout = _timeout(args.get("timeout_seconds"), 60)
    except ValueError as exc:
        return _fail(str(exc))
    return _remote(args, cfg, f'S=""; [ "$(id -u)" = 0 ] || S="sudo -n"; $S {_CONFIG_TEST[service]}', timeout)


def _domains(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError("domains must be a list of 1 to 20 names")
    names = [_text(item).lower() for item in value]
    for name in names:
        if len(name) > 253 or not _DOMAIN_RE.match(name):
            raise ValueError(f"not a valid domain name: {name!r}")
    return names


def ssl_cert_issue(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    """Issue or extend a certificate with certbot."""
    try:
        domains = _domains(args.get("domains"))
        email = _text(args.get("email"))
        if not _EMAIL_RE.match(email):
            raise ValueError("email must be an address such as admin@example.com")
        authenticator = _one_of(args.get("authenticator"), _AUTHENTICATORS, "authenticator")
        dry_run = args.get("dry_run")
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be true or false")
        timeout = _timeout(args.get("timeout_seconds"), 240)
    except ValueError as exc:
        return _fail(str(exc))
    if any(name.startswith("*.") for name in domains):
        # A wildcard needs a DNS challenge, which none of these authenticators can answer.
        return _fail("wildcard names need a DNS challenge; these authenticators cannot answer it")
    flags = [
        "certbot",
        "certonly" if authenticator == "standalone" else "",
        f"--{authenticator}",
        "--non-interactive",
        "--agree-tos",
        "--keep-until-expiring",
        "--expand",
        "-m",
        shlex.quote(email),
    ]
    for name in domains:
        flags += ["-d", shlex.quote(name)]
    if dry_run:
        flags.append("--dry-run")
    command = 'S=""; [ "$(id -u)" = 0 ] || S="sudo -n"; $S ' + " ".join(p for p in flags if p) + " 2>&1"
    return _remote(args, cfg, command, timeout)


def ssl_cert_renew(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    """Renew every certificate certbot manages that is close to expiry."""
    try:
        dry_run = args.get("dry_run")
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be true or false")
        timeout = _timeout(args.get("timeout_seconds"), 240)
    except ValueError as exc:
        return _fail(str(exc))
    command = 'S=""; [ "$(id -u)" = 0 ] || S="sudo -n"; $S certbot renew --non-interactive'
    if dry_run:
        command += " --dry-run"
    return _remote(args, cfg, command + " 2>&1", timeout)


def ssl_server_service(args: dict, cfg: dict | None = None) -> dict[str, Any]:
    try:
        service = _one_of(args.get("service"), _SERVICES, "service")
        action = _one_of(args.get("action"), _SERVICE_ACTIONS, "action")
        timeout = _timeout(args.get("timeout_seconds"), 60)
    except ValueError as exc:
        return _fail(str(exc))
    command = (
        'S=""; [ "$(id -u)" = 0 ] || S="sudo -n"; '
        f"$S systemctl {action} {service} 2>&1; echo \"exit=$?\"; systemctl is-active {service} 2>&1"
    )
    return _remote(args, cfg, command, timeout)


def _optional(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


_TARGET = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "description": "Name of an SSH Runtime target (the server to look after)",
}
_TIMEOUT = _optional(
    {"type": "integer", "minimum": 1, "maximum": 300, "description": "Seconds to wait for the command"}
)
_SERVICE = {"type": "string", "enum": list(_SERVICES), "description": "Web server unit"}
_DRY_RUN = {
    "type": "boolean",
    "description": "true: certbot only rehearses against the staging CA and changes nothing",
}


def _definition(
    *,
    title: str,
    description: str,
    parameters: dict[str, Any],
    mutating: bool,
    func: Any,
    local: bool = False,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": parameters,
        "capabilities": ["ssl", "server"],
        "risk_level": "high" if mutating else "low",
        "read_only": not mutating,
        "requires_approval": mutating,
        "supported_transports": list(_TRANSPORTS),
        "executor": {"type": "local"},
        "metadata": {"runs_on": "logos_host" if local else "ssh_target"},
        "func": func,
    }


SSL_TOOLS: dict[str, dict[str, Any]] = {
    "ssl_cert_check": _definition(
        title="SSL: check a host's certificate",
        description=(
            "Open a TLS connection from the Logos host to host:port and report the certificate it "
            "presents: subject, issuer, DNS names, validity dates, days left, whether the chain "
            "verifies and why not, protocol and cipher."
        ),
        parameters=object_schema(
            {
                "host": {"type": "string", "minLength": 1, "maxLength": 253},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "server_name": _optional(
                    {"type": "string", "minLength": 1, "maxLength": 253, "description": "SNI name; not given: host"}
                ),
                "timeout_seconds": _TIMEOUT,
            },
            ["host", "port"],
        ),
        mutating=False,
        func=ssl_cert_check,
        local=True,
    ),
    "ssl_server_status": _definition(
        title="SSL: server status",
        description=(
            "Read-only report from an SSH Runtime target: running web servers (nginx, caddy, apache), "
            "what listens on ports 80 and 443, the certbot certificate list with expiry, uptime, disk."
        ),
        parameters=object_schema({"target": _TARGET, "timeout_seconds": _TIMEOUT}, ["target"]),
        mutating=False,
        func=ssl_server_status,
    ),
    "ssl_server_config_test": _definition(
        title="SSL: test web-server config",
        description="Check the web-server configuration on the target without applying it (nginx -t and the like).",
        parameters=object_schema(
            {"target": _TARGET, "service": _SERVICE, "timeout_seconds": _TIMEOUT},
            ["target", "service"],
        ),
        mutating=False,
        func=ssl_server_config_test,
    ),
    "ssl_cert_issue": _definition(
        title="SSL: issue a certificate",
        description=(
            "Run certbot on the target to get or extend a certificate for the listed domains, "
            "answering the challenge through the nginx or apache plugin or a standalone listener."
        ),
        parameters=object_schema(
            {
                "target": _TARGET,
                "domains": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {"type": "string", "minLength": 3, "maxLength": 253},
                },
                "email": {"type": "string", "minLength": 6, "maxLength": 320},
                "authenticator": {"type": "string", "enum": list(_AUTHENTICATORS)},
                "dry_run": _DRY_RUN,
                "timeout_seconds": _TIMEOUT,
            },
            ["target", "domains", "email", "authenticator", "dry_run"],
        ),
        mutating=True,
        func=ssl_cert_issue,
    ),
    "ssl_cert_renew": _definition(
        title="SSL: renew certificates",
        description="Run certbot renew on the target for every certificate close to expiry.",
        parameters=object_schema(
            {"target": _TARGET, "dry_run": _DRY_RUN, "timeout_seconds": _TIMEOUT},
            ["target", "dry_run"],
        ),
        mutating=True,
        func=ssl_cert_renew,
    ),
    "ssl_server_service": _definition(
        title="SSL: control the web server",
        description="Reload, restart, start or stop the web-server service on the target; the result shows its state afterwards.",
        parameters=object_schema(
            {
                "target": _TARGET,
                "service": _SERVICE,
                "action": {"type": "string", "enum": list(_SERVICE_ACTIONS)},
                "timeout_seconds": _TIMEOUT,
            },
            ["target", "service", "action"],
        ),
        mutating=True,
        func=ssl_server_service,
    ),
}

__all__ = ["SSL_TOOLS"]
