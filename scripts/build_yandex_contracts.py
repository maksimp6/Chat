"""Compile the pinned YFM API reference into offline, source-bound contracts.

No network access. Examples are retained as evidence, never mistaken for schemas.
Unknown/ambiguous types remain explicit blockers in the generated JSON Schema.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MIRROR = Path("docs/mirrors/yandex-ai-studio")
SOURCE = MIRROR / "source/ru/ai-studio"
OUTPUT = Path("contracts/yandex/catalog.json")
SCHEMA = "http://json-schema.org/draft-07/schema#"


def unknown(reason: str) -> dict[str, Any]:
    return {"x-unresolved": reason}


def fields(text: str) -> list[dict[str, str]]:
    result = []
    for table in re.findall(r"(?ms)^#\|\s*\n(.*?)^\|#", text):
        for row in re.split(r"(?m)^\|\|", table)[1:]:
            row = row.strip().removesuffix("||").strip()
            parts = re.split(r"(?<!\\)\|", row, maxsplit=1)
            if len(parts) != 2 or parts[0].strip() in ("Field", "Name", "Parameter"):
                continue
            result.append({"name": parts[0].strip(), "description": parts[1].strip()})
    return result


def sections(text: str) -> list[dict[str, Any]]:
    result = []
    matches = list(re.finditer(r"(?m)^## (.+)$", text))
    for index, match in enumerate(matches):
        title = match[1]
        anchor = re.search(r"\{#([^}]+)\}", title)
        body = text[
            match.end() : matches[index + 1].start()
            if index + 1 < len(matches)
            else None
        ]
        result.append(
            {
                "title": re.sub(r"\s*\{#[^}]+\}", "", title).strip(),
                "anchor": anchor[1] if anchor else None,
                "line": text[: match.start()].count("\n") + 1,
                "documentation": body.strip(),
                "fields": fields(body),
            }
        )
    return result


def field_schema(field: dict[str, str], aliases: dict[str, str]) -> dict[str, Any]:
    description = field["description"]
    type_line = description.split("\n", 1)[0]
    tokens = re.findall(r"\*\*([^*]+)\*\*", type_line)
    variants = []
    primitive = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "bool": "boolean",
        "double": "number",
        "float": "number",
        "int64": "integer",
        "int32": "integer",
        "null": "null",
        "object": "object",
    }
    map_type = re.search(r"map<\*\*([^*]+)\*\*,\s*\*\*([^*]+)\*\*>", type_line)
    if map_type:
        tokens = tokens[:1]
    for token in tokens:
        # `string | null` occurs inside one bold token in the vendor tables.
        for part in re.split(r"\s*\\?\|\s*", token):
            ref = re.fullmatch(r"\[([^]]+)\]\(#([^)]+)\)", part)
            if ref and ref[2] in aliases:
                spec: dict[str, Any] = {"$ref": "#/definitions/" + aliases[ref[2]]}
            elif part in primitive:
                spec = {"type": primitive[part]}
            elif part == "enum":
                values = re.findall(r"(?m)^\s*- `([^`]+)`(?:\s*:|\s*$)", description)
                spec = (
                    {"enum": list(dict.fromkeys(values))}
                    if values
                    else unknown("enum values absent")
                )
            else:
                spec = unknown("unresolved documented type: " + part)
            variants.append(spec)
    if not variants:
        variants = [unknown("no explicit field type")]
    spec = variants[0] if len(variants) == 1 else {"anyOf": variants}
    if "(int64)" in type_line and spec.get("type") == "string":
        spec["format"] = "int64"
    if map_type:
        if map_type[1] != "string" or map_type[2] not in primitive:
            spec = unknown("unresolved map key/value type")
        else:
            spec = {
                "type": "object",
                "additionalProperties": {"type": primitive[map_type[2]]},
            }
    if field["name"].endswith("[]"):
        spec = {"type": "array", "items": spec}
    spec["description"] = description
    # Preserve prose constraints instead of silently pretending they were compiled.
    # Only the typed table and explicit enum/required markers are compiled here.
    remaining_prose = re.sub(r"Required field\.", "", description)
    if "enum" in spec:
        remaining_prose = re.sub(
            r"must be `([^`]+)`",
            lambda m: "" if m[1] in spec["enum"] else m[0],
            remaining_prose,
        )
    if re.search(
        r"(?i)\b(?:must|should|maximum|minimum|between|no more than|includes only one)\b",
        remaining_prose,
    ):
        spec["x-unresolved"] = "prose constraint requires explicit schema review"
    if "(binary)" in type_line:
        spec["x-unresolved"] = "binary multipart field is not a JSON value contract"
    if re.search(r"NOT SUPPORT", description, re.IGNORECASE):
        spec["x-unresolved"] = "field marked unsupported in snapshot"
    return spec


def table_schema(section: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    if not section["fields"]:
        return unknown("no field table/schema documented for " + section["title"])
    if len(re.findall(r"(?m)^#\|", section["documentation"])) != 1:
        return unknown("multiple tables/union branches require explicit schema review")
    if re.search(r'```json\s*\{\s*"<(?:anyOf|allOf|oneOf)>"', section["documentation"]):
        return unknown(
            "root composition cannot be reconstructed from a flat field table"
        )
    properties = {}
    required = []
    for field in section["fields"]:
        name = field["name"].removesuffix("[]")
        if name in properties:
            raise ValueError("duplicate field " + name)
        properties[name] = field_schema(field, aliases)
        if re.search(r"\bRequired field\.", field["description"]):
            required.append(name)
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "x-requiredness": "only explicit Required field markers; no inferred mandatory fields",
    }
    if required:
        result["required"] = required
    return result


def normalize_schema(spec: Any) -> Any:
    """Keep playground schema constraints; reject unknown JSON Schema types."""
    if isinstance(spec, list):
        return [normalize_schema(item) for item in spec]
    if not isinstance(spec, dict):
        return spec
    result = copy.deepcopy(spec)
    # Traverse schema positions, never interpret data inside enum/default/examples.
    for key in ("properties", "definitions", "patternProperties"):
        if isinstance(result.get(key), dict):
            result[key] = {
                name: normalize_schema(value) for name, value in result[key].items()
            }
    for key in ("items", "additionalProperties", "propertyNames", "not"):
        if key in result:
            result[key] = normalize_schema(result[key])
    for key in ("oneOf", "anyOf", "allOf"):
        if key in result:
            result[key] = [normalize_schema(item) for item in result[key]]
    if result.get("type") == "unknown":
        del result["type"]
        result["x-unresolved"] = "unknown type in apiPlayground"
    description = result.get("description", "")
    # A playground can omit numeric/length/oneof constraints present only in prose.
    if re.search(
        r"(?i)\b(?:must|should|maximum|minimum|between|no more than|includes only one)\b",
        description,
    ):
        result["x-prose-constraints"] = description
    return result


def compile_document(relative: str, text: str, digest: str) -> dict[str, Any]:
    front = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---\n") else {}
    front = front or {}
    # Do not split the YAML's multiline description headings into Markdown sections.
    markdown = text.split("---", 2)[2] if text.startswith("---\n") else text
    parts = sections(markdown)
    offset = text[: len(text) - len(markdown)].count("\n")
    for part in parts:
        part["line"] += offset
    aliases = {s["anchor"]: s["anchor"] for s in parts if s["anchor"]}
    definitions = {s["anchor"]: table_schema(s, aliases) for s in parts if s["anchor"]}

    def package(
        spec: dict[str, Any], defs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "$schema": SCHEMA,
            **spec,
            "definitions": definitions if defs is None else defs,
        }

    requests = {}
    for location, heading in (
        ("path", "Path parameters"),
        ("query", "Query parameters"),
        ("body", "Body parameters"),
    ):
        section = next((s for s in parts if s["title"] == heading), None)
        if section:
            requests[location] = package(table_schema(section, aliases))
    playgrounds = front.get("apiPlayground", [])
    if len(playgrounds) > 1:
        raise ValueError("multiple playgrounds require explicit handling: " + relative)
    if playgrounds:
        api = playgrounds[0]
        defs = normalize_schema(api.get("definitions") or {})
        for key in ("path", "query", "body"):
            if api.get(key) is not None:
                requests[key] = package(normalize_schema(api[key]), defs)

    rpc = re.search(
        r"rpc (\w+) \((stream )?\[([^]]+)\]\(#([^)]+)\)\) returns \((.*)\)\*\*",
        markdown,
    )
    if "## gRPC request" in markdown:
        if not rpc:
            raise ValueError("unparsed gRPC signature: " + relative)
        requests = {"message": package({"$ref": "#/definitions/" + rpc[4]})}
        response_ref = re.search(r"\[([^]]+)\]\(#([^)]+)\)", rpc[5])
        responses = (
            {"message": package({"$ref": "#/definitions/" + response_ref[2]})}
            if response_ref
            else {"message": package(unknown("unresolved gRPC response"))}
        )
        transport = {
            "kind": "grpc",
            "method": rpc[1],
            "signature": rpc[0].removesuffix("**"),
            "request_stream": bool(rpc[2]),
            "response_stream": rpc[5].startswith("stream "),
        }
    else:
        endpoint = re.search(
            r"(?m)^(GET|POST|PATCH|PUT|DELETE|HEAD|OPTIONS) (https?://[^\n]+)", markdown
        )
        if not endpoint:
            raise ValueError("unparsed HTTP binding: " + relative)
        transport = {
            "kind": "http",
            "method": endpoint[1],
            "url_template": endpoint[2].strip(),
        }
        if "clientEvents/" in relative or "serverEvents/" in relative:
            transport["kind"] = "realtime-event"
            transport["direction"] = (
                "client-to-server"
                if "clientEvents/" in relative
                else "server-to-client"
            )
            transport["binding_note"] = (
                "vendor reference binding; not proof of callable standalone HTTP endpoint"
            )
        responses = {}
        for section in parts:
            status = re.search(r"HTTP Code: (\d{3})", section["documentation"])
            if section["title"].startswith("Response") and status:
                if status[1] in responses:
                    raise ValueError("duplicate response status: " + relative)
                responses[status[1]] = package(table_schema(section, aliases))
    title = re.search(r"(?m)^# (.+)$", markdown)
    # A single definition pool per operation avoids copying its entire type graph
    # into every request location and response status.
    pool = {}
    bindings = {"request": {}, "responses": {}}
    for group, schemas in (("request", requests), ("responses", responses)):
        for location, schema in schemas.items():
            prefix = (
                "playground." if playgrounds and group == "request" else "markdown."
            )

            def rebind(value: Any, prefix: str = prefix) -> Any:
                if isinstance(value, list):
                    return [rebind(item) for item in value]
                if not isinstance(value, dict):
                    return value
                return {
                    key: (
                        "#/definitions/" + prefix + item.removeprefix("#/definitions/")
                    )
                    if key == "$ref"
                    and isinstance(item, str)
                    and item.startswith("#/definitions/")
                    else rebind(item)
                    for key, item in value.items()
                }

            for name, definition in schema["definitions"].items():
                pool[prefix + name] = rebind(definition)
            name = group + "." + location
            pool[name] = rebind(
                {
                    key: item
                    for key, item in schema.items()
                    if key not in ("definitions", "$schema")
                }
            )
            bindings[group][location] = "#/definitions/" + name
    return {
        "name": "yandex." + relative.removesuffix(".md").replace("/", "."),
        "source": {"path": str(SOURCE / relative), "sha256": digest},
        "title": title[1] if title else relative,
        "documentation": markdown.split("##", 1)[0].strip(),
        "availability": "unsupported_in_snapshot"
        if re.search(r"NOT SUPPORT", markdown.split("##", 1)[0], re.IGNORECASE)
        else "not_verified_live",
        "transport": transport,
        "request": bindings["request"],
        "responses": bindings["responses"],
        "schema": {"$schema": SCHEMA, "definitions": pool},
        "limitations": [
            "historical snapshot; no live provider compatibility claim",
            "authentication, headers and transport are not validated by payload schemas",
            "prose constraints are retained; table-derived schemas require review before provider conformance claims",
        ]
        + (
            []
            if any(k.isdigit() and int(k) >= 400 for k in responses)
            else ["error response schema absent from this method reference"]
        ),
        "sections": [
            {key: value for key, value in part.items() if key != "fields"}
            for part in parts
        ],
    }


def build(root: Path = ROOT) -> dict[str, Any]:
    manifest_path = root / MIRROR / "manifest.json"
    manifest_data = manifest_path.read_bytes()
    manifest = json.loads(manifest_data)
    entries = {str(MIRROR / entry["path"]): entry for entry in manifest["files"]}
    contracts = []
    excluded = []
    source_root = root / SOURCE
    for path in sorted(source_root.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        entry = entries.get(relative)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not entry or digest != entry["sha256"] or len(data) != entry["bytes"]:
            raise ValueError(
                "source missing from manifest, modified or still an LFS pointer: "
                + relative
            )
        text = data.decode()
        if re.search(r"(?m)^## (?:HTTP|gRPC) request", text):
            contracts.append(
                compile_document(path.relative_to(source_root).as_posix(), text, digest)
            )
        else:
            excluded.append(
                {
                    "path": relative,
                    "sha256": digest,
                    "reason": "no API method/event binding",
                }
            )
    expected = {
        p for p in entries if p.startswith(str(SOURCE) + "/") and p.endswith(".md")
    }
    actual = {p.relative_to(root).as_posix() for p in source_root.rglob("*.md")}
    if expected != actual:
        raise ValueError("source inventory differs from manifest")
    names = [c["name"] for c in contracts]
    if len(names) != len(set(names)):
        raise ValueError("duplicate contract name")
    return {
        "format_version": 1,
        "source_revision": manifest["source_revision"],
        "source_revision_date": manifest["source_revision_date"],
        "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "license": manifest["license"],
        "copyright": manifest["copyright"],
        "derivation": "Schemas extracted from unchanged YANDEX LLC / contributors documentation; strict unknown-field policy added by Logos. See source paths and retained sections for original constraints.",
        "coverage": {
            "api_documents": len(contracts),
            "excluded_documents": len(excluded),
            "by_transport": {
                kind: sum(c["transport"]["kind"] == kind for c in contracts)
                for kind in ("http", "grpc", "realtime-event")
            },
        },
        "contracts": contracts,
        "excluded": excluded,
    }


def render(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render(build(ROOT))
    output = ROOT / OUTPUT
    if args.check:
        if not output.exists() or output.read_text() != rendered:
            parser.exit(
                1,
                "Yandex contracts are stale; run python scripts/build_yandex_contracts.py\n",
            )
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
    print(
        "Yandex API contract catalog verified"
        if args.check
        else "Yandex API contract catalog generated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
