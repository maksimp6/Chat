# Alice Pro for Windows

The desktop client wraps the existing Alice Pro Flask UI in a native Windows window.
It does not duplicate the frontend or run an Electron/Node production runtime.

## Runtime model

1. `desktop/launcher.py` chooses the desktop data directory.
2. Before importing `app.py`, it sets `ALICE_DB_PATH` to
   `%LOCALAPPDATA%\\Alice Pro\\alice_pro.db`.
3. Alice Pro starts a local WSGI server on `127.0.0.1` with an ephemeral port.
4. pywebview opens that local URL in a desktop window.
5. Closing the window shuts down the local server.

The server is intentionally loopback-only. The desktop launcher must not bind to
`0.0.0.0`.

## Local development

Use Python 3.13 for the Windows desktop packaging path.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-desktop.txt
python -m desktop.launcher
```

Windows uses the installed WebView2 runtime through pywebview. Current Windows
installations normally already include WebView2; if it is missing, install the
Microsoft WebView2 Runtime before starting Alice Pro.

## Build the executable

```powershell
pip install -r requirements-desktop.txt
pyinstaller --noconfirm --clean desktop/AlicePro.spec
```

The output is:

```text
dist/AlicePro.exe
```

The executable bundles repository-local `templates/` and `static/` assets.
Credentials are not embedded in the executable. On first launch Alice Pro generates
its provider-credential encryption key and stores it in the operating system keyring
(Windows Credential Locker via Python `keyring`) before the Flask backend is imported.
If the system keyring cannot read or persist the key, startup fails closed instead of
saving provider credentials without encryption.

## GitHub Actions

The `Windows desktop app` workflow builds and uploads `AlicePro.exe` as an
artifact. Pull requests that change desktop code or frontend assets trigger the
build automatically. It can also be started manually with `workflow_dispatch`.

## Desktop data override

For testing or portable development, override the data directory:

```powershell
$env:ALICE_DESKTOP_DATA_DIR = "D:\\AliceProData"
python -m desktop.launcher
```

Do not point production desktop data at a temporary directory.
