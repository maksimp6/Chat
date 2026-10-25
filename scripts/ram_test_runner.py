#!/usr/bin/env python3
"""Run unittest modules in RAM and cache complete module passes on GitHub.

The runner writes no local cache, bytecode, or coverage files. Test bodies may
write files themselves. This is a unittest runner, not a pytest implementation.
A three-second deadline is a measured goal, never a reason to omit test work.

All tracked shared inputs and installed package versions enter the cache key.
Explicit imports between test modules are followed transitively; opaque loaders
and file readers depend on all test sources. Cached modules must be independent
and deterministic. Declare configuration with --cache-env; use --force for
external or mutable inputs. Remote persistence needs commit-status write access.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import fnmatch
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import unittest
import urllib.error
import urllib.request

CONTEXT = "logos/ram-tests-v2/"
ROOT = Path(__file__).resolve().parent.parent
API_ERRORS = (OSError, urllib.error.URLError, ValueError)
OPAQUE_CALLS = {
    "__import__", "eval", "exec", "import_module", "loadTestsFromName",
    "loadTestsFromNames", "open", "read_bytes", "read_text", "run_module",
    "run_path", "spec_from_file_location",
}


def github_job():
    keys = ("GITHUB_RUN_ID", "RUNNER_NAME", "RUNNER_TEMP", "GITHUB_REPOSITORY", "GITHUB_SHA")
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("CI") == "true"
        and all(os.environ.get(key) for key in keys)
    )


def git(*args):
    # Git's optional index refresh must not turn a read into a disk write.
    return subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args],
        capture_output=True, check=True,
    ).stdout


def git_paths():
    return sorted(
        part.decode("utf-8", "surrogateescape")
        for part in git("ls-files", "-z", "--cached").split(b"\0") if part
    )


def clean_checkout():
    return not git("status", "--porcelain", "-z", "--untracked-files=normal")


def raw_source(name):
    path = ROOT / name
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError("tracked path leaves the checkout: " + name)
    return path.read_bytes()


def source_bytes(name):
    path = ROOT / name
    if path.is_symlink():
        return b"symlink\0" + os.fsencode(os.readlink(path)) + b"\0" + raw_source(name)
    if not path.exists():
        return b"missing\0"
    return b"file\0" + raw_source(name)


def context_for(name):
    return CONTEXT + hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


def module_name(name):
    return name[:-3].replace("/", ".")


def import_dependencies(name, source, module_paths):
    """Resolve explicit imports; opaque loaders/readers invalidate all test modules."""
    tree = ast.parse(source, filename=name)
    module = module_name(name)
    package = module.rpartition(".")[0]
    dependencies = {name}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "load_tests":
            return set(module_paths.values())
        if isinstance(node, ast.Call):
            called = node.func
            called_name = called.id if isinstance(called, ast.Name) else (
                called.attr if isinstance(called, ast.Attribute) else ""
            )
            if called_name in OPAQUE_CALLS:
                return set(module_paths.values())
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = importlib.util.resolve_name("." * node.level + base, package)
            targets = [base, *(base + "." + alias.name for alias in node.names)]
            if any(alias.name == "*" for alias in node.names):
                targets.extend(key for key in module_paths if key.startswith(base + "."))
        for target in targets:
            for candidate, path in module_paths.items():
                if (
                    target == candidate or target.startswith(candidate + ".")
                    or candidate.startswith(target + ".")
                ):
                    dependencies.add(path)
    return dependencies


def fingerprints(paths, pattern, cache_env=()):
    modules = [
        name for name in paths
        if name.startswith("tests/") and name.endswith(".py")
        and (
            fnmatch.fnmatchcase(Path(name).name, "test_*.py")
            or fnmatch.fnmatchcase(Path(name).name, pattern)
        )
    ]
    sources = {name: raw_source(name) for name in modules}
    module_paths = {module_name(name): name for name in modules}
    graph = {
        name: import_dependencies(name, source, module_paths)
        for name, source in sources.items()
    }
    shared = hashlib.sha256()
    shared.update(json.dumps({
        "version": 2,
        "python": sys.version,
        "platform": sys.platform,
        "machine": platform.machine(),
        "runner_os": os.environ.get("RUNNER_OS", ""),
        "runner_arch": os.environ.get("RUNNER_ARCH", ""),
        "image": os.environ.get("ImageVersion", ""),
        "dependencies": sorted(
            (dist.metadata.get("Name", ""), dist.version)
            for dist in importlib.metadata.distributions()
        ),
        "environment": {
            key: os.environ.get(key)
            for key in sorted(set(cache_env) | {"PYTHONHASHSEED", "TZ", "LANG", "LC_ALL"})
        },
    }, sort_keys=True).encode())
    for name in paths:
        if name in sources:
            continue
        shared.update(name.encode("utf-8", "surrogateescape") + b"\0")
        content = source_bytes(name)
        shared.update(len(content).to_bytes(8, "big"))
        shared.update(content)
    result = {}
    for name in modules:
        if not fnmatch.fnmatchcase(Path(name).name, pattern):
            continue
        pending, dependencies = [name], set()
        while pending:
            dependency = pending.pop()
            if dependency in dependencies:
                continue
            dependencies.add(dependency)
            pending.extend(graph[dependency] - dependencies)
        digest = shared.copy()
        for dependency in sorted(dependencies):
            digest.update(dependency.encode("utf-8", "surrogateescape") + b"\0")
            path = ROOT / dependency
            if path.is_symlink():
                digest.update(b"symlink\0" + os.fsencode(os.readlink(path)) + b"\0")
            content = sources[dependency]
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        result[name] = digest.hexdigest()
    return result


def api(method, endpoint, token, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.github.com" + endpoint, data=body, method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "logos-ram-tests",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def commit_statuses(repo, sha, token):
    page, newest = 1, {}
    while True:
        items = api("GET", f"/repos/{repo}/commits/{sha}/statuses?per_page=100&page={page}", token)
        if not isinstance(items, list):
            raise ValueError("GitHub statuses response must be a list")
        for item in items:
            context = item.get("context", "")
            if context.startswith(CONTEXT):
                newest.setdefault(context, item)
        if len(items) < 100:
            return newest
        page += 1


def read_cache(repo, candidates, token):
    if not candidates:
        return {}
    # Fetch each commit once rather than one request per module per ancestor.
    with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
        snapshots = list(pool.map(lambda sha: commit_statuses(repo, sha, token), candidates))
    cache, seen = {}, set()
    for sha, snapshot in zip(candidates, snapshots):
        for context, item in snapshot.items():
            parts = (item.get("description") or "").split(":")
            if len(parts) < 2:
                continue
            digest = parts[1]
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                continue
            key = (context, digest)
            if key in seen:
                continue
            seen.add(key)
            if item.get("state") == "success" and parts[0] == "PASS" and len(parts) == 3:
                try:
                    count = int(parts[2])
                except ValueError:
                    continue
                if count > 0:
                    cache[key] = {"tests": count, "source_commit": sha}
    return cache


def save_status(repo, sha, token, name, digest, state, label, count=0):
    return api("POST", f"/repos/{repo}/statuses/{sha}", token, {
        "state": state,
        "context": context_for(name),
        "description": f"{label}:{digest}:{count}",
    })


def load_module(name):
    module = importlib.import_module(module_name(name))
    if getattr(module, "pytestmark", None):
        raise ValueError(name + ": pytest module marks require pytest")
    if not callable(getattr(module, "load_tests", None)):
        # unittest does not collect pytest functions/classes. Never cache a
        # partial module while presenting it as a complete successful module.
        for key, value in vars(module).items():
            if (
                isinstance(value, type)
                and issubclass(value, unittest.TestCase)
                and (
                    getattr(value, "pytestmark", None)
                    or any(
                        getattr(member, "pytestmark", None)
                        for member in vars(value).values()
                    )
                )
            ):
                raise ValueError(f"{name}: pytest fixture/mark integration requires pytest: {key}")
            if key.startswith("test") and callable(value) and not isinstance(value, type):
                raise ValueError(f"{name}: function tests require pytest: {key}")
            if (
                isinstance(value, type)
                and value.__module__ == module.__name__
                and not issubclass(value, unittest.TestCase)
                and any(attr.startswith("test") and callable(getattr(value, attr)) for attr in vars(value))
            ):
                raise ValueError(f"{name}: non-unittest test class requires pytest: {key}")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(module)
    if loader.errors:
        raise ValueError("\n".join(loader.errors))
    if suite.countTestCases() == 0:
        raise ValueError(name + ": no unittest cases collected")
    return suite


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="run every selected module")
    parser.add_argument("--pattern", default="test_*.py", help="test module filename pattern")
    parser.add_argument("--lookback", type=int, default=12, help="commits to search for cached results")
    parser.add_argument("--no-cache", action="store_true", help="run without reading or writing remote cache")
    parser.add_argument("--cache-env", action="append", default=[], metavar="NAME",
                        help="include this environment variable in every cache key; repeatable")
    parser.add_argument("--deadline", type=float, default=3.0,
                        help="performance goal in seconds; never abort or misreport tests")
    args = parser.parse_args(argv)
    start = time.perf_counter()
    if not github_job():
        print("LOCAL_TESTS_FORBIDDEN: run inside a GitHub Actions job", file=sys.stderr)
        return 2
    if args.lookback < 1 or args.deadline <= 0:
        parser.error("--lookback and --deadline must be positive")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, str(ROOT))
    token = os.environ.get("GITHUB_TOKEN", "")
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    use_cache = not args.no_cache and bool(token)
    cache = {}
    try:
        paths = git_paths()
        plan = fingerprints(paths, args.pattern, args.cache_env)
        if not plan:
            raise ValueError("EMPTY_SUITE: no selected tracked test modules")
        checkout_sha = git("rev-parse", "HEAD").decode("ascii").strip()
        if use_cache and checkout_sha != sha:
            use_cache = False
            print("CACHE_DISABLED: GITHUB_SHA differs from checkout HEAD", file=sys.stderr)
        if use_cache and not clean_checkout():
            use_cache = False
            print("CACHE_DISABLED: checkout has local changes", file=sys.stderr)
        if use_cache and not args.force:
            commits = git("rev-list", "-n", str(args.lookback), "HEAD").decode("ascii").splitlines()
            try:
                cache = read_cache(repo, list(dict.fromkeys([sha] + commits)), token)
            except API_ERRORS as exc:
                print("CACHE_READ_ERROR: running selected modules: " + str(exc), file=sys.stderr)
    except (OSError, ValueError, SyntaxError, ImportError, subprocess.CalledProcessError) as exc:
        print("COLLECTION_ERROR: " + str(exc), file=sys.stderr)
        return 2

    cached = {}
    pending = {}
    for name, digest in plan.items():
        entry = cache.get((context_for(name), digest))
        if entry is None:
            pending[name] = digest
        else:
            cached[name] = entry
    if not token and not args.no_cache:
        print("CACHE_UNAVAILABLE: no GITHUB_TOKEN; results will not be persisted", file=sys.stderr)

    outcomes = []
    collection_errors = []
    cache_errors = []
    for name, digest in pending.items():
        if use_cache:
            try:
                save_status(repo, sha, token, name, digest, "pending", "PENDING")
            except API_ERRORS as exc:
                cache_errors.append({"module": name, "error": str(exc)})
        try:
            suite = load_module(name)
        except (Exception, SystemExit) as exc:
            collection_errors.append({"module": name, "error": repr(exc)})
            outcomes.append((name, digest, "error", "ERROR", 0))
            continue
        result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
        incomplete = bool(result.skipped or result.expectedFailures)
        complete_pass = result.wasSuccessful() and not incomplete
        label = "PASS" if complete_pass else ("INCOMPLETE" if result.wasSuccessful() else "FAIL")
        outcomes.append((
            name, digest, "success" if complete_pass else "failure", label, result.testsRun,
        ))

    inputs_changed = False
    if use_cache:
        try:
            stable = (
                clean_checkout()
                and git_paths() == paths
                and fingerprints(paths, args.pattern, args.cache_env) == plan
            )
        except (OSError, ValueError, SyntaxError, ImportError, subprocess.CalledProcessError):
            stable = False
        inputs_changed = not stable
        for name, digest, state, label, count in outcomes:
            if not stable and state == "success":
                state, label = "error", "INPUTS_CHANGED"
            try:
                save_status(repo, sha, token, name, digest, state, label, count)
            except API_ERRORS as exc:
                cache_errors.append({"module": name, "error": str(exc)})

    elapsed = time.perf_counter() - start
    failed = any(state != "success" for _, _, state, _, _ in outcomes)
    status = (
        "ERROR" if collection_errors or inputs_changed else "FAIL" if failed else
        "CACHED_PASS" if not outcomes else "PASS"
    )
    print(json.dumps({
        "status": status,
        "executed_tests": sum(count for _, _, _, _, count in outcomes),
        "cached_tests": sum(entry["tests"] for entry in cached.values()),
        "executed_modules": list(pending),
        "cached_modules": cached,
        "collection_errors": collection_errors,
        "cache_errors": cache_errors,
        "cache_enabled": use_cache,
        "inputs_changed": inputs_changed,
        "elapsed_sec": round(elapsed, 3),
        "goal_met": elapsed <= args.deadline,
    }))
    return 2 if collection_errors or inputs_changed else 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
