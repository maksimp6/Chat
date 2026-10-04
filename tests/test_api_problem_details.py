"""RFC 9457 migration ratchet (dependency-free); see docs/api/errors.md."""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PREFIXES = ("tests/", "node_modules/", "android/", "scripts/", "deploy/")
RESPONSE_BUILDERS = {"jsonify", "problem"}

# Legacy {"error": ...} bodies still to convert to problem().
LEGACY_ERROR_BODY_BASELINE = 200
# Responses built inside `except ... as exc` that echo the exception to the client.
EXCEPTION_ECHO_BASELINE = 75


def _python_sources():
    listed = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    for path in listed:
        if not path.startswith(EXCLUDED_PREFIXES):
            yield path, ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)


def _call_name(node):
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _is_legacy_error_body(node):
    if not (isinstance(node, ast.Call) and _call_name(node) == "jsonify" and node.args):
        return False
    body = node.args[0]
    return isinstance(body, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == "error" for key in body.keys
    )


def _echoes_exception(handler):
    if not handler.name:
        return 0
    count = 0
    for node in ast.walk(handler):
        if isinstance(node, ast.Call) and _call_name(node) in RESPONSE_BUILDERS:
            names = {
                inner.id
                for argument in [*node.args, *(k.value for k in node.keywords)]
                for inner in ast.walk(argument)
                if isinstance(inner, ast.Name)
            }
            count += handler.name in names
    return count


def _counts():
    legacy = echoes = 0
    for _path, tree in _python_sources():
        for node in ast.walk(tree):
            legacy += _is_legacy_error_body(node)
            if isinstance(node, ast.ExceptHandler):
                echoes += _echoes_exception(node)
    return legacy, echoes


def test_error_responses_only_move_towards_problem_details():
    legacy, echoes = _counts()
    assert legacy <= LEGACY_ERROR_BODY_BASELINE, (
        f"new legacy jsonify({{'error': ...}}) bodies ({legacy} > "
        f"{LEGACY_ERROR_BODY_BASELINE}); use invocation.problems.problem()"
    )
    assert echoes <= EXCEPTION_ECHO_BASELINE, (
        f"responses echo exceptions to clients ({echoes} > {EXCEPTION_ECHO_BASELINE}); "
        "log the exception and return fixed problem() text"
    )
    assert (legacy, echoes) == (LEGACY_ERROR_BODY_BASELINE, EXCEPTION_ECHO_BASELINE), (
        f"lower the baselines to lock in the fixes: legacy={legacy}, echoes={echoes}"
    )


def test_detector_flags_known_patterns():
    tree = ast.parse(
        "try:\n    pass\nexcept ValueError as exc:\n"
        "    jsonify({'error': str(exc)})\n    problem(400, 'bad', 'Bad', detail=f'{exc}')\n"
    )
    handler = next(node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler))
    assert _echoes_exception(handler) == 2
    assert sum(_is_legacy_error_body(node) for node in ast.walk(tree)) == 1
    unnamed = ast.parse("try:\n    pass\nexcept ValueError:\n    jsonify({'ok': 1})\n")
    handler = next(node for node in ast.walk(unnamed) if isinstance(node, ast.ExceptHandler))
    assert _echoes_exception(handler) == 0
