#!/data/data/com.termux/files/usr/bin/python
"""Manage Alice Pet overlay via existing authorized ADB connection."""
import argparse
import json
import re
import subprocess
import sys

COMPONENT="org.alice.pet/.PetActivity"
def adb(*args):
    result=subprocess.run(["adb",*args],capture_output=True,text=True,timeout=12)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ADB command failed")
    return result.stdout.strip()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["show","hide","move","stop","status","style","vulkan_probe","cursor_preview","cursor_hide"])
    parser.add_argument("--style",choices=["classic","ember"],default="classic")
    parser.add_argument("--x",type=int,default=1320)
    parser.add_argument("--y",type=int,default=380)
    args=parser.parse_args()
    try:
        if args.command=="status":
            window=adb("shell","dumpsys","window")
            match=re.search(r"Window #\d+ Window\{[^\n]*org\.alice\.pet\}:(.*?)(?=\n  Window #|\Z)",window,re.S)
            all_attrs=re.findall(r"mAttrs=\{\((\d+),(\d+)\)\((\d+)x(\d+)\)",window)
            chosen=next((a for a in all_attrs if a[2]=="300" and a[3]=="330"),None)
            attrs=re.match(r"(\d+),(\d+),(\d+),(\d+)",",".join(chosen)) if chosen else None
            flags=match.group(1) if match else ""
            print(json.dumps({"window_present":bool(match),"position":list(map(int,attrs.group(1,2))) if attrs else None,"size":list(map(int,attrs.group(3,4))) if attrs else None,"touch_passthrough_flag":"NOT_TOUCHABLE" in flags,"visibility_unverified":True}))
        elif args.command=="show":
            adb("shell","am","start","-n","org.alice.pet/.PetActivity")
            adb("shell","am","start","-n",COMPONENT,"--es","pet_action","show")
            print("show submitted")
        elif args.command=="stop":
            adb("shell","am","force-stop","org.alice.pet")
            print("stopped")
        else:
            command=["shell","am","start","-n",COMPONENT,"--es","pet_action",args.command]
            if args.command=="style":
                command += ["--es","style",args.style]
            if args.command=="cursor_preview":
                if not (0<=args.x<=4000 and 0<=args.y<=4000):
                    parser.error("cursor preview coordinates must be 0..4000")
                command+=["--ei","x",str(args.x),"--ei","y",str(args.y)]
            if args.command=="move":
                if not (0<=args.x<=2000 and 0<=args.y<=2000):
                    parser.error("coordinates must be 0..2000")
                command+=["--ei","x",str(args.x),"--ei","y",str(args.y)]
            print(adb(*command))
    except (RuntimeError,subprocess.TimeoutExpired) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0
if __name__=="__main__":
    raise SystemExit(main())
