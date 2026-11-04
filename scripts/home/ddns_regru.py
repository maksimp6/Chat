"""DDNS for maxxxpavlov.ru through REG.API: the A records follow the home's public IP.

Run from the Logos checkout in Termux; the script also writes its Termux:Boot hook:

    python scripts/home/ddns_regru.py setup    # ask for the REG.RU login, update now, keep running
    python scripts/home/ddns_regru.py          # check every 5 minutes, forever
    python scripts/home/ddns_regru.py --once   # one check

The REG.RU account is read from ~/.config/regru/ddns.json (mode 600):

    {"username": "...", "password": "..."}

REG.API answers only when API access is enabled in the REG.RU account and the
request comes from an address allowed there.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DOMAIN = "maxxxpavlov.ru"
SUBDOMAINS = ("@", "www")
API = "https://api.reg.ru/api/regru2/"
IP_URL = "https://api.ipify.org"
INTERVAL_SECONDS = 300
CREDENTIALS = Path.home() / ".config/regru/ddns.json"
BOOT_HOOK = Path.home() / ".termux/boot/80-ddns-regru"
LOG = Path.home() / ".ddns-regru.log"


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def public_ip() -> str:
    with urllib.request.urlopen(IP_URL, timeout=20) as response:
        address = response.read().decode("ascii").strip()
    parts = address.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise ValueError(f"not an IPv4 address: {address!r}")
    return address


def call(method: str, account: dict[str, str], **params: object) -> dict:
    data = {
        "username": account["username"],
        "password": account["password"],
        "domains": [{"dname": DOMAIN}],
        "output_content_type": "plain",
        **params,
    }
    body = urllib.parse.urlencode(
        {"input_format": "json", "input_data": json.dumps(data)}
    ).encode("ascii")
    with urllib.request.urlopen(API + method, data=body, timeout=30) as response:
        answer = json.loads(response.read().decode("utf-8"))
    if answer.get("result") != "success":
        raise RuntimeError(f"{method}: {answer.get('error_code')} {answer.get('error_text')}")
    domain = answer["answer"]["domains"][0]
    if domain.get("result") != "success":
        raise RuntimeError(f"{method}: {domain.get('error_code')} {domain.get('error_text')}")
    return domain


def update(account: dict[str, str]) -> None:
    address = public_ip()
    records = call("zone/get_resource_records", account).get("rrs", [])
    actions: list[dict[str, str]] = []
    for sub in SUBDOMAINS:
        current = sorted(
            r["content"] for r in records if r.get("rectype") == "A" and r.get("subname") == sub
        )
        if current == [address]:
            continue
        for content in current:
            actions.append(
                {"action": "remove_record", "subdomain": sub, "record_type": "A", "content": content}
            )
        actions.append({"action": "add_alias", "subdomain": sub, "ipaddr": address})
        log(f"{sub}.{DOMAIN}: {', '.join(current) or 'no A record'} -> {address}")
    if not actions:
        return
    result = call("zone/update_records", account, action_list=actions)
    failed = [a for a in result.get("action_list", []) if a.get("result") != "success"]
    if failed:
        raise RuntimeError(f"zone/update_records: {failed}")
    log(f"{DOMAIN} now points to {address}")


def install_boot_hook() -> None:
    BOOT_HOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOT_HOOK.write_text(
        "#!/data/data/com.termux/files/usr/bin/sh\n"
        f"{sys.executable} {Path(__file__).resolve()} >>{LOG} 2>&1 </dev/null &\n",
        encoding="utf-8",
    )
    BOOT_HOOK.chmod(0o700)


def save_and_start(account: dict[str, str]) -> None:
    """Check the account by updating the records, then save it and start the background check."""
    update(account)
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS.parent, 0o700)
    fd = os.open(CREDENTIALS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(account, handle)
    os.chmod(CREDENTIALS, 0o600)
    install_boot_hook()
    subprocess.run(["pkill", "-f", f"{Path(__file__).name}$"], check=False)
    with open(LOG, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, start_new_session=True,
        )


HINT = (
    "Проверьте логин и пароль и что в REG.RU (Настройки -> API) включён доступ к API "
    "и разрешён адрес этого дома."
)


def setup() -> int:
    username = input("Логин REG.RU: ").strip()
    password = getpass.getpass("Пароль REG.RU (не отображается): ")
    try:
        save_and_start({"username": username, "password": password})
    except Exception as exc:
        print(f"Не получилось: {exc}")
        print(HINT)
        return 1
    print(f"Готово: {DOMAIN} указывает на этот дом и будет обновляться сам, в том числе после перезагрузки.")
    return 0


def main() -> int:
    if sys.argv[1:] == ["setup"]:
        return setup()
    account = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    if "--once" in sys.argv:
        update(account)
        return 0
    install_boot_hook()
    while True:
        try:
            update(account)
        except Exception as exc:
            log(f"error: {exc}")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
