"""LLM / Gemini configuration loader.

Loads environment variables (via python-dotenv if present) and exposes a simple
function to retrieve the Gemini API key. Keeps LLM-specific setup isolated.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

try:  # load .env if available
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover
    pass

GEMINI_ENV_KEY = "GEMINI_API_KEY"
GEMINI_MODEL_ENV_KEY = "GEMINI_MODEL_NAME"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
EOB_DEBUG_PREVIEW_KEY = "EOB_DEBUG_PREVIEW"
EOB_USE_FILE_UPLOAD_KEY = "EOB_USE_FILE_UPLOAD"


class LLMConfigError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_gemini_api_key(required: bool = False) -> Optional[str]:
    key = os.getenv(GEMINI_ENV_KEY)
    if required and not key:
        raise LLMConfigError("GEMINI_API_KEY is not set. Add it to your .env file.")
    return key


@lru_cache(maxsize=1)
def get_gemini_model_name() -> str:
    return os.getenv(GEMINI_MODEL_ENV_KEY, DEFAULT_GEMINI_MODEL)


@lru_cache(maxsize=1)
def get_eob_debug_preview_enabled() -> bool:
    val = os.getenv(EOB_DEBUG_PREVIEW_KEY, "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_eob_use_file_upload() -> bool:
    val = os.getenv(EOB_USE_FILE_UPLOAD_KEY, "true").strip().lower()
    return val in {"1", "true", "yes", "on"}
