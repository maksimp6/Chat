import ast
from pathlib import Path

from api_contracts import CONTRACTS


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_API = ROOT / "runtime_api.py"


def _runtime_route_functions():
    tree = ast.parse(RUNTIME_API.read_text(encoding="utf-8"))
    routes = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        methods = []
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "runtime_bp"
                and func.attr in {"get", "post", "put", "patch", "delete"}
            ):
                methods.append(func.attr.upper())
        if methods:
            routes.append((node, methods))
    return routes


def _calls(node):
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)]


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return None


def _literal_contract_names(node):
    names = []
    for call in _calls(node):
        call_name = _call_name(call)
        if call_name not in {"_contract_response", "_request_payload"}:
            continue
        assert call.args, f"{node.name}: {call_name} must receive a named contract"
        first = call.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            f"{node.name}: {call_name} contract name must be a string literal"
        )
        names.append(first.value)
    return names


def test_every_runtime_route_crosses_response_contract_boundary():
    routes = _runtime_route_functions()
    assert routes, "runtime API route inventory is empty"

    offenders = []
    for node, _methods in routes:
        names = {_call_name(call) for call in _calls(node)}
        if "_contract_response" not in names:
            offenders.append(node.name)

    assert not offenders, (
        "runtime routes must return through _contract_response: " + ", ".join(offenders)
    )


def test_runtime_routes_cannot_bypass_contract_helpers():
    forbidden = {"jsonify", "request.get_json"}
    failures = {}

    for node, _methods in _runtime_route_functions():
        used = sorted(
            name
            for name in {_call_name(call) for call in _calls(node)}
            if name in forbidden
        )
        if used:
            failures[node.name] = used

    assert not failures, (
        "runtime routes bypass strict API contract helpers: " + repr(failures)
    )


def test_runtime_mutation_routes_parse_payload_through_request_contract():
    failures = []
    for node, methods in _runtime_route_functions():
        if not set(methods) & {"POST", "PUT", "PATCH"}:
            continue
        names = {_call_name(call) for call in _calls(node)}
        if "_request_payload" not in names:
            failures.append(node.name)

    assert not failures, (
        "runtime mutation routes must parse bodies through _request_payload: "
        + ", ".join(failures)
    )


def test_every_runtime_contract_reference_is_declared():
    unknown = {}
    for node, _methods in _runtime_route_functions():
        for contract_name in _literal_contract_names(node):
            if contract_name not in CONTRACTS:
                unknown.setdefault(node.name, []).append(contract_name)

    assert not unknown, "runtime routes reference undeclared contracts: " + repr(unknown)


def test_runtime_contract_helpers_themselves_have_single_boundary():
    tree = ast.parse(RUNTIME_API.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    contract_response = functions["_contract_response"]
    names = [_call_name(call) for call in _calls(contract_response)]
    assert names.count("validate_api_contract") == 1
    assert names.count("jsonify") == 1

    request_payload = functions["_request_payload"]
    names = [_call_name(call) for call in _calls(request_payload)]
    assert names.count("request.get_json") == 1
    assert names.count("validate_api_contract") == 1
