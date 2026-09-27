from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Versions with Android wheels in Chaquopy's repository (https://chaquo.com/pypi-13.1/).
CHAQUOPY_NATIVE_WHEELS = {"cryptography": {"42.0.8"}}


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    return pins


def test_android_native_pins_have_chaquopy_wheels():
    pins = _pins(ROOT / "android" / "requirements.txt")
    for package, versions in CHAQUOPY_NATIVE_WHEELS.items():
        assert pins.get(package) in versions, (
            f"android/requirements.txt pins {package}=={pins.get(package)}, "
            f"but Chaquopy only provides {sorted(versions)}"
        )


def test_gradle_installs_android_requirements_file():
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'install("-r", "../requirements.txt")' in gradle
