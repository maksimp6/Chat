"""Measure how independent the Logos units are from each other.

A *unit* is one top-level package, one top-level module, one file under
``scripts/`` or ``tools/``, or a cluster declared in ``pyproject.toml``
(``[tool.logos.units]``). *Roots* (``[tool.logos.independence] roots``) are the
composition roots that wire units together; they are the only files allowed to
import more than one unit.

The check is static: it parses source files and never imports or runs them.
It is a manual tool, not a CI gate. Counters (all must be 0):

  E  cross-unit references: imports at any depth, importlib/__import__/runpy
     calls, dotted module names and ``.py`` paths of other units in string
     literals, and imports inside embedded ``-c`` programs
  R  roots: a unit imports a root, or a root/entry block contains logic
  S  standalone: a unit's imports do not resolve against its own files,
     the standard library and the declared third-party packages
  V  vendored copies differ from their canonical source
  I  import-time effects in units (registration, binding, configuration)
  L  logging: unit loggers outside ``logos.*`` or units using system_log
  A  public API: package units without a valid ``__all__``
  W  wiring: ``Host`` fields with defaults, hosts never bound by a root
  D  docs: package units without README sections, stale module map

Usage:
  python3 scripts/check_independence.py [--summary] [--only E,S] [--json]
  python3 scripts/check_independence.py --mermaid
  python3 scripts/check_independence.py --routes [REV]
  python3 scripts/check_independence.py --tool-order
  python3 scripts/check_independence.py --entrypoints
  python3 scripts/check_independence.py --sync-vendored
"""

from __future__ import annotations

import argparse
import ast
import collections
import io
import json
import os
import re
import tokenize
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = "ERSVILAWD"
MODULES_DOC = "docs/architecture/modules.md"
MAP_BEGIN = (
    "<!-- BEGIN GENERATED: unit map (scripts/check_independence.py --mermaid) -->"
)
MAP_END = "<!-- END GENERATED: unit map -->"
VENDOR_HEADER = (
    "# Vendored from {canonical}. Do not edit this copy: change the canonical file\n"
    "# and run `python3 scripts/check_independence.py --sync-vendored`.\n"
)
EFFECT_CALLS = {
    "register",
    "register_tool",
    "register_tools",
    "register_blueprint",
    "register_operation",
    "load_source",
    "bind",
    "install",
    "configure",
    "basicConfig",
    "load_dotenv",
    "get_logger",
}
STATE_FACTORIES = {"ContextVar", "Lock", "RLock", "local", "Thread", "Semaphore"}


# ---------------------------------------------------------------------------
# Configuration and unit map


@dataclass
class Config:
    roots: list[str] = field(default_factory=list)
    entry_blocks: list[str] = field(default_factory=list)
    embedded_programs: list[str] = field(default_factory=list)
    standalone_downloads: list[str] = field(default_factory=list)
    data_reads: list[str] = field(default_factory=list)
    third_party: set[str] = field(default_factory=set)
    optional_third_party: set[str] = field(default_factory=set)
    per_file_dirs: list[str] = field(default_factory=list)
    tool_dirs: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    units: dict[str, dict] = field(default_factory=dict)
    vendored: list[dict] = field(default_factory=list)
    effect_calls: set[str] = field(default_factory=set)
    system_log_module: str = ""
    tool_registry_file: str = ""
    tool_sources_file: str = ""
    env_loader: str = ""
    mcp_tools_file: str = ""


def load_config() -> Config:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    logos = data.get("tool", {}).get("logos", {})
    ind = logos.get("independence", {})
    return Config(
        roots=list(ind.get("roots", [])),
        entry_blocks=list(ind.get("entry-blocks", [])),
        embedded_programs=list(ind.get("embedded-programs", [])),
        standalone_downloads=list(ind.get("standalone-downloads", [])),
        data_reads=list(ind.get("data-reads", [])),
        third_party=set(ind.get("third-party", [])),
        optional_third_party=set(ind.get("optional-third-party", [])),
        per_file_dirs=list(ind.get("per-file-dirs", ["scripts", "tools"])),
        tool_dirs=list(ind.get("tool-dirs", ["scripts", "tools"])),
        exclude=list(ind.get("exclude", ["docs/"])),
        units=dict(logos.get("units", {})),
        vendored=list(logos.get("vendored", [])),
        effect_calls=EFFECT_CALLS | set(ind.get("effect-calls", [])),
        system_log_module=ind.get("system-log-module", ""),
        tool_registry_file=ind.get("tool-registry-file", ""),
        tool_sources_file=ind.get("tool-sources-file", ""),
        env_loader=ind.get("env-loader", ""),
        mcp_tools_file=ind.get("mcp-tools-file", ""),
    )


def tracked_files(suffixes: tuple[str, ...]) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
    except (OSError, subprocess.CalledProcessError):
        out = [
            str(p.relative_to(ROOT)).replace(os.sep, "/")
            for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts
        ]
    return sorted(f for f in out if f.endswith(suffixes) and (ROOT / f).is_file())


def under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


@dataclass
class UnitMap:
    config: Config
    files: list[str]
    unit_of: dict[str, str]
    flat: dict[str, bool]
    module_file: dict[str, str]
    root_files: set[str]

    def is_tool(self, unit: str) -> bool:
        files = [f for f, u in self.unit_of.items() if u == unit]
        return bool(files) and all(
            any(under(f, d) for d in self.config.tool_dirs) for f in files
        )

    def unit_files(self, unit: str) -> list[str]:
        return sorted(f for f, u in self.unit_of.items() if u == unit)

    def units(self) -> list[str]:
        return sorted(set(self.unit_of.values()))


def module_name(path: str) -> str:
    name = path[:-3].replace("/", ".")
    return name.removesuffix(".__init__")


def build_unit_map(config: Config) -> UnitMap:
    files = [
        f
        for f in tracked_files((".py",))
        if not any(under(f, e) for e in config.exclude)
    ]
    root_files = {
        f for f in files if any(under(f, r.rstrip("/")) for r in config.roots)
    }
    declared: list[tuple[str, str, bool]] = []
    for name, spec in config.units.items():
        flat = bool(spec.get("flat", False))
        for entry in (
            spec.get("files", []) + spec.get("dirs", []) + spec.get("packages", [])
        ):
            declared.append((entry.rstrip("/"), name, flat))
    unit_of: dict[str, str] = {}
    flat: dict[str, bool] = {}
    for f in files:
        if f in root_files:
            unit_of[f] = "<root>"
            continue
        match = [d for d in declared if under(f, d[0])]
        if match:
            entry, name, is_flat = max(match, key=lambda d: len(d[0]))
            unit_of[f], flat[name] = name, is_flat
            continue
        parts = f.split("/")
        if len(parts) == 1:
            unit = parts[0][:-3]
        elif parts[0] in config.per_file_dirs:
            unit = f[:-3]
        else:
            unit = parts[0]
        unit_of[f] = unit
        flat.setdefault(unit, False)
    module_file = {module_name(f): f for f in files if "-" not in Path(f).name}
    return UnitMap(config, files, unit_of, flat, module_file, root_files)


# ---------------------------------------------------------------------------
# Parsing helpers


_PARSE_CACHE: dict[str, ast.AST | None] = {}


def parse(path: str, text: str | None = None) -> ast.AST | None:
    if text is None and path in _PARSE_CACHE:
        return _PARSE_CACHE[path]
    try:
        source = text if text is not None else (ROOT / path).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        tree = None
    if text is None:
        _PARSE_CACHE[path] = tree
    return tree


def parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def is_main_guard(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def in_main_guard(node: ast.AST, up: dict[ast.AST, ast.AST]) -> bool:
    while node in up:
        node = up[node]
        if is_main_guard(node):
            return True
    return False


def in_optional_import(node: ast.AST, up: dict[ast.AST, ast.AST]) -> bool:
    while node in up:
        parent = up[node]
        if isinstance(parent, ast.Try) and node in parent.body:
            for handler in parent.handlers:
                names = []
                if isinstance(handler.type, ast.Name):
                    names = [handler.type.id]
                elif isinstance(handler.type, ast.Tuple):
                    names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
                if {"ImportError", "ModuleNotFoundError", "Exception"} & set(names):
                    return True
        node = parent
    return False


def call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    if isinstance(node, ast.Name):
        return node.id
    return ""


def docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def looks_like_program(text: str) -> bool:
    return ("import " in text) and ("\n" in text or ";" in text)


# ---------------------------------------------------------------------------
# Reference extraction


@dataclass
class Ref:
    path: str
    line: int
    kind: str  # import, lazy, call, string, path, embedded
    target: str  # dotted module name or path
    main_block: bool = False
    optional: bool = False


def package_of(path: str) -> str:
    name = module_name(path)
    return name if path.endswith("__init__.py") else name.rpartition(".")[0]


def absolute(path: str, node: ast.ImportFrom) -> str:
    base = node.module or ""
    if not node.level:
        return base
    parts = package_of(path).split(".") if package_of(path) else []
    if node.level > 1:
        parts = parts[: len(parts) - (node.level - 1)]
    return ".".join([p for p in parts if p] + ([base] if base else []))


def references(path: str, tree: ast.AST) -> list[Ref]:
    up = parents(tree)
    docs = docstring_nodes(tree)
    refs: list[Ref] = []

    def scope_kind(node: ast.AST) -> str:
        cur = node
        while cur in up:
            cur = up[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return "lazy"
        return "import"

    for node in ast.walk(tree):
        main = in_main_guard(node, up)
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(
                    Ref(
                        path,
                        node.lineno,
                        scope_kind(node),
                        alias.name,
                        main,
                        in_optional_import(node, up),
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            base = absolute(path, node)
            for alias in node.names:
                target = base if alias.name == "*" else f"{base}.{alias.name}"
                refs.append(
                    Ref(
                        path,
                        node.lineno,
                        scope_kind(node),
                        target,
                        main,
                        in_optional_import(node, up),
                    )
                )
        elif isinstance(node, ast.Call):
            name = dotted(node.func)
            if (
                name
                in {
                    "importlib.import_module",
                    "import_module",
                    "__import__",
                    "runpy.run_module",
                    "run_module",
                    "runpy.run_path",
                    "run_path",
                }
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                kind = "path" if name.endswith("run_path") else "call"
                refs.append(Ref(path, node.lineno, kind, node.args[0].value, main))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
        ):
            text = node.value
            line = getattr(node, "lineno", 0)
            if re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+", text):
                refs.append(Ref(path, line, "string", text, main))
            elif re.fullmatch(r"(\./)?[\w./-]+\.py", text):
                refs.append(Ref(path, line, "path", text.removeprefix("./"), main))
            elif looks_like_program(text):
                program = parse("<embedded>", text)
                if program is not None:
                    for inner in ast.walk(program):
                        if isinstance(inner, ast.Import):
                            for alias in inner.names:
                                refs.append(
                                    Ref(path, line, "embedded", alias.name, main)
                                )
                        elif (
                            isinstance(inner, ast.ImportFrom)
                            and inner.module
                            and not inner.level
                        ):
                            for alias in inner.names:
                                refs.append(
                                    Ref(
                                        path,
                                        line,
                                        "embedded",
                                        f"{inner.module}.{alias.name}",
                                        main,
                                    )
                                )
    return refs


def resolve(umap: UnitMap, src: str, ref: Ref) -> str | None:
    """Return the repository file a reference points at, or None if external."""
    if ref.kind == "string":
        return umap.module_file.get(ref.target)
    if ref.kind == "path":
        candidate = ref.target
        if candidate in umap.unit_of:
            return candidate
        sibling = str(Path(src).parent / candidate).replace(os.sep, "/")
        if sibling in umap.unit_of:
            return sibling
        return None
    parts = ref.target.split(".")
    for i in range(len(parts), 0, -1):
        name = ".".join(parts[:i])
        if name in umap.module_file:
            return umap.module_file[name]
    flat_context = umap.flat.get(umap.unit_of.get(src, ""), False) or any(
        under(src, d) for d in umap.config.tool_dirs
    )
    if ref.kind in {"import", "lazy", "call"} and parts[0] and flat_context:
        # Scripts run as files see their own directory on sys.path; packages do not.
        directory = Path(src).parent
        for candidate in (
            directory / f"{parts[0]}.py",
            directory / parts[0] / "__init__.py",
        ):
            rel = str(candidate).replace(os.sep, "/")
            if rel in umap.unit_of:
                return rel
    return None


# ---------------------------------------------------------------------------
# Counters


@dataclass
class Violation:
    counter: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"  {self.counter}  {where}  {self.message}"


def check_edges(
    umap: UnitMap, violations: list[Violation], edges: collections.Counter
) -> None:
    cfg = umap.config
    seen: set[tuple[str, int, str, str]] = set()
    for path in umap.files:
        tree = parse(path)
        if tree is None:
            continue
        unit = umap.unit_of[path]
        for ref in references(path, tree):
            target = resolve(umap, path, ref)
            if target is None:
                continue
            if ref.kind == "path" and f"{path} -> {target}" in cfg.data_reads:
                continue
            target_unit = umap.unit_of[target]
            if unit == "<root>":
                continue
            allowed_root_code = (path in cfg.entry_blocks and ref.main_block) or (
                path in cfg.embedded_programs and ref.kind == "embedded"
            )
            if target_unit == "<root>":
                if allowed_root_code or ref.kind == "path":
                    continue
                violations.append(
                    Violation("R", path, ref.line, f"unit {unit} imports root {target}")
                )
                continue
            if target_unit == unit:
                continue
            if allowed_root_code:
                continue
            key = (path, ref.line, target_unit, ref.kind)
            if key in seen:
                continue
            seen.add(key)
            edges[(unit, target_unit)] += 1
            violations.append(
                Violation(
                    "E",
                    path,
                    ref.line,
                    f"{unit} -> {target_unit} ({ref.kind} {ref.target})",
                )
            )
    for name, spec in cfg.units.items():
        for a, b in spec.get("forbid", []):
            for path in umap.unit_files(name):
                if not under(path, a):
                    continue
                tree = parse(path)
                if tree is None:
                    continue
                for ref in references(path, tree):
                    target = resolve(umap, path, ref)
                    if target and under(target, b):
                        violations.append(
                            Violation(
                                "E",
                                path,
                                ref.line,
                                f"{a} must not import {b} ({ref.target})",
                            )
                        )


ALLOWED_ROOT_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
    ast.Return,
    ast.FunctionDef,
    ast.With,
    ast.Pass,
    ast.Raise,
)


def thin_violations(
    path: str, body: list[ast.stmt], violations: list[Violation], top: bool
) -> None:
    for stmt in body:
        if top and is_main_guard(stmt):
            thin_violations(path, stmt.body, violations, False)
            continue
        if isinstance(stmt, ast.For):
            simple = (
                len(stmt.body) == 1
                and isinstance(stmt.body[0], ast.Expr)
                and isinstance(stmt.body[0].value, ast.Call)
            )
            if not simple:
                violations.append(
                    Violation("R", path, stmt.lineno, "root loop with logic")
                )
            continue
        if not isinstance(stmt, ALLOWED_ROOT_NODES):
            violations.append(
                Violation(
                    "R", path, stmt.lineno, f"root contains {type(stmt).__name__}"
                )
            )
            continue
        for node in ast.walk(stmt):
            if isinstance(
                node,
                (
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                    ast.IfExp,
                    ast.While,
                    ast.Try,
                    ast.ClassDef,
                    ast.If,
                ),
            ):
                violations.append(
                    Violation(
                        "R",
                        path,
                        getattr(node, "lineno", stmt.lineno),
                        f"root contains {type(node).__name__}",
                    )
                )


def check_roots(umap: UnitMap, violations: list[Violation]) -> None:
    for path in sorted(umap.root_files):
        tree = parse(path)
        if tree is not None:
            thin_violations(path, tree.body, violations, True)
    for path in umap.config.entry_blocks:
        tree = parse(path)
        if tree is None:
            continue
        for stmt in tree.body:
            if is_main_guard(stmt):
                thin_violations(path, stmt.body, violations, False)


def module_names_defined(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


def check_standalone(
    umap: UnitMap, violations: list[Violation], failing: set[str]
) -> None:
    cfg = umap.config
    std = set(sys.stdlib_module_names) | {"__future__"}
    for unit in umap.units():
        if unit == "<root>":
            continue
        own = set(umap.unit_files(unit))
        own_modules = {module_name(f): f for f in own}
        declared = local_requirements(own)
        flat_names = {}
        for f in own:
            flat_names.setdefault(Path(f).stem, f)
        for path in sorted(own):
            tree = parse(path)
            if tree is None:
                violations.append(Violation("S", path, 0, "file does not parse"))
                failing.add(unit)
                continue
            up = parents(tree)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if path in cfg.entry_blocks and in_main_guard(node, up):
                    continue
                optional = in_optional_import(node, up)
                if isinstance(node, ast.Import):
                    targets = [(a.name, None) for a in node.names]
                else:
                    base = absolute(path, node)
                    targets = [(base, [a.name for a in node.names])]
                for name, imported in targets:
                    if not name:
                        continue
                    top = name.split(".")[0]
                    if top in std or top in cfg.third_party or top in declared:
                        continue
                    if optional and top in cfg.optional_third_party:
                        continue
                    found = None
                    parts = name.split(".")
                    for i in range(len(parts), 0, -1):
                        candidate = ".".join(parts[:i])
                        if candidate in own_modules:
                            found = own_modules[candidate]
                            break
                    sibling = str(Path(path).parent / f"{top}.py").replace(os.sep, "/")
                    package = str(Path(path).parent / top / "__init__.py").replace(
                        os.sep, "/"
                    )
                    as_file = umap.flat.get(unit) or umap.is_tool(unit)
                    if found is None and as_file and (sibling in own or package in own):
                        found = sibling if sibling in own else package
                    if found is None and umap.flat.get(unit) and top in flat_names:
                        found = flat_names[top]
                    if (
                        found is None
                        and imported
                        and all(f"{name}.{item}" in own_modules for item in imported)
                    ):
                        # ``from scripts import x``: a namespace package of own modules.
                        continue
                    if found is None:
                        violations.append(
                            Violation(
                                "S",
                                path,
                                node.lineno,
                                f"{unit}: '{name}' does not resolve inside the unit",
                            )
                        )
                        failing.add(unit)
                        continue
                    if imported and found.endswith(".py"):
                        target_tree = parse(found)
                        if target_tree is None:
                            continue
                        defined = module_names_defined(target_tree)
                        has_getattr = "__getattr__" in defined
                        for item in imported:
                            if item == "*" or has_getattr:
                                continue
                            sub = f"{module_name(found)}.{item}"
                            if item in defined or sub in own_modules:
                                continue
                            violations.append(
                                Violation(
                                    "S",
                                    path,
                                    node.lineno,
                                    f"{unit}: '{name}' has no name '{item}'",
                                )
                            )
                            failing.add(unit)


def local_requirements(files: set[str]) -> set[str]:
    """Import names a unit declares in a requirements.txt next to its own files."""
    names: set[str] = set()
    for directory in sorted({Path(f).parent for f in files}):
        requirements = ROOT / directory / "requirements.txt"
        if not requirements.is_file():
            continue
        for line in requirements.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line.split("#", 1)[0])
            if match:
                names.add(match.group(1).lower().replace("-", "_"))
    return names


def module_level_state(tree: ast.AST) -> list[int]:
    lines = []
    for stmt in getattr(tree, "body", []):
        if (
            isinstance(stmt, (ast.Assign, ast.AnnAssign))
            and isinstance(stmt.value, ast.Call)
            and call_name(stmt.value) in STATE_FACTORIES
        ):
            lines.append(stmt.lineno)
    return lines


def check_vendored(umap: UnitMap, violations: list[Violation]) -> None:
    for item in umap.config.vendored:
        canonical = item["canonical"]
        canon_tree = parse(canonical)
        if canon_tree is None:
            violations.append(Violation("V", canonical, 0, "canonical source missing"))
            continue
        for line in module_level_state(canon_tree):
            violations.append(
                Violation(
                    "V", canonical, line, "vendored source holds module-level state"
                )
            )
        expected = ast.dump(canon_tree)
        for copy in item.get("copies", []):
            tree = parse(copy)
            if tree is None:
                violations.append(Violation("V", copy, 0, "vendored copy missing"))
            elif ast.dump(tree) != expected:
                violations.append(
                    Violation(
                        "V", copy, 0, f"differs from {canonical} (run --sync-vendored)"
                    )
                )


def check_import_effects(umap: UnitMap, violations: list[Violation]) -> None:
    calls = umap.config.effect_calls
    for path in umap.files:
        if umap.unit_of[path] == "<root>":
            continue
        tree = parse(path)
        if tree is None:
            continue

        def scan(stmts: list[ast.stmt], path: str = path) -> None:
            for stmt in stmts:
                if is_main_guard(stmt):
                    continue
                if isinstance(
                    stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    for deco in stmt.decorator_list:
                        for node in ast.walk(deco):
                            if isinstance(node, ast.Call) and call_name(node) in calls:
                                violations.append(
                                    Violation(
                                        "I",
                                        path,
                                        node.lineno,
                                        f"import-time call {call_name(node)}()",
                                    )
                                )
                    continue
                if isinstance(stmt, (ast.If, ast.Try, ast.With)):
                    for block in ("body", "orelse", "finalbody"):
                        scan(getattr(stmt, block, []))
                    for handler in getattr(stmt, "handlers", []):
                        scan(handler.body)
                    continue
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Lambda):
                        continue
                    if isinstance(node, ast.Call) and call_name(node) in calls:
                        violations.append(
                            Violation(
                                "I",
                                path,
                                node.lineno,
                                f"import-time call {call_name(node)}()",
                            )
                        )

        scan(tree.body)


def check_logging(umap: UnitMap, violations: list[Violation]) -> None:
    system_log = umap.config.system_log_module
    owner = system_log.split(".")[0]
    for path in umap.files:
        unit = umap.unit_of[path]
        if unit == "<root>" or umap.is_tool(unit):
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and dotted(node.func) == "logging.getLogger":
                if not node.args:
                    violations.append(Violation("L", path, node.lineno, "root logger"))
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Name) and arg.id == "__name__":
                    violations.append(
                        Violation("L", path, node.lineno, "logger named by __name__")
                    )
                elif (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value != "logos"
                    and not arg.value.startswith("logos.")
                ):
                    violations.append(
                        Violation(
                            "L",
                            path,
                            node.lineno,
                            f"logger '{arg.value}' outside logos.*",
                        )
                    )
            if (
                system_log
                and isinstance(node, (ast.Import, ast.ImportFrom))
                and unit != owner
            ):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [f"{absolute(path, node)}.{a.name}" for a in node.names]
                )
                if any(
                    n == system_log or n.startswith(system_log + ".") for n in names
                ):
                    violations.append(
                        Violation("L", path, node.lineno, f"unit uses {system_log}")
                    )


def package_units(umap: UnitMap) -> list[tuple[str, str]]:
    out = []
    for unit in umap.units():
        if unit == "<root>" or umap.is_tool(unit):
            continue
        inits = [f for f in umap.unit_files(unit) if f == f"{unit}/__init__.py"]
        if inits:
            out.append((unit, inits[0]))
    return out


def check_api(umap: UnitMap, violations: list[Violation]) -> None:
    for unit, init in package_units(umap):
        tree = parse(init)
        if tree is None:
            continue
        exported = None
        for stmt in tree.body:
            targets = (
                stmt.targets
                if isinstance(stmt, ast.Assign)
                else [stmt.target]
                if isinstance(stmt, ast.AnnAssign) and stmt.value is not None
                else []
            )
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                try:
                    exported = list(ast.literal_eval(stmt.value))
                except ValueError:
                    exported = []
        if exported is None:
            violations.append(Violation("A", init, 0, f"{unit} has no __all__"))
            continue
        defined = module_names_defined(tree)
        if "__getattr__" in defined:
            continue
        for name in exported:
            if name not in defined:
                violations.append(
                    Violation("A", init, 0, f"__all__ names missing symbol {name}")
                )
    for path in sorted(umap.root_files):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if alias.name.startswith("_") and not alias.name.startswith("__"):
                        violations.append(
                            Violation(
                                "A",
                                path,
                                node.lineno,
                                f"root uses private {node.module}.{alias.name}",
                            )
                        )


def check_wiring(umap: UnitMap, violations: list[Violation]) -> None:
    wiring = ROOT / WIRING_FILE
    if wiring.is_file() and wiring.read_text(encoding="utf-8") != render_wiring(umap):
        violations.append(Violation("W", WIRING_FILE, 0, "stale: run --write-wiring"))
    root_text = "\n".join(
        (ROOT / f).read_text(encoding="utf-8") for f in sorted(umap.root_files)
    )
    for path in umap.files:
        name = Path(path).name
        if name != "host.py" and not name.endswith("_host.py"):
            continue
        if umap.unit_of[path] == "<root>":
            continue
        unit = umap.unit_of[path]
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Host":
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                        violations.append(
                            Violation(
                                "W", path, stmt.lineno, "Host field has a default"
                            )
                        )
        if not re.search(rf"\b{re.escape(module_name(path))}\b", root_text):
            violations.append(
                Violation("W", path, 0, f"{unit} host is never bound by a root")
            )
        check_host_names(umap, path, tree, violations)
    for path in sorted(umap.root_files):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            target = repo_module_file(node.module or "")
            if target is None:
                continue
            names, dynamic = top_level_names(target)
            for alias in node.names:
                if alias.name in names or dynamic or alias.name == "*":
                    continue
                if repo_module_file(f"{node.module}.{alias.name}") is None:
                    violations.append(
                        Violation(
                            "W",
                            path,
                            node.lineno,
                            f"provider {node.module}.{alias.name} does not exist",
                        )
                    )


def repo_module_file(module: str) -> str | None:
    """The repository file of a dotted module name, if it is one."""
    base = ROOT.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(ROOT).as_posix()
    return None


def top_level_names(path: str) -> tuple[set[str], bool]:
    """Names a module binds at top level, and whether it has a module ``__getattr__``."""
    tree = parse(path)
    names: set[str] = set()
    if tree is None:
        return names, True
    statements = list(tree.body)
    while statements:
        node = statements.pop()
        if isinstance(node, (ast.If, ast.Try)):
            statements.extend(node.body)
            statements.extend(node.orelse)
            if isinstance(node, ast.Try):
                statements.extend(node.finalbody)
                for handler in node.handlers:
                    statements.extend(handler.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names, "__getattr__" in names


def check_host_names(
    umap: UnitMap, host_path: str, host_tree: ast.Module, violations: list[Violation]
) -> None:
    """Every ``bound.<name>`` and every name imported from the host exists in it."""
    fields: set[str] = set()
    for node in host_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Host":
            fields = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    exported, _ = top_level_names(host_path)
    host_module = module_name(host_path)
    unit = umap.unit_of[host_path]
    for path in umap.unit_files(unit):
        tree = parse(path)
        if tree is None:
            continue
        bound_names = {"bound"} if path == host_path else set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == host_module:
                for alias in node.names:
                    if alias.name == "bound":
                        bound_names.add(alias.asname or alias.name)
                    if alias.name not in exported:
                        violations.append(
                            Violation(
                                "W",
                                path,
                                node.lineno,
                                f"{host_module} has no {alias.name}",
                            )
                        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in bound_names
                and node.attr not in fields
            ):
                violations.append(
                    Violation("W", path, node.lineno, f"Host has no port {node.attr}")
                )


def check_docs(umap: UnitMap, violations: list[Violation], mermaid: str) -> None:
    for unit, init in package_units(umap):
        readme = ROOT / unit / "README.md"
        if not readme.is_file():
            violations.append(
                Violation("D", f"{unit}/README.md", 0, "missing unit README")
            )
            continue
        text = readme.read_text(encoding="utf-8")
        headings = {
            h.strip().lower() for h in re.findall(r"^#+\s*(.+)$", text, re.MULTILINE)
        }
        required = ["public api"]
        if (ROOT / unit / "ports.py").is_file():
            required.append("ports")
        if (ROOT / unit / "install.py").is_file():
            required.append("install")
        for section in required:
            if not any(h.startswith(section) for h in headings):
                violations.append(
                    Violation(
                        "D", f"{unit}/README.md", 0, f"missing section '{section}'"
                    )
                )
    for unit in umap.units():
        if unit == "<root>" or not umap.is_tool(unit):
            continue
        for path in umap.unit_files(unit):
            tree = parse(path)
            if tree is not None and ast.get_docstring(tree) is None:
                violations.append(Violation("D", path, 0, "tool file has no docstring"))
    doc = ROOT / MODULES_DOC
    if not doc.is_file():
        violations.append(Violation("D", MODULES_DOC, 0, "module map document missing"))
        return
    text = doc.read_text(encoding="utf-8")
    if MAP_BEGIN not in text or MAP_END not in text:
        violations.append(Violation("D", MODULES_DOC, 0, "generated map block missing"))
        return
    block = text.split(MAP_BEGIN, 1)[1].split(MAP_END, 1)[0].strip()
    if block != mermaid.strip():
        violations.append(
            Violation("D", MODULES_DOC, 0, "generated map block is stale")
        )


def render_mermaid(umap: UnitMap, edges: collections.Counter) -> str:
    def node(name: str) -> str:
        return re.sub(r"\W", "_", name)

    app_units = [u for u in umap.units() if u != "<root>" and not umap.is_tool(u)]
    lines = ["```mermaid", "flowchart LR"]
    lines.append("    roots([composition roots])")
    for unit in app_units:
        lines.append(f"    {node(unit)}[{unit}]")
    for (a, b), count in sorted(edges.items()):
        if a in app_units and b in app_units:
            lines.append(f"    {node(a)} -->|{count}| {node(b)}")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auxiliary reports


def git_show(rev: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def git_files(rev: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", rev],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        f
        for f in result.stdout.split("\n")
        if f.endswith(".py") and not f.startswith("docs/")
    ]


def route_table(rev: str | None) -> list[tuple[str, str, str]]:
    files = (
        git_files(rev)
        if rev
        else [f for f in tracked_files((".py",)) if not f.startswith("docs/")]
    )
    blueprints: dict[tuple[str, str], tuple[str, str]] = {}
    registrations: dict[str, str] = {}
    decorated = []
    for path in files:
        text = git_show(rev, path) if rev else (ROOT / path).read_text(encoding="utf-8")
        tree = parse(path, text) if text is not None else None
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and call_name(node.value) == "Blueprint"
            ):
                args = node.value.args
                bp_name = (
                    args[0].value if args and isinstance(args[0], ast.Constant) else "?"
                )
                prefix = ""
                for kw in node.value.keywords:
                    if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value or ""
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        blueprints[(path, target.id)] = (bp_name, prefix)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_url_rule"
            ):
                decorated.append((path, *url_rule(node)))
            if isinstance(node, ast.Call) and call_name(node) == "register_blueprint":
                for kw in node.keywords:
                    if (
                        kw.arg == "url_prefix"
                        and node.args
                        and isinstance(kw.value, ast.Constant)
                    ):
                        registrations[dotted(node.args[0])] = kw.value.value
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if not isinstance(deco, ast.Call) or not isinstance(
                        deco.func, ast.Attribute
                    ):
                        continue
                    verb = deco.func.attr
                    if verb not in {"route", "get", "post", "put", "patch", "delete"}:
                        continue
                    owner = dotted(deco.func.value)
                    rule = "?"
                    if deco.args and isinstance(deco.args[0], ast.Constant):
                        rule = deco.args[0].value
                    elif deco.args and dotted(deco.args[0]):
                        rule = "{" + dotted(deco.args[0]) + "}"
                    methods = [verb.upper()] if verb != "route" else ["GET"]
                    endpoint = node.name
                    for kw in deco.keywords:
                        if kw.arg == "methods":
                            try:
                                methods = sorted(ast.literal_eval(kw.value))
                            except ValueError:
                                pass
                        if kw.arg == "endpoint" and isinstance(kw.value, ast.Constant):
                            endpoint = kw.value.value
                    decorated.append((path, owner, rule, methods, endpoint))
    table = []
    for path, owner, rule, methods, endpoint in decorated:
        if owner == "app":
            table.append((rule, ",".join(methods), endpoint))
            continue
        bp_name, prefix = blueprints.get((path, owner), ("?", ""))
        prefix = registrations.get(owner, prefix)
        table.append((f"{prefix}{rule}", ",".join(methods), f"{bp_name}.{endpoint}"))
    return sorted(set(table))


def url_rule(call: ast.Call) -> tuple[str, str, list[str], str]:
    """(owner, rule, methods, endpoint) of ``owner.add_url_rule(rule, endpoint, view_func)``."""
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    args = list(call.args)
    rule_node = args[0] if args else keywords.get("rule")
    rule = "?"
    if isinstance(rule_node, ast.Constant):
        rule = rule_node.value
    elif rule_node is not None and dotted(rule_node):
        rule = "{" + dotted(rule_node) + "}"
    endpoint_node = args[1] if len(args) > 1 else keywords.get("endpoint")
    view = args[2] if len(args) > 2 else keywords.get("view_func")
    endpoint = "?"
    if isinstance(endpoint_node, ast.Constant) and endpoint_node.value:
        endpoint = endpoint_node.value
    elif view is not None and dotted(view):
        endpoint = dotted(view).rsplit(".", 1)[-1]
    methods = ["GET"]
    if "methods" in keywords:
        try:
            methods = sorted(ast.literal_eval(keywords["methods"]))
        except ValueError:
            pass
    return dotted(call.func.value), rule, methods, endpoint


def tool_order(config: Config) -> list[str]:
    order: list[str] = []
    registry = parse(config.tool_registry_file)
    if registry is not None:
        for node in ast.walk(registry):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            if any(
                isinstance(t, ast.Name) and t.id == "_TOOL_SOURCES" for t in targets
            ):
                for item in getattr(node.value, "elts", []):
                    first = item.elts[0] if isinstance(item, ast.Tuple) else None
                    if isinstance(first, ast.Constant) and first.value not in order:
                        order.append(first.value)
    sources = parse(config.tool_sources_file) if config.tool_sources_file else None
    if sources is not None:
        for node in ast.walk(sources):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SOURCES" for t in node.targets
            ):
                for item in getattr(node.value, "elts", []):
                    first = item.elts[0] if isinstance(item, ast.Tuple) else None
                    if isinstance(first, ast.Constant) and first.value not in order:
                        order.append(first.value)
    for path in sorted(Path(ROOT).glob("*/install.py")):
        tree = parse(str(path.relative_to(ROOT)))
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and call_name(node) == "load_source"
                and node.args
            ):
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value not in order:
                    order.append(first.value)
    tools = parse(config.mcp_tools_file)
    if tools is not None:
        for node in ast.walk(tools):
            if (
                isinstance(node, ast.Call)
                and call_name(node) == "register"
                and node.args
            ):
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value not in order:
                    order.append(first.value)
    return order


def entrypoint_report(umap: UnitMap) -> list[str]:
    problems = []
    std = set(sys.stdlib_module_names) | {"__future__"}
    for path in umap.config.standalone_downloads:
        tree = parse(path)
        if tree is None:
            problems.append(f"{path}: missing or does not parse")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                sibling = (ROOT / Path(path).parent / f"{top}.py").is_file()
                if top in std or top in umap.config.third_party or sibling:
                    continue
                problems.append(f"{path}:{node.lineno}: imports {name}")
    docs = [
        f
        for f in tracked_files((".md", ".yml", ".yaml", ".sh"))
        if not f.startswith("docs/mirrors")
    ]
    for doc in docs:
        text = (ROOT / doc).read_text(encoding="utf-8", errors="ignore")
        for module in re.findall(r"python3? -m ([A-Za-z_][\w.]*)", text):
            top = module.split(".")[0]
            if (
                top in std
                or top in umap.config.third_party
                or top
                in {
                    "pip",
                    "venv",
                    "ruff",
                    "mypy",
                    "pytest",
                    "build",
                    "twine",
                }
            ):
                continue
            if (
                module not in umap.module_file
                and f"{module}.__main__" not in umap.module_file
            ):
                problems.append(f"{doc}: python -m {module} does not resolve")
        for script in re.findall(r"python3? ((?:scripts|tools)/[\w/.-]+\.py)", text):
            if not (ROOT / script).is_file():
                problems.append(f"{doc}: {script} does not exist")
    return sorted(set(problems))


def sync_vendored(umap: UnitMap) -> list[str]:
    written = []
    for item in umap.config.vendored:
        canonical = item["canonical"]
        source = (ROOT / canonical).read_text(encoding="utf-8")
        for copy in item.get("copies", []):
            target = ROOT / copy
            target.parent.mkdir(parents=True, exist_ok=True)
            content = VENDOR_HEADER.format(canonical=canonical) + source
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                target.write_text(content, encoding="utf-8")
                written.append(copy)
    return written


WIRING_FILE = "composition/wiring.py"
WIRING_HEADER = '''"""Which unit provides each port: the one place where units meet.

Generated from the units' ``host.py`` files by
``python3 scripts/check_independence.py --write-wiring``: each ``Host`` field's
comment names its provider. ``register_all`` loads ``core/.env`` and registers a
factory per unit; a factory imports its providers and builds the host the first
time the unit needs it."""

from __future__ import annotations

from importlib import import_module

from {module} import {name}
'''


def host_files(umap: UnitMap) -> list[tuple[str, str]]:
    """(factory key, host module) of every unit host, sorted by key."""
    hosts = []
    for path in umap.files:
        name = Path(path).name
        if umap.unit_of[path] == "<root>":
            continue
        if name == "host.py" and path.count("/") == 1:
            hosts.append((path.split("/")[0], module_name(path)))
        elif name.endswith("_host.py") and path.startswith("scripts/"):
            hosts.append((Path(path).stem.removesuffix("_host"), module_name(path)))
    return sorted(hosts)


def host_providers(host_module: str) -> list[tuple[str, str]]:
    """(port, provider) of a host, in field order, from the ``Host`` field comments."""
    path = repo_module_file(host_module)
    if path is None:
        return []
    text = (ROOT / path).read_text(encoding="utf-8")
    comments = {
        token.start[0]: token.string.lstrip("#").strip()
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type == tokenize.COMMENT
    }
    ports = []
    for node in ast.parse(text).body:
        if not (isinstance(node, ast.ClassDef) and node.name == "Host"):
            continue
        for field_node in node.body:
            if isinstance(field_node, ast.AnnAssign) and isinstance(
                field_node.target, ast.Name
            ):
                lines = range(field_node.lineno, (field_node.end_lineno or 0) + 2)
                provider = next((comments[n] for n in lines if n in comments), "")
                ports.append((field_node.target.id, provider))
    return ports


def render_wiring(umap: UnitMap) -> str:
    def fits(line: str) -> bool:
        return len(line) <= 88

    module, name = umap.config.env_loader.rsplit(".", 1)
    parts = [WIRING_HEADER.format(module=module, name=name)]
    hosts = host_files(umap)
    for key, module in hosts:
        parent, leaf = module.rsplit(".", 1)
        lines = [
            f"def _{key}():",
            f"    from {parent} import {leaf}" + ("" if leaf == "host" else " as host"),
        ]
        for port, provider in host_providers(module):
            if repo_module_file(provider) is not None:
                lines.append(f"    import {provider} as {port}")
                continue
            source, attr = provider.rsplit(".", 1)
            line = f"    from {source} import {attr} as {port}"
            lines.append(
                line
                if fits(line)
                else f"    from {source} import (\n        {attr} as {port},\n    )"
            )
        args = [f"{port}={port}" for port, _ in host_providers(module)]
        call = f"    return host.Host({', '.join(args)})"
        if not fits(call):
            inner = "        " + ", ".join(args)
            call = (
                f"    return host.Host(\n{inner}\n    )"
                if fits(inner)
                else "    return host.Host(\n"
                + "".join(f"        {a},\n" for a in args)
                + "    )"
            )
        lines += ["", call]
        parts.append("\n\n" + "\n".join(lines) + "\n")
    parts.append(
        "\n\n# Each unit's host module and factory, in registration order.\n_HOSTS = {\n"
    )
    parts += [f'    "{key}": ("{module}", _{key}),\n' for key, module in hosts]
    parts.append(
        "}\n\n\n"
        "def register_units(*units: str) -> None:\n"
        '    """Register the factories of these units only; ``core/.env`` is not loaded."""\n'
        "    for unit in units:\n"
        "        module, factory = _HOSTS[unit]\n"
        "        import_module(module).provide(factory)\n"
        "\n\n"
        "def register_all() -> None:\n"
        '    """Load ``core/.env`` and register every unit\'s factory."""\n'
        f"    {name}()\n"
        "    register_units(*_HOSTS)\n"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Entry point


def run_checks(umap: UnitMap) -> tuple[list[Violation], collections.Counter, set[str]]:
    violations: list[Violation] = []
    edges: collections.Counter = collections.Counter()
    failing: set[str] = set()
    check_edges(umap, violations, edges)
    check_roots(umap, violations)
    check_standalone(umap, violations, failing)
    check_vendored(umap, violations)
    check_import_effects(umap, violations)
    check_logging(umap, violations)
    check_api(umap, violations)
    check_wiring(umap, violations)
    check_docs(umap, violations, render_mermaid(umap, edges))
    return violations, edges, failing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--summary", action="store_true", help="print counters only")
    parser.add_argument("--only", default="", help="comma-separated counters to report")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--mermaid", action="store_true", help="print the unit map")
    parser.add_argument(
        "--routes",
        nargs="?",
        const="",
        default=None,
        metavar="REV",
        help="print the static route table (of REV if given)",
    )
    parser.add_argument(
        "--tool-order", action="store_true", help="print tool categories"
    )
    parser.add_argument(
        "--entrypoints",
        action="store_true",
        help="check documented invocations and single-file downloads",
    )
    parser.add_argument(
        "--sync-vendored",
        action="store_true",
        help="rewrite vendored copies from their canonical sources",
    )
    parser.add_argument(
        "--write-wiring",
        action="store_true",
        help=f"regenerate {WIRING_FILE} from the units' host files",
    )
    args = parser.parse_args(argv)

    if args.routes is not None:
        for rule, methods, endpoint in route_table(args.routes or None):
            print(f"{rule}\t{methods}\t{endpoint}")
        return 0
    if args.tool_order:
        print("\n".join(tool_order(load_config())))
        return 0

    umap = build_unit_map(load_config())
    if args.sync_vendored:
        for path in sync_vendored(umap):
            print(f"wrote {path}")
        return 0
    if args.write_wiring:
        (ROOT / WIRING_FILE).write_text(render_wiring(umap), encoding="utf-8")
        print(f"wrote {WIRING_FILE}")
        return 0
    if args.entrypoints:
        problems = entrypoint_report(umap)
        print("\n".join(problems) if problems else "all documented invocations resolve")
        return 1 if problems else 0

    violations, edges, failing = run_checks(umap)
    if args.mermaid:
        print(render_mermaid(umap, edges))
        return 0
    only = set(args.only.replace(",", "").upper()) if args.only else set(COUNTERS)
    counts = {c: 0 for c in COUNTERS}
    for v in violations:
        counts[v.counter] += 1
    counts["S"] = len(failing)
    shown = [v for v in violations if v.counter in only]
    total = sum(counts[c] for c in COUNTERS if c in only)
    if args.json:
        print(
            json.dumps(
                {
                    "counts": counts,
                    "total": total,
                    "units": len([u for u in umap.units() if u != "<root>"]),
                    "violations": [v.__dict__ for v in shown],
                },
                indent=2,
            )
        )
        return 1 if total else 0
    if not args.summary:
        for counter in COUNTERS:
            group = [v for v in shown if v.counter == counter]
            if group:
                print(f"{counter}: {len(group)}")
                print("\n".join(v.render() for v in group))
    print(
        " ".join(f"{c}={counts[c]}" for c in COUNTERS if c in only) + f" TOTAL={total}"
    )
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
