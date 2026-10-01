"""Shared API dependencies for programmatic access controls."""

from __future__ import annotations

import os
import secrets
from functools import lru_cache

from fastapi import Header, HTTPException, status

API_KEY_HEADER_NAME = "X-API-Key"
AGENT_ID_HEADER_NAME = "X-Agent-Id"
REQUEST_ID_HEADER_NAME = "X-Request-Id"
APP_API_KEY_ENV = "APP_API_KEY"
APP_API_KEYS_ENV = "APP_API_KEYS"
APP_CORS_ORIGINS_ENV = "APP_CORS_ORIGINS"


@lru_cache(maxsize=1)
def get_configured_api_keys() -> set[str]:
    """Load API keys from environment.

    Supports either a single key via APP_API_KEY or a comma-separated list via
    APP_API_KEYS. Empty values are ignored.
    """
    keys: set[str] = set()

    single_key = os.getenv(APP_API_KEY_ENV, "").strip()
    if single_key:
        keys.add(single_key)

    many_keys = os.getenv(APP_API_KEYS_ENV, "")
    for key in many_keys.split(","):
        cleaned = key.strip()
        if cleaned:
            keys.add(cleaned)

    return keys


def api_key_auth(
    x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER_NAME)
):
    """Enforce API key auth when one or more keys are configured.

    If no key is configured, auth is treated as disabled to preserve existing
    local workflows until APP_API_KEY or APP_API_KEYS is set.
    """
    configured_keys = get_configured_api_keys()
    if not configured_keys:
        return None

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing required header: {API_KEY_HEADER_NAME}",
        )

    for configured in configured_keys:
        if secrets.compare_digest(x_api_key, configured):
            return x_api_key

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid API key",
    )


def get_agent_id(
    x_agent_id: str | None = Header(default=None, alias=AGENT_ID_HEADER_NAME)
) -> str:
    """Return caller-provided agent id or a stable anonymous fallback."""
    cleaned = (x_agent_id or "").strip()
    return cleaned or "anonymous"


@lru_cache(maxsize=1)
def get_cors_origins() -> list[str]:
    """Load allowed CORS origins from APP_CORS_ORIGINS.

    APP_CORS_ORIGINS accepts a comma-separated list of origins.
    """
    raw = os.getenv(APP_CORS_ORIGINS_ENV, "").strip()
    if not raw:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]

    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["http://localhost:3000", "http://127.0.0.1:3000"]
