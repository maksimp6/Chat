"""Read-only probe of the tabs already open in the Redmi 9 browser.

Reports page structure only: titles, visible control labels, headings and input
types. It never reads input values, cookies or storage, never navigates, and
never changes the device.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
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


def probe():
    import redmi_screenshot_capture as cap

    cap.device_model_guard()
    rdp = cap.RDP()
    report = []
    try:
        for tab in rdp.request("root", "listTabs")["tabs"]:
            url = tab.get("url", "")
            if not url or url.startswith(("about:", "chrome:", "moz-extension:")):
                continue
            parsed = urlparse(url)
            actor = rdp.request(tab["actor"], "getTarget")["frame"]["consoleActor"]
            report.append({"host": parsed.hostname, "path": parsed.path,
                           "page": rdp.evaluate(actor, PROBE_JS)})
    finally:
        rdp.s.close()
    print(json.dumps(report, ensure_ascii=False, indent=1))


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "_probe":
        probe()
        return
    required = ("GITHUB_ACTIONS", "RUNNER_TEMP")
    if os.environ.get("GITHUB_ACTIONS") != "true" or any(not os.environ.get(k) for k in required):
        raise RuntimeError("Run the browser probe inside the existing GitHub Actions runner")
    argv = [sys.executable, str(Path(__file__).resolve()), "_probe"]
    safe_env = {"PATH": os.environ.get("PATH", "/system/bin"), "TMPDIR": os.environ["RUNNER_TEMP"]}
    result = subprocess.run(["su", "-c", shlex.join(argv)], env=safe_env, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-1200:] or "Probe failed")
    print(result.stdout.decode().strip())


if __name__ == "__main__":
    main()
