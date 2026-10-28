"""Ingest the whole GitHub repository into the chat RAG store through the app API.

What is ingested:
- git: the source tree at GITHUB_SHA, every ref, and the full history with patches of
  every branch and pull-request ref; the wiki repository the same way when it exists;
- issues and pull requests with comments, reviews and review comments;
- discussions with comments and replies, and commit comments;
- every Actions run with its jobs, steps, job logs and artifacts.

Runs on the Redmi 9 runner, piped from the repository into python3. One step on that
runner may run at most 15 minutes, so the work is done in slices: before BUDGET_SECONDS
run out the script stops and dispatches the same workflow with a cursor that says where
to continue. Packs are named by window and content hash, so packs that are already in
the store are not uploaded or attached again. Git data is cloned into the RAM disk
(MIRROR_DIR), never to flash; everything else is built in memory. Packs go to the running
app's own API on 127.0.0.1, so no provider key is needed here. Known secret shapes are
masked and e-mail addresses are replaced by hashes. Nothing is dropped.
"""

import base64
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = os.environ["GITHUB_REPOSITORY"]
SHA = os.environ["GITHUB_SHA"]
REF = os.environ.get("GITHUB_REF", "refs/heads/develop").removeprefix("refs/heads/")
GH_TOKEN = os.environ["GH_TOKEN"]
STORE_ID = os.environ["STORE_ID"]
WORKFLOW_FILE = os.environ["WORKFLOW_FILE"]
MIRROR_DIR = os.environ["MIRROR_DIR"]
BUDGET_SECONDS = float(os.environ.get("BUDGET_SECONDS", "780"))
APP = "http://127.0.0.1:8080"
GITHUB = "https://api.github.com"
PACK_LIMIT = 4 * 1024 * 1024
STAGES = ("git", "issues", "pulls", "discussions", "commit_comments", "runs")
STARTED = time.monotonic()

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
TOTALS = {"packs": 0, "uploaded": 0, "attached": 0, "failed": 0, "chunks": 0}


class OutOfTime(Exception):
    """The slice budget is spent; the next slice continues from the cursor."""


class DropAuthorizationOnRedirect(urllib.request.HTTPRedirectHandler):
    """Log and artifact downloads redirect to blob storage, which must not get the token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if (
            new is not None
            and urllib.parse.urlsplit(newurl).netloc
            != urllib.parse.urlsplit(req.full_url).netloc
        ):
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), DropAuthorizationOnRedirect()
)


def remaining():
    return BUDGET_SECONDS - (time.monotonic() - STARTED)


def check_time():
    if remaining() <= 0:
        raise OutOfTime()


def mask(text):
    def secret(match):
        value = match.group("value")
        if value.casefold().startswith(
            ("${{", "${", "$", "your_", "changeme", "placeholder", "[redacted]")
        ):
            return match.group(0)
        REDACTIONS["count"] += 1
        return (
            match.group(1) + match.group("quote") + "[REDACTED]" + match.group("quote")
        )

    def auth(match):
        REDACTIONS["count"] += 1
        return match.group(1) + "[REDACTED]"

    def shape(_match):
        REDACTIONS["count"] += 1
        return "[REDACTED]"

    def email(match):
        REDACTIONS["count"] += 1
        return (
            "[email:"
            + hashlib.sha256(match.group().casefold().encode()).hexdigest()[:16]
            + "]"
        )

    text = JSON_SECRET.sub(secret, text)
    text = AUTH_SECRET.sub(auth, text)
    text = TOKEN_SHAPE.sub(shape, text)
    return EMAIL.sub(email, text)


# --- GitHub API ------------------------------------------------------------------------


def github_request(url, data=None, method=None, accept="application/vnd.github+json"):
    """Return (status, body bytes, headers); None body for gone resources."""
    for attempt in range(8):
        check_time()
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + GH_TOKEN,
                "Accept": accept,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "logos-rag-ingest",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with OPENER.open(request, timeout=120) as response:
                left = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset")
                body = response.read()
                if left is not None and int(left) < 10 and reset:
                    pause(max(0, int(reset) - time.time()) + 5)
                return response.status, body, response.headers
        except urllib.error.HTTPError as error:
            limited = error.code == 429 or (
                error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
            )
            if limited:
                reset = error.headers.get("X-RateLimit-Reset")
                pause(
                    (max(60, int(reset) - time.time()) if reset else 60 * (attempt + 1))
                    + 5
                )
                continue
            if error.code in (404, 409, 410, 451):
                return error.code, None, error.headers
            if error.code >= 500:
                pause(10 * (attempt + 1))
                continue
            raise
    raise RuntimeError("github request failed after retries")


def pause(seconds):
    if seconds >= remaining():
        raise OutOfTime()
    time.sleep(seconds)


def github(path, params=None):
    url = path if path.startswith("https://") else GITHUB + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    _status, body, _headers = github_request(url)
    return json.loads(body.decode()) if body is not None else None


def github_raw(path):
    status, body, _headers = github_request(GITHUB + path, accept="*/*")
    return status, body


def graphql(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    _status, body, _headers = github_request(
        GITHUB + "/graphql", data=payload, method="POST"
    )
    result = json.loads(body.decode()) if body is not None else {}
    if result.get("errors"):
        raise RuntimeError(
            "graphql: " + "; ".join(e.get("message", "") for e in result["errors"])
        )
    return result.get("data") or {}


def all_pages(path, params=None, key=None):
    items, page = [], 1
    while True:
        body = github(path, dict(params or {}, per_page=100, page=page))
        rows = (body or {}).get(key, []) if key else (body or [])
        items.extend(rows)
        if len(rows) < 100:
            return items
        page += 1


def login(node):
    return (node or {}).get("login")


# --- App API ---------------------------------------------------------------------------


def app(method, path, body=None, headers=None):
    request = urllib.request.Request(
        APP + path, data=body, method=method, headers=headers or {}
    )
    # Never wait past the step limit: the slice must still dispatch its continuation.
    with OPENER.open(request, timeout=max(30, remaining() + 60)) as response:
        raw = response.read()
        return json.loads(raw.decode()) if raw else {}


def listing(path):
    """Every row of a paged app listing (``after`` = last id of the previous page)."""
    rows, after = [], None
    while True:
        query = {"limit": 100, **({"after": after} if after else {})}
        page = app("GET", path + "?" + urllib.parse.urlencode(query))
        data = page.get("data", [])
        rows.extend(data)
        more = page.get("has_more")
        if not data or more is False or (more is None and len(data) < 100):
            return rows
        if data[-1].get("id") == after:
            return rows
        after = data[-1].get("id")


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
    return app(
        "POST",
        "/api/files",
        head + body + tail,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


class Store:
    def __init__(self):
        self.stored = {
            row.get("filename"): row["id"]
            for row in listing("/api/files")
            if "id" in row
        }
        self.attached = {
            row["id"]
            for row in listing(f"/api/vector-stores/{STORE_ID}/files")
            if "id" in row
        }

    def put(self, kind, window, chunks):
        for name, body in build_packs(kind, window, chunks):
            check_time()
            TOTALS["packs"] += 1
            try:
                file_id = self.stored.get(name)
                if file_id is None:
                    file_id = upload(name, body)["id"]
                    self.stored[name] = file_id
                    TOTALS["uploaded"] += 1
                if file_id not in self.attached:
                    app(
                        "POST",
                        f"/api/vector-stores/{STORE_ID}/files",
                        json.dumps({"file_id": file_id}).encode(),
                        {"Content-Type": "application/json"},
                    )
                    self.attached.add(file_id)
                    TOTALS["attached"] += 1
            except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
                TOTALS["failed"] += 1
                print(
                    f"pack {name} failed: {type(error).__name__}",
                    file=sys.stderr,
                    flush=True,
                )


# --- Packs -----------------------------------------------------------------------------


def split_chunk(text):
    """Split a chunk larger than one pack into parts that each fit; nothing is dropped."""
    limit = PACK_LIMIT - 4096
    if len(text.encode()) <= limit:
        return [text]
    title = text.split("\n", 1)[0][:200]
    parts, current, size = [], [], 0
    for line in text.splitlines(keepends=True):
        encoded = len(line.encode())
        while encoded > limit:  # a single enormous line
            cut = line[: limit // 4]
            if current:
                parts.append("".join(current))
                current, size = [], 0
            parts.append(cut)
            line = line[len(cut) :]
            encoded = len(line.encode())
        if current and size + encoded > limit:
            parts.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += encoded
    if current:
        parts.append("".join(current))
    return [
        f"{title} (part {i + 1} of {len(parts)})\n\n{part}"
        for i, part in enumerate(parts)
    ]


def make_pack(kind, window, part, parts):
    body = ("\n\n---\n\n".join(parts) + "\n").encode()
    digest = hashlib.sha256(body).hexdigest()[:12]
    return f"gh-{kind}-{window}-{part:03d}-{digest}.md", body


def build_packs(kind, window, chunks):
    current, size, part = [], 0, 0
    for chunk in chunks:
        for piece in split_chunk(mask(chunk)):
            TOTALS["chunks"] += 1
            encoded = len(piece.encode()) + 16
            if current and size + encoded > PACK_LIMIT:
                yield make_pack(kind, window, part, current)
                current, size, part = [], 0, part + 1
            current.append(piece)
            size += encoded
    if current:
        yield make_pack(kind, window, part, current)


# --- git -------------------------------------------------------------------------------


def git(directory, *args):
    return subprocess.run(
        ["git", "-C", directory, *args], capture_output=True, check=True
    ).stdout


def mirror(url, directory):
    """Clone (once per slice) every ref of a repository into the RAM disk."""
    if os.path.isfile(os.path.join(directory, "HEAD")):
        return True
    basic = base64.b64encode(f"x-access-token:{GH_TOKEN}".encode()).decode()
    result = subprocess.run(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}",
            "clone",
            "--mirror",
            "--quiet",
            url,
            directory,
        ],
        capture_output=True,
        check=False,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1"),
        timeout=max(30, remaining()),
    )
    return result.returncode == 0


def text_or_none(data):
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def collect_tree(directory, revision, label):
    """One chunk per file of ``revision``, windowed by top-level directory."""
    windows = {}
    listing_ = git(directory, "ls-tree", "-r", "-l", "-z", revision).split(b"\0")
    entries = []
    for line in listing_:
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        mode, kind, oid, size = meta.split()
        entries.append(
            (
                path.decode("utf-8", "replace"),
                kind.decode(),
                oid.decode(),
                size.decode(),
                mode.decode(),
            )
        )
    batch = subprocess.Popen(
        ["git", "-C", directory, "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        for path, kind, oid, size, mode in entries:
            window = path.split("/", 1)[0] if "/" in path else "_root"
            header = f"# File {path}\n{label} revision: {revision}\nblob: {oid}\nmode: {mode}\nsize: {size}\n"
            if kind != "blob":
                windows.setdefault(window, []).append(header + f"\n({kind} entry)")
                continue
            batch.stdin.write(oid.encode() + b"\n")
            batch.stdin.flush()
            info = batch.stdout.readline().split()
            data = b""
            if len(info) == 3:  # "<oid> missing" has no content and no trailing newline
                data = batch.stdout.read(int(info[2]))
                batch.stdout.read(1)
            text = text_or_none(data)
            if text is None:
                digest = hashlib.sha256(data).hexdigest()
                windows.setdefault(window, []).append(
                    header + f"\nbinary content, sha256 {digest}"
                )
            else:
                windows.setdefault(window, []).append(
                    header + "\n```\n" + text + "\n```"
                )
    finally:
        batch.stdin.close()
        batch.wait()
    return windows


def collect_refs(directory):
    refs = git(
        directory,
        "for-each-ref",
        "--format=%(refname) %(objectname) %(creatordate:iso-strict) %(subject)",
    ).decode("utf-8", "replace")
    return "# Refs\n\n" + refs


def history(directory, store, kind):
    """Every commit reachable from any ref, oldest first, with its full patch."""
    marker = "\x1e"
    process = subprocess.Popen(
        [
            "git",
            "-C",
            directory,
            "log",
            "--all",
            "--date-order",
            "--reverse",
            "--no-color",
            "--no-ext-diff",
            "--find-renames",
            "--stat",
            "--patch",
            (
                f"--format={marker}# Commit %H%nparents: %P%nauthor: %an <%ae>%nauthor-date: %aI%n"
                "committer: %cn <%ce>%ncommit-date: %cI%nrefs: %D%n%n%B"
            ),
        ],
        stdout=subprocess.PIPE,
    )
    window, chunks, commit = None, [], []
    parts_seen = {}

    def flush():
        if chunks:
            # The same month can come back after a later one; give it a fresh part series.
            label = (
                window
                if window not in parts_seen
                else f"{window}-r{parts_seen[window]}"
            )
            parts_seen[window] = parts_seen.get(window, 0) + 1
            store.put(kind, label, list(chunks))
            chunks.clear()

    def finish(lines):
        nonlocal window
        text = "".join(lines)
        month = re.search(r"^commit-date: (\d{4}-\d{2})", text, re.MULTILINE)
        month = month.group(1) if month else "unknown"
        if window is not None and month != window:
            flush()
        window = month
        chunks.append(text)

    try:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace")
            if line.startswith(marker):
                if commit:
                    finish(commit)
                commit = [line[1:]]
            else:
                commit.append(line)
        if commit:
            finish(commit)
        flush()
    finally:
        process.stdout.close()
        process.wait()


def stage_git(store, cursor):
    repo_dir = os.path.join(MIRROR_DIR, "repo.git")
    if not mirror(f"https://github.com/{REPO}.git", repo_dir):
        raise RuntimeError("cannot mirror the repository")
    # Later slices run on a newer develop; the source tree stays the one the chain began with.
    revision = cursor.setdefault("sha", SHA)
    for window, chunks in sorted(
        collect_tree(repo_dir, revision, "repository").items()
    ):
        store.put("source", window, chunks)
    store.put("refs", "all", [collect_refs(repo_dir)])
    history(repo_dir, store, "commit")
    wiki_dir = os.path.join(MIRROR_DIR, "wiki.git")
    if mirror(f"https://github.com/{REPO}.wiki.git", wiki_dir):
        head = git(wiki_dir, "rev-parse", "HEAD").decode().strip()
        for window, chunks in sorted(collect_tree(wiki_dir, head, "wiki").items()):
            store.put("wiki", window, chunks)
        history(wiki_dir, store, "wiki-commit")
    else:
        print("wiki: no wiki repository", flush=True)


# --- Issues, pull requests, discussions, commit comments --------------------------------


def comments_of(number):
    lines = []
    for comment in all_pages(f"/repos/{REPO}/issues/{number}/comments"):
        lines.append(
            f"\n## Comment by {login(comment.get('user'))} at {comment['created_at']}\n\n"
            + (comment.get("body") or "")
        )
    return lines


def issue_text(issue):
    number = issue["number"]
    lines = [
        f"# Issue #{number}: {issue['title']}",
        f"url: {issue['html_url']}",
        f"state: {issue['state']} ({issue.get('state_reason')})",
        f"created: {issue['created_at']}",
        f"updated: {issue['updated_at']}",
        f"closed: {issue.get('closed_at')}",
        f"author: {login(issue.get('user'))}",
        f"assignees: {', '.join(login(a) or '' for a in issue.get('assignees') or [])}",
        f"labels: {', '.join(label['name'] for label in issue.get('labels', []))}",
        f"milestone: {(issue.get('milestone') or {}).get('title')}",
        "",
        issue.get("body") or "",
    ]
    if issue.get("comments"):
        lines += comments_of(number)
    return "\n".join(lines)


def pull_text(pr):
    number = pr["number"]
    lines = [
        f"# Pull request #{number}: {pr['title']}",
        f"url: {pr['html_url']}",
        f"state: {pr['state']}",
        f"draft: {pr.get('draft')}",
        f"merged: {pr.get('merged_at')}",
        f"created: {pr['created_at']}",
        f"updated: {pr['updated_at']}",
        f"closed: {pr.get('closed_at')}",
        f"author: {login(pr.get('user'))}",
        f"base: {pr['base']['ref']} ({pr['base']['sha']}) head: {pr['head']['ref']} ({pr['head']['sha']})",
        f"labels: {', '.join(label['name'] for label in pr.get('labels', []))}",
        "",
        pr.get("body") or "",
    ]
    lines += comments_of(number)
    for review in all_pages(f"/repos/{REPO}/pulls/{number}/reviews"):
        lines.append(
            f"\n## Review by {login(review.get('user'))} ({review.get('state')}) at "
            f"{review.get('submitted_at')} on {review.get('commit_id')}\n\n"
            + (review.get("body") or "")
        )
    for comment in all_pages(f"/repos/{REPO}/pulls/{number}/comments"):
        lines.append(
            f"\n## Review comment by {login(comment.get('user'))} on {comment.get('path')}:"
            f"{comment.get('line') or comment.get('original_line')} at {comment['created_at']}\n\n"
            f"```diff\n{comment.get('diff_hunk') or ''}\n```\n\n"
            + (comment.get("body") or "")
        )
    return "\n".join(lines)


def paged_stage(store, cursor, kind, path, params, render, keep=lambda item: True):
    page = cursor.get("page", 1)
    while True:
        check_time()
        rows = github(path, dict(params, per_page=100, page=page)) or []
        chunks = [render(item) for item in rows if keep(item)]
        if chunks:
            store.put(kind, f"p{page:04d}", chunks)
        if len(rows) < 100:
            return
        page += 1
        cursor["page"] = page


def stage_issues(store, cursor):
    return paged_stage(
        store,
        cursor,
        "issue",
        f"/repos/{REPO}/issues",
        {"state": "all", "sort": "created", "direction": "asc"},
        issue_text,
        keep=lambda issue: "pull_request" not in issue,
    )


def stage_pulls(store, cursor):
    return paged_stage(
        store,
        cursor,
        "pull",
        f"/repos/{REPO}/pulls",
        {"state": "all", "sort": "created", "direction": "asc"},
        pull_text,
    )


def stage_commit_comments(store, cursor):
    return paged_stage(
        store,
        cursor,
        "commit-comment",
        f"/repos/{REPO}/comments",
        {},
        lambda c: (
            f"# Commit comment by {login(c.get('user'))} on {c.get('commit_id')} "
            f"{c.get('path') or ''}:{c.get('line') or ''} at {c.get('created_at')}\n"
            f"url: {c.get('html_url')}\n\n{c.get('body') or ''}"
        ),
    )


DISCUSSIONS = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 25, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title url createdAt updatedAt closed author { login } category { name } body
        answer { id }
        comments(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { id author { login } createdAt body replies(first: 100) { nodes { author { login } createdAt body } } }
        }
      }
    }
  }
}"""

DISCUSSION_COMMENTS = """
query($id: ID!, $after: String) {
  node(id: $id) {
    ... on Discussion {
      comments(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id author { login } createdAt body replies(first: 100) { nodes { author { login } createdAt body } } }
      }
    }
  }
}"""


def discussion_text(node):
    lines = [
        f"# Discussion #{node['number']}: {node['title']}",
        f"url: {node['url']}",
        f"category: {(node.get('category') or {}).get('name')}",
        f"closed: {node.get('closed')}",
        f"created: {node['createdAt']}",
        f"updated: {node['updatedAt']}",
        f"author: {login(node.get('author'))}",
        "",
        node.get("body") or "",
    ]
    comments = node["comments"]
    answer = (node.get("answer") or {}).get("id")
    while True:
        for comment in comments["nodes"]:
            marker = " (answer)" if comment.get("id") == answer else ""
            lines.append(
                f"\n## Comment by {login(comment.get('author'))} at {comment['createdAt']}{marker}\n\n"
                + (comment.get("body") or "")
            )
            for reply in (comment.get("replies") or {}).get("nodes", []):
                lines.append(
                    f"\n### Reply by {login(reply.get('author'))} at {reply['createdAt']}\n\n"
                    + (reply.get("body") or "")
                )
        if not comments["pageInfo"]["hasNextPage"]:
            return "\n".join(lines)
        comments = graphql(
            DISCUSSION_COMMENTS,
            {"id": node["id"], "after": comments["pageInfo"]["endCursor"]},
        )["node"]["comments"]


def stage_discussions(store, cursor):
    owner, name = REPO.split("/", 1)
    page = cursor.get("page", 1)
    after = cursor.get("after")
    while True:
        check_time()
        data = graphql(DISCUSSIONS, {"owner": owner, "name": name, "after": after})
        block = (data.get("repository") or {}).get("discussions") or {}
        chunks = [discussion_text(node) for node in block.get("nodes", [])]
        if chunks:
            store.put("discussion", f"p{page:04d}", chunks)
        info = block.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return
        page, after = page + 1, info["endCursor"]
        cursor.update(page=page, after=after)


# --- Actions runs, jobs, logs and artifacts --------------------------------------------


def artifact_text(artifact):
    lines = [
        f"\n### Artifact {artifact['name']} ({artifact['id']})",
        (
            f"size: {artifact.get('size_in_bytes')} bytes, expired: {artifact.get('expired')}, "
            f"created: {artifact.get('created_at')}, expires: {artifact.get('expires_at')}"
        ),
    ]
    if artifact.get("expired"):
        return lines
    status, data = github_raw(f"/repos/{REPO}/actions/artifacts/{artifact['id']}/zip")
    if data is None:
        lines.append(f"download: unavailable (HTTP {status})")
        return lines
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            content = archive.read(member)
            text = text_or_none(content)
            if text is None:
                lines.append(
                    f"\n#### {member.filename}\nbinary, {member.file_size} bytes, sha256 "
                    + hashlib.sha256(content).hexdigest()
                )
            else:
                lines.append(f"\n#### {member.filename}\n\n```\n{text}\n```")
    return lines


def run_text(run):
    lines = [
        f"# Actions run {run['id']}: {run.get('name')} #{run.get('run_number')} attempt {run.get('run_attempt')}",
        f"url: {run.get('html_url')}",
        f"title: {run.get('display_title')}",
        f"workflow: {run.get('path')}",
        f"event: {run.get('event')} | status: {run.get('status')} | conclusion: {run.get('conclusion')}",
        f"branch: {run.get('head_branch')} | sha: {run.get('head_sha')}",
        f"actor: {login(run.get('actor'))} | triggered by: {login(run.get('triggering_actor'))}",
        f"created: {run.get('created_at')} | started: {run.get('run_started_at')} | updated: {run.get('updated_at')}",
        f"commit message: {(run.get('head_commit') or {}).get('message')}",
    ]
    jobs = all_pages(
        f"/repos/{REPO}/actions/runs/{run['id']}/jobs", {"filter": "all"}, key="jobs"
    )
    for job in jobs:
        lines += [
            f"\n## Job {job.get('name')} ({job['id']}) attempt {job.get('run_attempt')}",
            f"status: {job.get('status')} | conclusion: {job.get('conclusion')} | runner: {job.get('runner_name')}",
            f"started: {job.get('started_at')} | completed: {job.get('completed_at')}",
        ]
        for step in job.get("steps") or []:
            lines.append(
                f"- step {step.get('number')} {step.get('name')}: {step.get('status')}/{step.get('conclusion')} "
                f"{step.get('started_at')} .. {step.get('completed_at')}"
            )
        status, log = github_raw(f"/repos/{REPO}/actions/jobs/{job['id']}/logs")
        if log is None:
            lines.append(f"\nlog: unavailable (HTTP {status})")
        else:
            lines.append("\nlog:\n```\n" + log.decode("utf-8", "replace") + "\n```")
    for artifact in all_pages(
        f"/repos/{REPO}/actions/runs/{run['id']}/artifacts", key="artifacts"
    ):
        lines += artifact_text(artifact)
    return "\n".join(lines)


def stage_runs(store, cursor):
    """Hour by hour from the repository's creation up to the current hour (UTC).

    A listing returns at most 1000 runs per query, so windows are one hour long.
    """
    now = datetime.datetime.now(datetime.UTC).replace(minute=0, second=0, microsecond=0)
    if "hour" not in cursor:
        created = github(f"/repos/{REPO}")["created_at"][:13]
        cursor.update(hour=created, page=1)
    per_page = 25
    while True:
        hour = datetime.datetime.fromisoformat(cursor["hour"] + ":00:00+00:00")
        if hour >= now:  # runs of the current hour are still being created
            return
        check_time()
        page = cursor.get("page", 1)
        span = f"{cursor['hour']}:00:00Z..{cursor['hour']}:59:59Z"
        body = (
            github(
                f"/repos/{REPO}/actions/runs",
                {"created": span, "per_page": per_page, "page": page},
            )
            or {}
        )
        runs = body.get("workflow_runs", [])
        chunks = [run_text(run) for run in runs]
        if chunks:
            store.put("run", f"{cursor['hour']}-p{page:03d}", chunks)
        if len(runs) < per_page:
            following = hour + datetime.timedelta(hours=1)
            cursor.update(hour=following.strftime("%Y-%m-%dT%H"), page=1)
        else:
            cursor["page"] = page + 1


# --- Slices ----------------------------------------------------------------------------


HANDLERS = {
    "git": stage_git,
    "issues": stage_issues,
    "pulls": stage_pulls,
    "discussions": stage_discussions,
    "commit_comments": stage_commit_comments,
    "runs": stage_runs,
}


def dispatch_next(cursor):
    payload = json.dumps(
        {"ref": REF, "inputs": {"cursor": json.dumps(cursor, separators=(",", ":"))}}
    ).encode()
    request = urllib.request.Request(
        f"{GITHUB}/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + GH_TOKEN,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "logos-rag-ingest",
            "Content-Type": "application/json",
        },
    )
    with OPENER.open(request, timeout=60):
        pass


def main():
    raw = os.environ.get("CURSOR", "").strip()
    cursor = json.loads(raw) if raw else {"stage": STAGES[0]}
    store = Store()
    try:
        while cursor["stage"] in HANDLERS:
            stage = cursor["stage"]
            HANDLERS[stage](store, cursor)
            print(f"{stage}: done", flush=True)
            following = STAGES.index(stage) + 1
            cursor = {"stage": STAGES[following] if following < len(STAGES) else "done"}
    except OutOfTime:
        dispatch_next(cursor)
        print(f"slice ended; continuing from {json.dumps(cursor)}", flush=True)
    else:
        hour = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H")
        print(
            "whole repository ingested; to add later runs only, dispatch with cursor "
            + json.dumps({"stage": "runs", "hour": hour, "page": 1}),
            flush=True,
        )
    TOTALS["redactions"] = REDACTIONS["count"]
    print(json.dumps(TOTALS, sort_keys=True), flush=True)
    if TOTALS["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
