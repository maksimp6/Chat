#!/usr/bin/env python3
"""Read existing tools-check evidence on the runner; never start a generation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from logos_tools_self_check import evaluate_tool_evidence


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise RuntimeError("Read-only diagnostic does not follow redirects")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def identifier(value):
    if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)
            and not re.search(r"gh[pousr]_|github_pat_|sk-|AQVN|BEGIN", value, re.IGNORECASE)):
        return value
    return "UNKNOWN"


def run(manifest_path):
    if not (os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("CI") == "true"
            and all(os.environ.get(key) for key in ("GITHUB_RUN_ID", "RUNNER_NAME", "RUNNER_TEMP"))):
        raise RuntimeError("LOCAL_TESTS_FORBIDDEN")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenario = manifest["creation_scenario"]
    baseline = scenario["after_reload"]
    conversation_id = baseline["current_id"]
    if baseline.get("message_count") != 0 or not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("Capture baseline is not a proven empty conversation")
    original = scenario["checkpoints"]["tools_self_check"]["question"]
    if not isinstance(original, str) or not original.strip():
        raise ValueError("Captured question is missing")
    # HTML input type=text removes CR and LF; retain every other character.
    submitted = original.replace("\r", "").replace("\n", "")
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def get(path):
        url = "http://127.0.0.1:8080" + path
        with opener.open(Request(url, method="GET"), timeout=15) as response:
            if response.geturl() != url or response.status != 200:
                raise RuntimeError("Unexpected read response")
            return json.load(response)

    prefix = "/api/conversations/" + quote(conversation_id, safe="")
    categories = get("/api/tools/categories")["categories"]
    active = get(prefix + "/tools")["active_tool_categories"]
    raw_messages = get(prefix + "/messages")["messages"]
    messages = []
    for message in raw_messages:
        trace = message.get("trace") or {}
        messages.append({"role": message.get("role"), "text": message.get("text", ""),
                         "invocation_id": trace.get("invocation_id")})
    traces = {}
    for message in messages:
        invocation_id = message["invocation_id"]
        if message["role"] == "assistant" and isinstance(invocation_id, str) and invocation_id:
            traces[invocation_id] = get("/api/invocations/" + quote(invocation_id, safe="") + "/trace")
    report = evaluate_tool_evidence(categories, active, [], messages, traces, submitted)
    report.update(diagnostic_status="completed", generation_requests_started=0,
                  original_question_sha256=digest(original), submitted_question_sha256=digest(submitted),
                  normalization="html_input_type_text_remove_CR_LF_only",
                  conversation_id_sha256=digest(conversation_id),
                  baseline_message_count=0, inventory_source="current_app_read_only")
    report["trace_errors"] = [
        {"invocation_id_sha256": digest(invocation_id),
         "source": identifier(error.get("source")), "type": identifier(error.get("type"))}
        for invocation_id, trace in traces.items() for error in trace.get("errors", [])
        if isinstance(error, dict)
    ]
    request_models = set()
    for trace in traces.values():
        for event in trace.get("events", []):
            if event.get("type") == "api_request_manifest":
                model = event.get("payload", {}).get("manifest", {}).get("model", {}).get("value")
                if isinstance(model, str) and re.fullmatch(r"gpt://[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+/[A-Za-z0-9_.-]+", model):
                    request_models.add(identifier(model.split("/")[-2]))
    report["request_models"] = sorted(request_models)
    version = get("/api/version")
    revision = version.get("runtime_revision")
    report["runtime"] = {}
    if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{7,40}", revision):
        report["runtime"]["revision"] = revision
        report["runtime"]["matches_capture"] = revision == manifest.get("runtime_revision")
    if type(version.get("process_id")) is int and version["process_id"] > 0:
        report["runtime"]["process_id"] = version["process_id"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.manifest)
    except Exception as error:
        report = {"status": "ERROR", "diagnostic_status": "failed", "generation_requests_started": 0,
                  "error_type": type(error).__name__, "reason": "read_only_evidence_recheck_failed"}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("diagnostic_status") != "completed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
