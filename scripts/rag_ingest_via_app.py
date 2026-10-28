"""Ingest GitHub issues, pull requests and Actions run metadata into the chat RAG store.

Runs on the Redmi 9 runner, piped from the repository into python3 so that no copy of
the script or of the data is written to disk. Packs are built in memory and sent to the
running app's own API on 127.0.0.1, so no provider key is needed here. Known secret
shapes are masked and e-mail addresses are replaced by hashes. Nothing is dropped.
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = os.environ["REPO"]
GH_TOKEN = os.environ["GH_TOKEN"]
STORE_ID = os.environ["STORE_ID"]
APP = "http://127.0.0.1:8080"
GITHUB = "https://api.github.com"
PACK_LIMIT = 4 * 1024 * 1024
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

JSON_SECRET = re.compile(
    r'(?i)(["\'](?:password|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|'
    r'private[_-]?key|secret[_-]?key|token|secret|credential)["\']\s*:\s*)'
    r'(?P<quote>["\'])(?P<value>[^"\'\r\n]{8,})(?P=quote)'
)
AUTH_SECRET = re.compile(
    r"(?i)(\bauthorization\s*[\"']?\s*[:=]\s*[\"']?\s*(?:api-key|basic|bearer)\s+)[A-Za-z0-9._~+/=-]{8,}"
)
TOKEN_SHAPE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"AQVN[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")

REDACTIONS = {"count": 0}


def mask(text):
    def secret(match):
        value = match.group("value")
        if value.casefold().startswith(("${{", "${", "$", "your_", "changeme", "placeholder", "[redacted]")):
            return match.group(0)
        REDACTIONS["count"] += 1
        return match.group(1) + match.group("quote") + "[REDACTED]" + match.group("quote")

    def auth(match):
        REDACTIONS["count"] += 1
        return match.group(1) + "[REDACTED]"

    def shape(_match):
        REDACTIONS["count"] += 1
        return "[REDACTED]"

    def email(match):
        REDACTIONS["count"] += 1
        return "[email:" + hashlib.sha256(match.group().casefold().encode()).hexdigest()[:16] + "]"

    text = JSON_SECRET.sub(secret, text)
    text = AUTH_SECRET.sub(auth, text)
    text = TOKEN_SHAPE.sub(shape, text)
    return EMAIL.sub(email, text)


def github(url):
    for attempt in range(8):
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + GH_TOKEN,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "logos-rag-ingest",
            },
        )
        try:
            with OPENER.open(request, timeout=60) as response:
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset")
                if remaining is not None and int(remaining) < 10 and reset:
                    time.sleep(max(0, int(reset) - time.time()) + 5)
                return json.loads(response.read().decode()), response.headers.get("Link", "")
        except urllib.error.HTTPError as error:
            limited = error.code == 429 or (
                error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
            )
            if limited:
                reset = error.headers.get("X-RateLimit-Reset")
                wait = max(60, int(reset) - time.time()) if reset else 60 * (attempt + 1)
                time.sleep(wait + 5)
                continue
            if error.code in (404, 409, 410, 451):
                return None, ""
            if error.code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            raise
    raise RuntimeError("github request failed after retries")


def pages(path, params=None, key=None):
    query = dict(params or {}, per_page=100)
    url = GITHUB + path + "?" + urllib.parse.urlencode(query)
    while url:
        body, link = github(url)
        if body is None:
            return
        yield from (body[key] if key else body)
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part[part.index("<") + 1 : part.index(">")]


def login(node):
    return (node or {}).get("login")


def window_of(stamp):
    return (stamp or "unknown")[:7]


def collect_issues():
    for issue in pages(f"/repos/{REPO}/issues", {"state": "all"}):
        if "pull_request" in issue:
            continue
        number = issue["number"]
        lines = [
            f"# Issue #{number}: {issue['title']}",
            f"url: {issue['html_url']}",
            f"state: {issue['state']}",
            f"created: {issue['created_at']}",
            f"updated: {issue['updated_at']}",
            f"author: {login(issue.get('user'))}",
            f"labels: {', '.join(label['name'] for label in issue.get('labels', []))}",
            "",
            issue.get("body") or "",
        ]
        if issue.get("comments"):
            for comment in pages(f"/repos/{REPO}/issues/{number}/comments"):
                lines.append(
                    f"\n## Comment by {login(comment.get('user'))} at {comment['created_at']}\n\n"
                    + (comment.get("body") or "")
                )
        yield window_of(issue["updated_at"]), "\n".join(lines)


def collect_pulls():
    for pr in pages(f"/repos/{REPO}/pulls", {"state": "all"}):
        number = pr["number"]
        lines = [
            f"# Pull request #{number}: {pr['title']}",
            f"url: {pr['html_url']}",
            f"state: {pr['state']}",
            f"merged: {pr.get('merged_at')}",
            f"created: {pr['created_at']}",
            f"updated: {pr['updated_at']}",
            f"author: {login(pr.get('user'))}",
            f"base: {pr['base']['ref']} head: {pr['head']['ref']}",
            "",
            pr.get("body") or "",
        ]
        if pr.get("comments"):
            for comment in pages(f"/repos/{REPO}/issues/{number}/comments"):
                lines.append(
                    f"\n## Comment by {login(comment.get('user'))} at {comment['created_at']}\n\n"
                    + (comment.get("body") or "")
                )
        for review in pages(f"/repos/{REPO}/pulls/{number}/reviews"):
            lines.append(
                f"\n## Review by {login(review.get('user'))} ({review.get('state')}) at "
                f"{review.get('submitted_at')}\n\n" + (review.get("body") or "")
            )
        if pr.get("review_comments"):
            for comment in pages(f"/repos/{REPO}/pulls/{number}/comments"):
                lines.append(
                    f"\n## Review comment by {login(comment.get('user'))} on {comment.get('path')} "
                    f"at {comment['created_at']}\n\n" + (comment.get("body") or "")
                )
        yield window_of(pr["updated_at"]), "\n".join(lines)


def collect_runs():
    for run in pages(f"/repos/{REPO}/actions/runs", key="workflow_runs"):
        yield window_of(run["created_at"]), (
            f"run {run['id']}: {run['name']} | event {run['event']} | status {run['status']} | "
            f"conclusion {run.get('conclusion')} | branch {run['head_branch']} | "
            f"sha {run['head_sha']} | created {run['created_at']} | url {run['html_url']}"
        )


def make_pack(kind, window, part, parts):
    body = ("\n\n---\n\n".join(parts) + "\n").encode()
    digest = hashlib.sha256(body).hexdigest()[:12]
    return f"gh-{kind}-{window}-{part:03d}-{digest}.md", body


def build_packs(kind, window, chunks):
    current, size, part = [], 0, 0
    for chunk in chunks:
        masked = mask(chunk)
        encoded = len(masked.encode()) + 16
        if current and size + encoded > PACK_LIMIT:
            yield make_pack(kind, window, part, current)
            current, size, part = [], 0, part + 1
        current.append(masked)
        size += encoded
    if current:
        yield make_pack(kind, window, part, current)


def app(method, path, body=None, headers=None):
    request = urllib.request.Request(APP + path, data=body, method=method, headers=headers or {})
    with OPENER.open(request, timeout=900) as response:
        raw = response.read()
        return json.loads(raw.decode()) if raw else {}


def upload(name, body):
    boundary = "logosrag" + hashlib.sha256(name.encode()).hexdigest()[:20]
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        "assistants\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return app("POST", "/api/files", head + body + tail, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def main():
    stored = {row["filename"]: row["id"] for row in app("GET", "/api/files").get("data", [])}
    attached = {row["id"] for row in app("GET", f"/api/vector-stores/{STORE_ID}/files").get("data", [])}
    totals = {"packs": 0, "uploaded": 0, "attached": 0, "failed": 0, "chunks": 0}
    for kind, collector in (("issue", collect_issues), ("pull", collect_pulls), ("run", collect_runs)):
        windows = {}
        for window, text in collector():
            windows.setdefault(window, []).append(text)
            totals["chunks"] += 1
        for window in sorted(windows):
            for name, body in build_packs(kind, window, windows[window]):
                totals["packs"] += 1
                try:
                    file_id = stored.get(name)
                    if file_id is None:
                        file_id = upload(name, body)["id"]
                        stored[name] = file_id
                        totals["uploaded"] += 1
                    if file_id not in attached:
                        app(
                            "POST",
                            f"/api/vector-stores/{STORE_ID}/files",
                            json.dumps({"file_id": file_id}).encode(),
                            {"Content-Type": "application/json"},
                        )
                        attached.add(file_id)
                        totals["attached"] += 1
                except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
                    totals["failed"] += 1
                    print(f"pack {name} failed: {type(error).__name__}", file=sys.stderr)
        print(f"{kind}: {sum(len(v) for v in windows.values())} items in {len(windows)} windows", flush=True)
    totals["redactions"] = REDACTIONS["count"]
    print(json.dumps(totals, sort_keys=True), flush=True)
    if totals["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
