"""Audit a filesystem subset; inventory the full native profile without invoking it."""

import argparse
import hashlib
import http.server
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

PROBE_ERRORS = (
    AssertionError,
    OSError,
    ValueError,
    TypeError,
    LookupError,
    ImportError,
    StopIteration,
    subprocess.SubprocessError,
    zipfile.BadZipFile,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--external", action="store_true", help="Also GET public GitHub repository metadata"
    )
    options = parser.parse_args()
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    fixture = Path(tempfile.mkdtemp(prefix="logos_probe_", dir=ROOT))
    os.environ["LOGOS_MEMORY_PATH"] = str(fixture / "audit.memory")
    backup_dir = ROOT / ".safe_backups"
    backup_existed = backup_dir.exists()
    server = None
    backup_files = []
    extra_files = []
    report = {"status": "running", "tools": {}, "defaults": {}, "null": {}}

    def check(condition):
        if not condition:
            raise AssertionError("independent_postcondition")

    def rel(path):
        return str(path.relative_to(ROOT))

    try:
        from scripts.logos_knowledge import DEFAULT_STORE_ID
        from scripts.logos_quality import MODEL, dialogue_params
        from tool_providers import filesystem
        from tool_providers.registry import registry
        from tool_providers.universal_tool_platform import (
            UniversalToolCall,
            UniversalToolExecutor,
            validate_json_schema,
        )

        check(Path(filesystem.BASE_DIR).resolve() == ROOT)
        executor = UniversalToolExecutor(registry)
        categories = registry.get_available_categories()
        report["categories"] = {name: len(names) for name, names in sorted(categories.items())}
        native = dialogue_params([DEFAULT_STORE_ID], list(categories))
        native_definitions = registry.get_definitions(set(native["active_tool_categories"]))
        offered_names = {tool["name"] for tool in native_definitions}
        report["native_profile"] = {
            "model": MODEL,
            "categories": native["active_tool_categories"],
            "offered_count": len(native_definitions),
            # Registered tools this host or transport cannot execute are not offered.
            "not_offered": sorted(
                name for names in categories.values() for name in names if name not in offered_names
            ),
            "hosted_enabled": {
                name: cfg["enabled"] for name, cfg in native["tools_config"].items()
            },
            "configured_vector_store_ids": native["tools_config"]["file_search"][
                "vector_store_ids"
            ],
            "verification": "configuration_only; hosted_and_other_categories_not_executed",
            "knowledge_status": "not_prepared_or_verified_by_diagnostic",
        }
        # logos-tools deliberately executes only temporary filesystem fixtures.
        # Native dialogue uses the full profile above after knowledge preparation.
        active = ["filesystem"]
        definitions = registry.get_definitions(set(active))
        exposed = {tool["name"]: tool["parameters"] for tool in definitions}
        report["mode"] = "logos-tools"
        report["generation_requests"] = 0
        report["provider_credentials_used"] = False
        report["active_categories"] = active
        report["hosted_enabled"] = dict.fromkeys(native["tools_config"], False)
        report["exposed_count"] = len(exposed)
        report["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        check(
            active == ["filesystem"]
            and len(exposed) == 17
            and not any(report["hosted_enabled"].values())
        )

        def invoke(name, arguments):
            return executor.execute(
                UniversalToolCall(
                    tool_name=name, arguments=arguments, transport="responses_api", approved=False
                )
            )

        def probe(name, arguments, verify, *, section="tools", label=None):
            label = label or name
            result = invoke(name, arguments)
            if not result.get("success"):
                phase = result.get("metadata", {}).get("phase", "execution")
                status = "blocked" if phase == "approval_required" else "failed"
                report[section][label] = {
                    "s": status,
                    "phase": phase,
                    "error_sha256": hashlib.sha256(str(result.get("error")).encode()).hexdigest()[
                        :12
                    ],
                }
                return None
            try:
                verify(result.get("data"))
                report[section][label] = "pass"
            except PROBE_ERRORS as exc:
                report[section][label] = {"s": "failed", "check": type(exc).__name__}
            return result.get("data")

        marker = "LOGOS_PROBE_MARKER_" + fixture.name
        text_file = fixture / (fixture.name + ".txt")
        text = "alpha\n" + marker + "\nomega\n"
        text_file.write_text(text)
        python_file = fixture / (fixture.name + ".py")
        python_file.write_text("OLD = 1\n")
        code = "import math\n\ndef twice(value):\n    return value * 2\n\nclass Probe:\n    def method(self, item):\n        return item\n"
        backup_candidate = backup_dir / (python_file.name + ".bak")
        check(not backup_candidate.exists())
        backup_files.append(backup_candidate)
        archive = fixture / "payload.zip"
        extraction = fixture / "extracted"
        copied = fixture / "copied.txt"
        moved = fixture / "moved.txt"
        nested = fixture / "nested" / "inner"
        disposable = fixture / "disposable"
        disposable.mkdir()
        (disposable / "owned.txt").write_text("delete fixture")

        probe("make_directory", {"path": rel(nested)}, lambda data: check(nested.is_dir()))
        probe(
            "write_file",
            {"path": rel(python_file), "content": code},
            lambda data: check(
                python_file.read_text() == code and backup_files[0].read_text() == "OLD = 1\n"
            ),
        )
        probe(
            "read_file",
            {"path": rel(text_file), "offset": 1, "length": 4, "whence": "start"},
            lambda data: check(
                data["content"] == "lpha" and data["bytes_read"] == 4 and data["offset"] == 1
            ),
        )
        probe(
            "python_ast_outline",
            {"path": rel(python_file)},
            lambda data: check(
                data["imports"] == ["math"]
                and [item["name"] for item in data["functions"]] == ["twice"]
                and data["classes"][0]["name"] == "Probe"
                and data["classes"][0]["methods"][0]["args"] == ["self", "item"]
            ),
        )
        probe(
            "calculate_hash",
            {"path": rel(text_file), "algorithm": "sha256"},
            lambda data: check(
                data["digest"] == hashlib.sha256(text_file.read_bytes()).hexdigest()
                and data["size_bytes"] == len(text.encode())
            ),
        )

        def verify_archive(data):
            with zipfile.ZipFile(archive) as zipped:
                check(
                    data["files_archived"] == 1
                    and zipped.namelist() == [text_file.name]
                    and zipped.read(text_file.name) == text.encode()
                )

        probe(
            "zip_compress",
            {"source_path": rel(text_file), "archive_path": rel(archive)},
            verify_archive,
        )
        probe(
            "zip_extract",
            {"archive_path": rel(archive), "target_dir": rel(extraction)},
            lambda data: check(
                data["extracted_count"] == 1 and (extraction / text_file.name).read_text() == text
            ),
        )
        probe(
            "copy_or_move_file",
            {"src": rel(text_file), "dst": rel(copied), "action": "copy"},
            lambda data: check(
                copied.read_bytes() == text_file.read_bytes() and text_file.exists()
            ),
        )
        probe(
            "copy_or_move_file",
            {"src": rel(copied), "dst": rel(moved), "action": "move"},
            lambda data: check(moved.read_text() == text and not copied.exists()),
            section="defaults",
            label="copy_or_move_file:move",
        )
        probe("delete_path", {"path": rel(disposable)}, lambda data: check(not disposable.exists()))
        probe(
            "list_directory",
            {"path": rel(fixture)},
            lambda data: check(
                {text_file.name, python_file.name, "nested"}
                <= {item["name"] for item in data["items"]}
            ),
        )
        probe(
            "glob_search",
            {"pattern": rel(fixture / "*.txt")},
            lambda data: check(
                rel(text_file) in {item["path"] for item in data["matches"]} and data["count"] >= 1
            ),
        )
        probe(
            "grep_search",
            {"query": marker, "file_pattern": text_file.name, "max_matches": 10},
            lambda data: check(
                any(
                    item["file"] == rel(text_file) and item["line"] == 2 and item["text"] == marker
                    for item in data["matches"]
                )
            ),
        )
        probe(
            "file_stat",
            {"path": rel(text_file)},
            lambda data: check(
                data["is_file"] is True
                and data["is_dir"] is False
                and data["size_bytes"] == text_file.stat().st_size
            ),
        )
        command_marker = fixture / "unapproved-command-ran.txt"
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path("
                + repr(rel(command_marker))
                + ").write_text('unexpected command execution'); print(Path("
                + repr(rel(text_file))
                + ").read_text().splitlines()[0])",
            ]
        )
        probe(
            "run_command",
            {"command": command, "timeout": 5},
            lambda data: check(
                data["returncode"] == 0 and data["stdout"] == "alpha" and data["stderr"] == ""
            ),
        )
        check(not command_marker.exists())
        check(report["tools"]["run_command"]["s"] == "blocked")
        check(report["tools"]["run_command"]["phase"] == "approval_required")
        report["command_execution"] = "not_run; approval_required; fixture_effect_absent"
        probe(
            "get_system_info",
            {},
            lambda data: check(
                Path(data["project_dir"]).resolve() == ROOT
                and data["disk_total_gb"] > 0
                and data["python_version"] == sys.version.split()[0]
            ),
        )
        before_patch = python_file.read_bytes()
        probe(
            "apply_patch",
            {"path": rel(python_file), "search_text": "import math", "replace_text": "replacement"},
            lambda data: check(False),
        )
        check(python_file.read_bytes() == before_patch)
        report["patch_schema_search_text"] = "search_text" in exposed["apply_patch"].get(
            "properties", {}
        )

        body = b"logos-loopback-probe"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:" + str(server.server_port) + "/probe"
        probe(
            "http_fetch",
            {"url": url, "timeout": 5},
            lambda data: check(
                data["status"] == 200
                and data["body"] == body.decode()
                and data["bytes_received"] == len(body)
            ),
        )

        default_copy = fixture / "default-copy.txt"
        default_extract = ROOT / text_file.name
        check(not default_extract.exists())
        extra_files.append(default_extract)
        defaults = {
            "read_file": ({"path": rel(text_file)}, lambda data: check(data["content"] == text)),
            "calculate_hash": (
                {"path": rel(text_file)},
                lambda data: check(
                    data["algorithm"] == "sha256"
                    and data["digest"] == hashlib.sha256(text.encode()).hexdigest()
                ),
            ),
            "zip_extract": (
                {"archive_path": rel(archive)},
                lambda data: check(default_extract.read_text() == text),
            ),
            "copy_or_move_file": (
                {"src": rel(text_file), "dst": rel(default_copy)},
                lambda data: check(default_copy.read_text() == text),
            ),
            "list_directory": (
                {},
                lambda data: check(fixture.name in {item["name"] for item in data["items"]}),
            ),
            "grep_search": (
                {"query": "def twice"},
                lambda data: check(
                    any(item["file"] == rel(python_file) for item in data["matches"])
                ),
            ),
            "run_command": (
                {"command": command},
                lambda data: check(data["returncode"] == 0 and data["stdout"] == "alpha"),
            ),
            "http_fetch": (
                {"url": url},
                lambda data: check(data["status"] == 200 and data["body"] == body.decode()),
            ),
        }
        for name, (arguments, verify) in defaults.items():
            probe(name, arguments, verify, section="defaults")
            original = registry.get_universal_definition(name).input_schema
            optional = set(original.get("properties", {})) - set(original.get("required", []))
            null_args = {**arguments, **{key: None for key in optional}}
            advertised_valid = not validate_json_schema(null_args, exposed[name])
            result = invoke(name, null_args)
            report["null"][name] = {
                "advertised": advertised_valid,
                "accepted": result["success"],
                "phase": result.get("metadata", {}).get("phase", "execution"),
            }
            if name == "run_command":
                check(not command_marker.exists())
                check(report["defaults"][name]["s"] == "blocked")
                check(result.get("metadata", {}).get("phase") == "approval_required")
        if options.external:
            probe(
                "http_fetch",
                {"url": "https://api.github.com/repos/maksimp6/Chat", "timeout": 10},
                lambda data: check(
                    data["status"] == 200
                    and re.search(r'"full_name"\s*:\s*"maksimp6/Chat"', data["body"]) is not None
                ),
                section="defaults",
                label="http_fetch:external",
            )
        check(set(report["tools"]) == set(exposed))
        report["status"] = "completed"
    except PROBE_ERRORS as exc:
        report.update(status="harness_error", harness_error=type(exc).__name__)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        for path in backup_files + extra_files:
            path.unlink(missing_ok=True)
        shutil.rmtree(fixture, ignore_errors=True)
        if not backup_existed and backup_dir.exists() and not any(backup_dir.iterdir()):
            backup_dir.rmdir()
        report["cleanup"] = not fixture.exists() and all(
            not path.exists() for path in backup_files + extra_files
        )
        if not report["cleanup"]:
            report["status"] = "harness_error"
            report["harness_error"] = "CleanupIncomplete"
        encoded = json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if len(encoded) >= 4000:
            raise RuntimeError("audit output exceeds native trace limit")
        print(encoded)

    raise SystemExit(0 if report["status"] == "completed" else 1)
