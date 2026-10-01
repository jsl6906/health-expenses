"""Build a lightweight tool catalog from this app's OpenAPI schema.

This script is intentionally simple so you can quickly bootstrap agent tool
definitions from the existing API contract.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _derive_tool_name(method: str, path: str, operation: dict[str, Any]) -> str:
    operation_id = operation.get("operationId")
    if operation_id:
        return _slugify(str(operation_id))
    return _slugify(f"{method}_{path}")


def build_catalog(
    schema: dict[str, Any], api_prefix: str = "/api"
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    paths = schema.get("paths", {})

    for path, ops in paths.items():
        if not isinstance(path, str) or not path.startswith(api_prefix):
            continue
        if not isinstance(ops, dict):
            continue

        for method, operation in ops.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue

            tools.append(
                {
                    "name": _derive_tool_name(method.lower(), path, operation),
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary") or "",
                    "description": operation.get("description") or "",
                    "tags": operation.get("tags") or [],
                }
            )

    tools.sort(key=lambda item: (item["path"], item["method"], item["name"]))
    return tools


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a JSON tool catalog from the app OpenAPI schema"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("APP_BASE_URL", "http://localhost:8080"),
        help="Base app URL, default: http://localhost:8080",
    )
    parser.add_argument(
        "--schema-path",
        default="/api/schema.json",
        help="Schema endpoint path, default: /api/schema.json",
    )
    parser.add_argument(
        "--api-prefix",
        default="/api",
        help="Only include endpoints that start with this prefix",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("APP_API_KEY", ""),
        help="API key for X-API-Key header (optional)",
    )
    parser.add_argument(
        "--output",
        default=".agent/tool-catalog.json",
        help="Output JSON file path",
    )
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}{args.schema_path}"
    headers: dict[str, str] = {}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    response = httpx.get(url, headers=headers, timeout=30.0)
    response.raise_for_status()
    schema = response.json()

    catalog = {
        "source": url,
        "api_prefix": args.api_prefix,
        "tools": build_catalog(schema, api_prefix=args.api_prefix),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    print(f"Wrote {len(catalog['tools'])} tool definitions to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
