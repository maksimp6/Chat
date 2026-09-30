#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys

ROOT = Path(sys.argv[1]).resolve()
OVERLAY = Path(sys.argv[2]).resolve()

service_src = OVERLAY / "AliceRootAgentService.java"
service_dst = ROOT / "app/src/main/java/com/termux/app/alice/AliceRootAgentService.java"
service_dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(service_src, service_dst)

manifest_path = ROOT / "app/src/main/AndroidManifest.xml"
manifest = manifest_path.read_text(encoding="utf-8")
service_block = """
        <service
            android:name=".app.alice.AliceRootAgentService"
            android:exported="false"
            android:foregroundServiceType="specialUse"
            tools:targetApi="p" />
"""
anchor = """        <service
            android:name=".app.TermuxService"
            android:exported="false" />
"""
if service_block.strip() not in manifest:
    if anchor not in manifest:
        raise SystemExit("TermuxService manifest anchor not found")
    manifest = manifest.replace(anchor, anchor + service_block)
manifest_path.write_text(manifest, encoding="utf-8")

app_path = ROOT / "app/src/main/java/com/termux/app/TermuxApplication.java"
app = app_path.read_text(encoding="utf-8")
if "import android.content.Intent;" not in app:
    app = app.replace("import android.content.Context;\n", "import android.content.Context;\nimport android.content.Intent;\nimport android.os.Build;\n")
if "import com.termux.app.alice.AliceRootAgentService;" not in app:
    app = app.replace("import com.termux.BuildConfig;\n", "import com.termux.BuildConfig;\nimport com.termux.app.alice.AliceRootAgentService;\n")

startup = """
        Intent aliceRootAgent = new Intent(context, AliceRootAgentService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(aliceRootAgent);
        } else {
            context.startService(aliceRootAgent);
        }

"""
anchor = "        // Set crash handler for the app\n"
if "Intent aliceRootAgent" not in app:
    if anchor not in app:
        raise SystemExit("TermuxApplication startup anchor not found")
    app = app.replace(anchor, startup + anchor)
app_path.write_text(app, encoding="utf-8")

gradle_path = ROOT / "app/build.gradle"
gradle = gradle_path.read_text(encoding="utf-8")
gradle = gradle.replace(
    'manifestPlaceholders.TERMUX_APP_NAME = "Termux"',
    'manifestPlaceholders.TERMUX_APP_NAME = "Alice Root Termux"',
)
gradle_path.write_text(gradle, encoding="utf-8")

print("Applied Alice Root Agent overlay")
print(service_dst)
print(manifest_path)
print(app_path)
print(gradle_path)
