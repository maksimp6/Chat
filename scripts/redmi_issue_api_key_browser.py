"""Issue a Yandex API key from the AI Studio page open on the Redmi 9 browser.

The browser clicks the create-key control with a 90-day lifetime. The secret is
read inside the page, returned only to this runner process in memory, and posted
to the local import endpoint. Nothing secret is printed, logged or screenshotted.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

IMPORT_URL = "http://127.0.0.1:8080/api/provider-credentials/yandex/issued"
LIFETIME = timedelta(days=90)

CREATE_JS = """JSON.stringify((()=>{
  const visible = e => e.getClientRects().length > 0;
  const text = e => (e.innerText || '').trim();
  const option = [...document.querySelectorAll('label,span,div')].find(e => visible(e) && text(e) === '90 дней');
  if (!option) throw Error('90-day option not found');
  option.click();
  const button = [...document.querySelectorAll('button')].find(e => visible(e) && text(e) === 'Создать');
  if (!button) throw Error('Create button not found');
  button.click();
  return {submitted: true};
})())"""

RESULT_JS = """JSON.stringify((()=>{
  const body = document.body.innerText || '';
  const fields = [...document.querySelectorAll('input,textarea')].map(e => e.value || '').join('\\n');
  const text = body + '\\n' + fields;
  const secret = (text.match(/AQVN[A-Za-z0-9_-]{20,}/) || [null])[0];
  const ids = [...new Set(text.match(/\\b[a-z0-9]{20}\\b/g) || [])];
  return {secret: secret, id_candidates: ids.slice(0, 20)};
})())"""


def child_issue(folder_id):
    import redmi_screenshot_capture as cap
    from redmi_browser_probe import aistudio_actor

    cap.device_model_guard()
    rdp = cap.RDP()
    try:
        actor, url = aistudio_actor(rdp)
        if folder_id not in urlparse(url).path:
            raise RuntimeError("The open AI Studio page is not the expected folder")
        # A result still open from an earlier attempt is read first, so no second key is created.
        result = rdp.evaluate(actor, RESULT_JS)
        if not result.get("secret"):
            rdp.evaluate(actor, CREATE_JS)
            time.sleep(3)
            result = rdp.evaluate(actor, RESULT_JS)
    finally:
        rdp.s.close()
    # Returned to the parent process through a pipe only; never printed here.
    sys.stdout.write(json.dumps(result))


def post_import(payload, token):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        IMPORT_URL, data=json.dumps(payload).encode(), method="POST", headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "_issue":
        child_issue(sys.argv[2])
        return
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("RUNNER_TEMP"):
        raise RuntimeError("Run the issuance inside the existing GitHub Actions runner")
    token = os.environ.get("LOGOS_PROVIDER_CREDENTIALS_TOKEN", "")
    folder = os.environ.get("YANDEX_FOLDER_ID", "")
    if not re.fullmatch(r"[a-z0-9]{20}", folder):
        raise RuntimeError("YANDEX_FOLDER_ID must be the 20-character folder id")
    argv = [sys.executable, str(Path(__file__).resolve()), "_issue", folder]
    safe_env = {
        "PATH": os.environ.get("PATH", "/system/bin"),
        "TMPDIR": os.environ["RUNNER_TEMP"],
    }
    result = subprocess.run(
        ["su", "-c", shlex.join(argv)], env=safe_env, capture_output=True, timeout=120
    )
    if result.returncode:
        raise RuntimeError("Browser issuance failed before a key was read")
    found = json.loads(result.stdout.decode())
    secret = found.get("secret")
    if not secret:
        raise RuntimeError("No API key was found in the create-key result")
    candidates = [c for c in found.get("id_candidates", []) if c != folder]
    if len(candidates) != 1:
        raise RuntimeError(
            "Key id not unique on the page: %d candidates" % len(candidates)
        )
    now = datetime.now(UTC)
    payload = {
        "project_id": folder,
        "provider_key_id": candidates[0],
        "api_key": secret,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + LIFETIME).isoformat().replace("+00:00", "Z"),
    }
    response = post_import(payload, token)
    print(
        json.dumps(
            {
                k: response.get(k)
                for k in ("status", "fingerprint", "expires_at", "storage_verified")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
