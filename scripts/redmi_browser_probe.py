"""Probe of the tabs already open in the Redmi 9 browser.

Default mode reports page structure only: titles, visible control labels,
headings and input types. It never reads input values, cookies or storage and
never navigates.

Mode open_api_key_form clicks the AI Studio "Создать API-ключ" control only to
open its form and reports the form structure. It never submits the form, so no
key is created, and it reads no values.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROBE_JS = """JSON.stringify((()=>{
  const visible = e => e.getClientRects().length > 0;
  const label = e => (e.innerText || e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 80);
  return {
    title: document.title.slice(0, 120),
    controls: [...document.querySelectorAll('button,a,[role=button],[role=tab],[role=menuitem]')]
      .filter(visible).map(label).filter(Boolean).slice(0, 80),
    headings: [...document.querySelectorAll('h1,h2,h3')].filter(visible).map(label).slice(0, 30),
    inputs: [...document.querySelectorAll('input,textarea,select')].filter(visible)
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || e.id || e.getAttribute('aria-label') || ''}))
      .slice(0, 30)
  };
})())"""

OPEN_FORM_JS = """JSON.stringify((()=>{
  const visible = e => e.getClientRects().length > 0;
  const label = e => (e.innerText || e.textContent || '').trim();
  const control = [...document.querySelectorAll('button,a,[role=button]')]
    .find(e => visible(e) && label(e) === 'Создать API-ключ');
  if (!control) throw Error('Create API key control not found');
  control.click();
  return {opened: true};
})())"""

FORM_JS = """JSON.stringify((()=>{
  const visible = e => e.getClientRects().length > 0;
  const label = e => (e.innerText || e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 120);
  const root = document.querySelector('[role=dialog],[aria-modal=true],.modal,[class*=modal],[class*=Modal],[class*=drawer],[class*=Drawer]');
  const scope = root || document;
  return {
    dialog: !!root,
    headings: [...scope.querySelectorAll('h1,h2,h3,h4,[role=heading]')].filter(visible).map(label).slice(0, 20),
    controls: [...scope.querySelectorAll('button,[role=button],[role=option],[role=radio],[role=checkbox],label')]
      .filter(visible).map(label).filter(Boolean).slice(0, 60),
    fields: [...scope.querySelectorAll('input,textarea,select')].filter(visible).map(e => ({
      tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || e.id || '',
      placeholder: (e.placeholder || '').slice(0, 80),
      label: (e.labels && e.labels[0] ? label(e.labels[0]) : e.getAttribute('aria-label') || '').slice(0, 80),
      has_value: !!e.value
    })).slice(0, 30)
  };
})())"""


def aistudio_actor(rdp):
    for tab in rdp.request("root", "listTabs")["tabs"]:
        if urlparse(tab.get("url", "")).hostname == "aistudio.yandex.ru":
            return rdp.request(tab["actor"], "getTarget")["frame"]["consoleActor"], tab[
                "url"
            ]
    raise RuntimeError("No AI Studio tab is open on the phone")


def probe(mode):
    import redmi_screenshot_capture as cap

    cap.device_model_guard()
    rdp = cap.RDP()
    try:
        if mode == "open_api_key_form":
            actor, url = aistudio_actor(rdp)
            rdp.evaluate(actor, OPEN_FORM_JS)
            time.sleep(2)
            report = {
                "url_path": urlparse(url).path,
                "form": rdp.evaluate(actor, FORM_JS),
            }
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return
        report = []
        for tab in rdp.request("root", "listTabs")["tabs"]:
            url = tab.get("url", "")
            if not url or url.startswith(("about:", "chrome:", "moz-extension:")):
                continue
            parsed = urlparse(url)
            actor = rdp.request(tab["actor"], "getTarget")["frame"]["consoleActor"]
            report.append(
                {
                    "host": parsed.hostname,
                    "path": parsed.path,
                    "page": rdp.evaluate(actor, PROBE_JS),
                }
            )
        print(json.dumps(report, ensure_ascii=False, indent=1))
    finally:
        rdp.s.close()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "_probe":
        probe(sys.argv[2])
        return
    required = ("GITHUB_ACTIONS", "RUNNER_TEMP")
    if os.environ.get("GITHUB_ACTIONS") != "true" or any(
        not os.environ.get(k) for k in required
    ):
        raise RuntimeError(
            "Run the browser probe inside the existing GitHub Actions runner"
        )
    mode = os.environ.get("PROBE_MODE", "read")
    if mode not in ("read", "open_api_key_form"):
        raise RuntimeError("Unknown probe mode")
    argv = [sys.executable, str(Path(__file__).resolve()), "_probe", mode]
    safe_env = {
        "PATH": os.environ.get("PATH", "/system/bin"),
        "TMPDIR": os.environ["RUNNER_TEMP"],
    }
    result = subprocess.run(
        ["su", "-c", shlex.join(argv)], env=safe_env, capture_output=True, timeout=120
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.decode(errors="replace")[-1200:] or "Probe failed"
        )
    print(result.stdout.decode().strip())


if __name__ == "__main__":
    main()
