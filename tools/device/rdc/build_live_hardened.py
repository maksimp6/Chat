#!/usr/bin/env python3
"""Build a separate, fail-closed Live Server hardening candidate (never touch active)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys

BASE=Path.home()/".alice-rdc"
DEST=BASE/"hardening-v1"
EXPECTED={
    "android_live_server.py":"d65962c0b1320e02e0b2a8d0185c0d825d4608bc6cbdddebda3f63fdc39ebd8e",
    "android_actions.py":"48ab9da0823d7a9a634b5501feae44561cd52c7332e7f3d7c7222bdbd5b3af2c",
    "android_accessibility.py":"1f0f8e62cd81ff70176cdfe04a3951a59c6b01d56ca77122a47b4aa3d9f95bf6",
}
def replace(source, old, new, label):
    if source.count(old)!=1: raise AssertionError(f"{label}: found {source.count(old)}, expected 1")
    return source.replace(old,new,1)

def build_actions(s):
    s=replace(s,"import pathlib\n","import pathlib\nimport re\nimport secrets\n","import action dependencies")
    s=replace(s,'POLICY = pathlib.Path.home() / ".alice-rdc" / "live-device.json"',
              'POLICY = pathlib.Path(os.environ.get("ALICE_LIVE_DEVICE_POLICY", str(pathlib.Path.home() / ".alice-rdc" / "live-device.json")))',
              "policy path")
    start=s.index("def device():")
    end=s.index("\ndef action(a):", start)
    new='''def device():
    """Match a specifically enrolled ADB transport AND its Android ID.

    Both checks run for each operation. A re-pair with a changed ADB serial must
    be explicitly re-enrolled, not silently redirected to another phone.
    """
    try:
        data = json.loads(POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Trusted target policy unavailable") from error
    if (not isinstance(data, dict)
        or set(data)!={"model","device","serial","android_id"}
        or data.get("model")!="Redmi_9" or data.get("device")!="galahad"
        or not isinstance(data.get("serial"),str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{5,100}",data["serial"])
        or not isinstance(data.get("android_id"),str)
        or not re.fullmatch(r"[0-9a-f]{16}",data["android_id"])):
        raise RuntimeError("Invalid pinned target policy")
    serial=data["serial"]
    listed=0
    for row in adb("devices","-l").splitlines():
        parts=row.split()
        if len(parts)<3 or parts[0]!=serial or parts[1]!="device":
            continue
        attrs=dict(p.split(":",1) for p in parts[2:] if ":" in p)
        if (attrs.get("model"),attrs.get("device"))==(data["model"],data["device"]):
            listed+=1
    if listed!=1:
        raise RuntimeError("Pinned Redmi 9 transport unavailable")
    actual=adb("-s",serial,"shell","settings","get","secure","android_id").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16}",actual) or not secrets.compare_digest(actual,data["android_id"]):
        raise RuntimeError("Pinned Redmi 9 identity mismatch")
    return serial

def keyguard_state(window_dump):
    """Return locked/unlocked only from an authoritative explicit boolean.

    Fail closed if Android omits the field or changes the expected format.
    """
    if not isinstance(window_dump,str):
        return True
    values=re.findall(r"\\b(?:isKeyguardShowing|mShowingLockscreen|mKeyguardShowing)\\s*[=:]\\s*(true|false)\\b",window_dump,re.I)
    if not values:
        return True
    return any(value.lower()=="true" for value in values)
'''
    s=s[:start]+new+s[end:]
    s=replace(s,
        'return {"device": serial, "target": "Redmi_9", "focus": adb(*prefix, "shell", "dumpsys", "window").split("mCurrentFocus=")[-1].splitlines()[0]}',
        '''window=adb(*prefix,"shell","dumpsys","window")
        focus=window.split("mCurrentFocus=",1)[-1].splitlines()[0] if "mCurrentFocus=" in window else ""
        return {"device":serial,"target":"Redmi_9","focus":focus,
                "keyguard_locked":keyguard_state(window)}''',
        "status parsing")
    # Preserve original action behavior; device() now requires trusted enrollment.
    return s

def build_accessibility(s):
    s=replace(s,"import re\n","import re\nimport secrets\n","accessibility dependencies")
    start=s.index("def read_accessibility():")
    end=s.index("    nodes = []",start)
    replacement='''def read_accessibility(serial):
    """Dump XML to private shell-owned temp and clean it on every exit.

    Never use public /sdcard paths, serial is from the Live Server's pinned
    device check; no screen text is printed to logs.
    """
    if not isinstance(serial,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{5,100}",serial):
        raise RuntimeError("Untrusted accessibility device")
    temporary="/data/local/tmp/alice-live-ui-"+secrets.token_hex(12)+".xml"
    prefix=["adb","-s",serial]
    try:
        result=subprocess.run(prefix+["shell","uiautomator","dump",temporary],
                              capture_output=True,timeout=15)
        if result.returncode:
            raise RuntimeError("UI hierarchy unavailable")
        result=subprocess.run(prefix+["exec-out","cat",temporary],
                              capture_output=True,timeout=8,check=True)
        if len(result.stdout)>2_000_000:
            raise RuntimeError("UI hierarchy too large")
        root=ET.fromstring(result.stdout)
    finally:
        cleanup=subprocess.run(prefix+["shell","rm","-f",temporary],
                               capture_output=True,timeout=6)
        if cleanup.returncode:
            raise RuntimeError("UI hierarchy cleanup failed")
'''
    s=s[:start]+replacement+s[end:]
    return s

def build_server(s):
    s=replace(s,"from android_dom_diff import diff as dom_diff\n","", "remove raw DOM diff")
    s=replace(s,"from android_state_merge import StateMerger\n","", "remove raw StateMerger")
    s=replace(s,"from android_state_store import StateStore\n","", "remove raw StateStore")
    s=replace(s,"from android_layout_tree import build_tree\n","", "remove textual layout")
    s=replace(s,"from android_public_tree import public_tree\n",
              "from android_public_tree import public_tree\nfrom android_screen_dom import build as build_ocr_dom\n",
              "safe OCR presentation")
    s=replace(s,'ACTIONS = BASE / "android_actions.py"',
              'ACTIONS = Path(os.environ.get("ALICE_LIVE_ACTIONS", str(BASE / "android_actions.py")))',
              "candidate actions override")
    s=replace(s,'''LAST_DOM = None
STATE_MERGER = StateMerger()
STATE_STORE = StateStore(BASE / "state")
STATE_STORE.restore(STATE_MERGER)
CAPTURE_STATE''',
              '''CAPTURE_STATE''',
              "disable persistent raw OCR")
    start=s.index("    def safe_screen(self):")
    end=s.index("\n    def do_GET(self):",start)
    s=s[:start]+'''    def bound_status(self):
        """Re-evaluate device binding on every request, keep identity private."""
        status=execute([ACTIONS,"status"])
        serial=status.get("device")
        if (status.get("target")!=TRUSTED_TARGET
            or not isinstance(serial,str) or not serial
            or not isinstance(status.get("focus"),str)
            or type(status.get("keyguard_locked")) is not bool):
            return None
        self._verified_serial=serial
        return status

    def safe_screen(self):
        """No observation or generic actions while Keyguard is showing."""
        status=self.bound_status()
        return status is not None and not status["keyguard_locked"]
''' + s[end:]
    if s.count("hierarchy = read_accessibility()")!=2:
        raise AssertionError("expected two accessibility handlers")
    s=s.replace("hierarchy = read_accessibility()",
                "hierarchy = read_accessibility(self._verified_serial)")
    s=replace(s,'''            if path == "/status":
                result = execute([ACTIONS, "status"])
                if result.get("target") != TRUSTED_TARGET:
                    return self.send(403, {"error": "wrong_device"})
                os.environ["ANDROID_SERIAL"] = result["device"]
                return self.send(200, result)
''',
              '''            if path == "/status":
                result=self.bound_status()
                if result is None:
                    return self.send(403,{"error":"device_not_authorized"})
                return self.send(200,{"target":TRUSTED_TARGET,"device_bound":True,
                                      "keyguard_locked":result["keyguard_locked"],
                                      "focus_present":bool(result["focus"])})
''',
              "privacy-safe status")
    start=s.index('            if path == "/screen/dom":')
    end=s.index('            if path == "/screen.jpg":',start)
    s=s[:start]+'''            if path == "/screen/dom":
                # Legacy text-bearing diff, merge, checkpoint and journal disabled.
                # Only return geometric roles without OCR labels or raw text.
                with FRAME_LOCK:
                    if not self.safe_screen():
                        return self.send(423,{"error":"screen_locked_or_unavailable"})
                    execute([ACTIONS,"screenshot"])
                    changed=DETECTOR.compare(IMAGE)
                    if not changed["changed"]:
                        return self.send(200,{"changed":False,"sequence":changed["sequence"],
                                              "redacted":True,"persistent":False})
                    current=build_ocr_dom(IMAGE)
                    if not self.safe_screen():
                        return self.send(409,{"error":"screen_changed"})
                    tree=public_tree(current)
                    return self.send(200,{"changed":True,"sequence":changed["sequence"],
                                          "tree":tree,"redacted":True,"persistent":False})
''' + s[end:]
    s=replace(s,'''                if result.get("target") != TRUSTED_TARGET:''','''                if result.get("target") != TRUSTED_TARGET:''',"identity noop") if False else s
    # One guard for every action. "open" no longer bypasses binding; wake
    # is the only verb allowed when the screen is locked.
    s=replace(s,
              '''                    if command != "open" and not self.safe_screen():
                        return self.send(403, {"error": "device_not_authorized"})
                    result = execute(argv)
                    return self.send(200, result)''',
              '''                    status=self.bound_status()
                    if status is None:
                        return self.send(403,{"error":"device_not_authorized"})
                    if status["keyguard_locked"] and command not in ("wake","status"):
                        return self.send(423,{"error":"screen_locked"})
                    result=execute(argv)
                    return self.send(200,result)''',
              "all actions guard")
    if "os.environ[\"ANDROID_SERIAL\"]" in s or "SAFE_APP" in s or "STATE_STORE.save" in s:
        raise AssertionError("legacy unsafe path remains")
    return s

def enroll_policy():
    sys.path.insert(0,str(BASE))
    import android_actions
    # Existing adapter is allowed to select the currently enrolled Redmi 9.
    # Additional identity fields are stored privately and not logged.
    serial=android_actions.device()
    android_id=android_actions.adb("-s",serial,"shell","settings","get","secure","android_id").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16}",android_id):
        raise AssertionError("ADB target cannot prove a stable Android ID")
    policy={"model":"Redmi_9","device":"galahad","serial":serial,"android_id":android_id}
    policy_path=DEST/"live-device.json"
    policy_path.write_text(json.dumps(policy,ensure_ascii=False),encoding="utf-8")
    policy_path.chmod(0o600)

def main():
    if DEST.exists():
        raise RuntimeError("Candidate already exists; preserve previous run")
    for name,digest in EXPECTED.items():
        actual=hashlib.sha256((BASE/name).read_bytes()).hexdigest()
        if actual!=digest:
            raise RuntimeError(f"Source {name} drifted: refusing patch")
    DEST.mkdir(mode=0o700)
    try:
        edits={"android_live_server.py":build_server,
               "android_actions.py":build_actions,
               "android_accessibility.py":build_accessibility}
        for name,change in edits.items():
            s=(BASE/name).read_text(encoding="utf-8")
            updated=change(s)
            compile(updated,name,"exec")
            target=DEST/name
            target.write_text(updated,encoding="utf-8")
            target.chmod(0o600)
        enroll_policy()
    except BaseException:
        shutil.rmtree(DEST,ignore_errors=True)
        raise
    print(json.dumps({"status":"STAGED","files":sorted(edits),
                      "trusted_target_pinned":True,
                      "raw_ocr_persistence_disabled":True,
                      "base_untouched":True}))

if __name__=="__main__":
    main()
