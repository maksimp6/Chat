#!/usr/bin/env python3
"""Render the exact Chrome MCP/OAuth routes for Cloud.ru Shared API Gateway."""

from __future__ import annotations

import argparse
import json
from urllib.parse import urlsplit
from uuid import UUID


def gateway_spec(container_id: str, public_url: str) -> dict:
    container_id = str(UUID(container_id))
    origin = urlsplit(public_url)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.port not in (None, 443)
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        raise ValueError("public_url must be an HTTPS origin")
    routes = {
        "/healthz": ("get",),
        "/browser/v1/mcp": ("get", "post", "delete"),
        "/browser/v1/status": ("get",),
        **{
            f"/browser/v1/{action}": ("post",)
            for action in ("wake", "sleep", "navigate", "click", "type", "extract", "screenshot")
        },
        "/.well-known/oauth-protected-resource": ("get",),
        "/.well-known/oauth-protected-resource/browser/v1/mcp": ("get",),
        "/.well-known/oauth-authorization-server": ("get",),
        "/.well-known/oauth-authorization-server/browser/oauth": ("get",),
        "/browser/oauth/register": ("post",),
        "/browser/oauth/authorize": ("get", "post"),
        "/browser/oauth/github/callback": ("get",),
        "/browser/oauth/token": ("post",),
        "/browser/oauth/revoke": ("post",),
    }
    paths = {}
    for path, methods in routes.items():
        paths[path] = {}
        for method in methods:
            paths[path][method] = {
                "operationId": method
                + "_"
                + path.strip("/").replace("/", "_").replace("-", "_").replace(".", "_"),
                "x-cloud-backend": {"$ref": "#/components/x-cloud-backends/ChromeWorker"},
                "x-cloud-limit-count": {"count": 120, "time_window": 60, "rejected_code": 429},
                "responses": {
                    "default": {
                        "description": "Forward the worker MCP/OAuth response and headers without caching"
                    }
                },
            }
    return {
        "openapi": "3.0.0",
        "info": {"title": "Chrome Playwright MCP", "version": "1.0.0"},
        "servers": [{"url": public_url.rstrip("/")}],
        "paths": paths,
        "components": {
            "x-cloud-backends": {
                "ChromeWorker": {
                    "name": "Chrome_Worker",
                    "type": "serverless_container",
                    "scheme": "http",
                    "nodes": [{"container_id": container_id, "weight": 1}],
                    "timeout": {"connect": 10, "send": 300, "read": 300},
                }
            }
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--public-url", required=True)
    args = parser.parse_args()
    print(json.dumps(gateway_spec(args.container_id, args.public_url), indent=2))


if __name__ == "__main__":
    main()
