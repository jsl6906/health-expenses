"""Gemini-backed EOB parsing utilities.

This module provides a best-effort extraction of structured charge rows from an
EOB (PDF or text) using:
 1. Lightweight PDF text extraction (pypdf) or raw bytes fallback.
 2. A prompt to Gemini instructing it to return JSON lines for each charge.
 3. Defensive parsing & normalization into a common structure compatible with
    ParsedChargeRow in `eob_import`.

If GEMINI_API_KEY is not configured, a graceful fallback returning an empty
list (so caller can decide to use mock parser) is provided.
"""
from __future__ import annotations

import json
import re
from datetime import date
from io import BytesIO
from typing import Any, Dict, List

from ..llm_config import (
    get_eob_debug_preview_enabled,
    get_eob_use_file_upload,
    get_gemini_api_key,
    get_gemini_model_name,
)

# Lazy import google.generativeai so the app can start without the dependency
try:  # pragma: no cover - runtime guarded
    import google.generativeai as genai  # type: ignore
except Exception:  # pragma: no cover
    genai = None  # type: ignore

try:  # PDF extraction optional
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

JSON_EXAMPLE = {
    "service_date": "2025-01-15",
    "cpt_code": "99213",
    "description": "Office visit",
    # charged_amount should represent the insurer-covered/allowed amount (what became the 'charge' for insurance)
    "charged_amount": 100.00,
    "owed_amount": 40.00,
    "provider_name": "Example Clinic",
    "family_member": "Alex",
    "insurance_paid_amount": 60.00,
    # Example: a line showing a 'Discount' or 'Disallowed' amount should map here
    "insurance_discount_amount": 40.00,
    # base_fee_amount should represent the original billed amount ("Billed Charges" on the EOB)
    "base_fee_amount": 140.00,
    "reimbursement_amount": 0.00,
}
PROMPT_TEMPLATE = (
    "You are an expert medical EOB parsing assistant. "
    "Extract top-level metadata about this EOB and each distinct line item representing a billed service from the provided EOB text. "
    "Return ONLY a single JSON OBJECT with two keys: 'metadata' and 'rows'. 'metadata' should be an object with possible keys: family_member (patient name string or null), provider_name (string or null), eob_claim_num (string or null), doctor_name (string or null). 'rows' should be a JSON array where each element is an object with the keys: service_date (YYYY-MM-DD or null), cpt_code (string or null), description (string), charged_amount (number or null), owed_amount (number or null), provider_name (string or null), family_member (string or null), insurance_paid_amount (number or null), insurance_discount_amount (number or null), base_fee_amount (number or null), reimbursement_amount (number or null). "
    "Important mapping guidance: On many EOBs you will see both a 'Billed Charges' (or simply 'Billed') value and a separate 'Allowed', 'Covered', or 'Covered Charges' / 'Covered Amount' value. Treat the EOB's original billed amount as the 'base_fee_amount' (this is the full provider charge). Treat the insurer 'Allowed' or 'Covered' amount as the 'charged_amount' (this represents the amount considered by the insurer / contracted allowed amount). "
    "If the document uses language like 'Covered', 'Allowed', 'Covered Charges', 'Allowed Amount', or 'Total Allowed', prefer that value for 'charged_amount'. Do NOT set both 'base_fee_amount' and 'charged_amount' to the same number unless the EOB clearly shows they are equal. "
    "If a row lists separate 'Insurance Paid' and 'Patient Owed' values, fill 'insurance_paid_amount' and 'owed_amount' respectively. If you can only find a single amount labeled 'Charges' or 'Amount', put it into 'base_fee_amount' and leave 'charged_amount' null unless context makes it clear it is an allowed/covered amount. "
    "If the EOB includes a labeled 'Discount', 'Discounts', 'Disallowed', 'Disallowed Amount', or similar reduction fields, map that value to 'insurance_discount_amount' (this typically represents the contractual or disallowed portion deducted from the billed amount). If both an 'Insurance Paid' and a 'Discount'/'Disallowed' are present, populate both 'insurance_paid_amount' and 'insurance_discount_amount' respectively. "
    "If a value is missing or unclear use null. Dates should be best-effort parsed. "
    f"Example row element: {json.dumps(JSON_EXAMPLE)}. "
    'Example full output format: {"metadata": {"family_member": "Alex", "provider_name": "Example Clinic", "eob_claim_num": "ABC123", "doctor_name": "Dr. Smith"}, "rows": [ ... ] } '
    "Now output ONLY this JSON object with NO commentary."
)

DATE_PAT = re.compile(r"(20\d{2}|19\d{2})[-/](\d{1,2})[-/](\d{1,2})")


def _extract_text(content_type: str | None, file_bytes: bytes) -> str:
    # Try PDF first
    if (content_type and "pdf" in content_type.lower()) or (file_bytes[:5] == b"%PDF-"):
        if PdfReader:
            try:
                reader = PdfReader.from_bytes(file_bytes) if hasattr(PdfReader, "from_bytes") else PdfReader(BytesIO(file_bytes))  # type: ignore
            except Exception:
                # fallback attempt via temp file not implemented (avoid FS writes)
                reader = None
            if reader:
                texts = []
                for page in reader.pages[:6]:  # limit pages for cost control
                    try:
                        texts.append(page.extract_text() or "")
                    except Exception:
                        continue
                joined = "\n".join(texts).strip()
                if joined:
                    return joined
        # fallback basic decode subset for PDF if pypdf absent
    # Generic heuristic decoding
    for enc in ("utf-8", "latin-1"):  # minimal attempts
        try:
            return file_bytes.decode(enc)
        except Exception:
            continue
    return ""


def _init_model() -> Any | None:
    key = get_gemini_api_key(False)
    if not key or genai is None:
        return None
    try:
        genai.configure(api_key=key)  # type: ignore[attr-defined]
    except Exception:
        # configure failed for some reason
        return None

    model_name = get_gemini_model_name()
    # Try configured model first
    try:
        m = genai.GenerativeModel(model_name)  # type: ignore[attr-defined]
        _LAST_USED_MODEL_NAME = model_name  # type: ignore[name-defined]
        return m
    except Exception:
        # Attempt to find a compatible model via listing
        try:
            available = _list_models_brief() or []
            # Prefer models that look like recent 'flash' gemini variants
            preferred = [n for n in available if ("gemini" in n and "flash" in n)]
            if not preferred:
                # fallback to any model that contains 'gemini' or take first
                preferred = [n for n in available if "gemini" in n]
            candidate = None
            if preferred:
                # prefer ones containing '2.5' then 'latest'
                for p in preferred:
                    if "2.5" in p:
                        candidate = p
                        break
                if not candidate:
                    for p in preferred:
                        if "latest" in p:
                            candidate = p
                            break
                if not candidate:
                    candidate = preferred[0]
            elif available:
                candidate = available[0]

            if candidate:
                try:
                    m2 = genai.GenerativeModel(candidate)  # type: ignore[attr-defined]
                    # record last used name for diagnostics
                    _LAST_USED_MODEL_NAME = candidate  # type: ignore[name-defined]
                    return m2
                except Exception:
                    return None
        except Exception:
            return None
    return None


# Track which model name was actually used when the client creates a model
_LAST_USED_MODEL_NAME: str | None = None


def _list_models_brief() -> list[str] | None:
    """Attempt to list available models from the genai client for debugging.

    Returns a list of model names (limited) or None if unavailable.
    This is used to surface helpful diagnostics when a model name is not
    supported by the API key / project.
    """
    try:
        if genai is None:
            return None
        key = get_gemini_api_key(False)
        if not key:
            return None
        # genai.list_models returns objects; pick a sensible attribute if present
        models = genai.list_models()  # type: ignore[attr-defined]
        names: list[str] = []
        for m in getattr(models, "models", []) or models or []:
            # model entries vary by client version; attempt common fields
            name = getattr(m, "name", None) or getattr(m, "id", None) or str(m)
            if name:
                names.append(str(name))
            if len(names) >= 50:
                break
        return names
    except Exception:
        return None


def call_gemini_for_rows(text: str) -> tuple[List[Dict[str, Any]], dict]:
    meta: dict = {
        "api_key_present": bool(get_gemini_api_key(False)),
        "model_configured": False,
        "attempted": False,
        "raw_text_chars": 0,
        "reasons": [],
    }
    if not text.strip():
        meta["reasons"].append("empty_extracted_text")
        return [], meta
    model = _init_model()
    if model is None:
        meta["reasons"].append("model_init_failed_or_missing_key")
        return [], meta
    meta["model_configured"] = True
    prompt = PROMPT_TEMPLATE + "\n\nEOB TEXT BEGIN:\n" + text[:20000] + "\nEOB TEXT END"
    meta["attempted"] = True
    try:
        resp = model.generate_content(prompt)
        raw = getattr(resp, "text", None) or ""
    except Exception as ex:
        meta["reasons"].append("generation_exception")
        meta["error"] = str(ex)
        # If generation failed (404/model not found etc), attempt to list models
        try:
            available = _list_models_brief()
            if available:
                meta["available_models"] = available
        except Exception:
            pass
        return [], meta
    raw = raw.strip().strip("`")
    meta["raw_text_chars"] = len(raw)
    if get_eob_debug_preview_enabled():
        meta["raw_snippet"] = raw[:600]
    # Try to parse a JSON object containing metadata+rows first
    try:
        parsed = json.loads(raw)
        # If the model returned the full object with 'rows', extract
        if isinstance(parsed, dict) and "rows" in parsed:
            rows = parsed.get("rows") or []
            parsed_meta = parsed.get("metadata") or {}
            if isinstance(parsed_meta, dict):
                meta["parsed_metadata"] = parsed_meta
            if isinstance(rows, list):
                return rows, meta
            # If rows was not a list, fall through to trying to find any array substring
    except Exception:
        # Not a pure JSON object; fall back to scanning for a JSON array within text
        pass

    # Fallback: find the first JSON array in the response
    first = raw.find("[")
    last = raw.rfind("]")
    if first == -1 or last == -1 or last < first:
        meta["reasons"].append("no_json_array_found")
        return [], meta
    json_str = raw[first : last + 1]
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return data, meta
        meta["reasons"].append("json_not_list")
        return [], meta
    except Exception as ex:
        meta["reasons"].append("json_parse_error")
        meta["error"] = str(ex)
        return [], meta


def normalize_rows(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in raw_rows:

        def g(k):
            return r.get(k)

        svc = g("service_date")
        svc_date = None
        if isinstance(svc, str) and svc.strip():
            m = DATE_PAT.search(svc)
            if m:
                y, mth, d = m.group(1), m.group(2), m.group(3)
                try:
                    svc_date = date(int(y), int(mth), int(d))
                except Exception:
                    svc_date = None

        def to_f(v):
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v.replace("$", "").replace(",", "").strip())
                except Exception:
                    return None
            return None

        out.append(
            {
                "service_date": svc_date.isoformat() if svc_date else None,
                "cpt_code": (g("cpt_code") or None)
                if isinstance(g("cpt_code"), str)
                else None,
                "description": (g("description") or "")[:160],
                "charged_amount": to_f(g("charged_amount")),
                "owed_amount": to_f(g("owed_amount")),
                "provider_name": g("provider_name")
                if isinstance(g("provider_name"), str)
                else None,
                "family_member": g("family_member")
                if isinstance(g("family_member"), str)
                else None,
                "insurance_paid_amount": to_f(g("insurance_paid_amount")),
                "insurance_discount_amount": to_f(g("insurance_discount_amount")),
                "base_fee_amount": to_f(g("base_fee_amount")),
                "reimbursement_amount": to_f(g("reimbursement_amount")),
            }
        )
    return out


def parse_eob_bytes(
    filename: str, file_bytes: bytes, content_type: str | None
) -> tuple[List[Dict[str, Any]], dict]:
    model_name = get_gemini_model_name()
    meta: dict = {
        "model": model_name,
        "bytes": len(file_bytes),
        "content_type": content_type,
    }
    rows: List[Dict[str, Any]] = []
    # Attempt file upload path if enabled
    upload_enabled = get_eob_use_file_upload()
    meta["upload_enabled"] = upload_enabled
    if (
        upload_enabled
        and genai is not None
        and content_type
        and "pdf" in content_type.lower()
    ):
        meta["upload_used"] = True
        try:
            # Upload file (Gemini API: may vary by version; using generic method name)
            file_obj = genai.upload_file({"file_name": filename, "data": file_bytes})  # type: ignore[attr-defined]
            meta["upload_file_id"] = getattr(file_obj, "name", None) or getattr(
                file_obj, "id", None
            )
            prompt = (
                "You are an expert at reading medical Explanation of Benefits (EOB) PDFs. "
                "Return ONLY a single JSON OBJECT with two keys: 'metadata' and 'rows'. 'metadata' should be an object with possible keys: family_member (patient name string or null), provider_name (string or null), eob_claim_num (string or null), doctor_name (string or null). 'rows' should be an array where each element is an object with keys: service_date, cpt_code, description, charged_amount, owed_amount, provider_name, family_member, insurance_paid_amount, insurance_discount_amount, base_fee_amount, reimbursement_amount. "
                "Use null where unknown. Map any fields labeled 'Discount', 'Discounts', 'Disallowed', or 'Disallowed Amount' to insurance_discount_amount. Map 'Billed' or 'Billed Charges' to base_fee_amount and 'Allowed'/'Covered'/'Allowed Amount' to charged_amount. No commentary outside JSON."
            )
            try:
                resp = genai.GenerativeModel(model_name).generate_content([file_obj, prompt])  # type: ignore[attr-defined]
                raw_text = getattr(resp, "text", "") or ""
                meta["upload_raw_text_chars"] = len(raw_text)
                if get_eob_debug_preview_enabled():
                    meta["upload_raw_snippet"] = raw_text[:600]
                raw_text = raw_text.strip().strip("`")
                # First try: full JSON object with metadata + rows
                try:
                    parsed = json.loads(raw_text)
                    if isinstance(parsed, dict) and "rows" in parsed:
                        parsed_meta = parsed.get("metadata") or {}
                        if isinstance(parsed_meta, dict):
                            meta["parsed_metadata"] = parsed_meta
                        parsed_rows = parsed.get("rows") or []
                        if isinstance(parsed_rows, list):
                            rows = parsed_rows
                            meta["response_mode"] = "file_upload"
                    else:
                        # Fallback: try to find any JSON array in the text
                        first = raw_text.find("[")
                        last = raw_text.rfind("]")
                        if first != -1 and last != -1 and last > first:
                            json_str = raw_text[first : last + 1]
                            try:
                                data = json.loads(json_str)
                                if isinstance(data, list):
                                    rows = data
                                    meta["response_mode"] = "file_upload"
                            except Exception as ex_json:
                                meta.setdefault("reasons", []).append(
                                    "upload_json_parse_error"
                                )
                                meta["upload_error"] = str(ex_json)
                        else:
                            meta.setdefault("reasons", []).append(
                                "upload_no_json_array"
                            )
                except Exception as ex_outer:
                    # If top-level parse fails, attempt to salvage an array substring
                    first = raw_text.find("[")
                    last = raw_text.rfind("]")
                    if first != -1 and last != -1 and last > first:
                        json_str = raw_text[first : last + 1]
                        try:
                            data = json.loads(json_str)
                            if isinstance(data, list):
                                rows = data
                                meta["response_mode"] = "file_upload"
                        except Exception as ex_json:
                            meta.setdefault("reasons", []).append(
                                "upload_json_parse_error"
                            )
                            meta["upload_error"] = str(ex_json)
                    else:
                        meta.setdefault("reasons", []).append("upload_parse_exception")
                        meta["upload_error"] = str(ex_outer)
            except Exception as gen_ex:
                meta.setdefault("reasons", []).append("upload_generation_exception")
                meta["upload_error"] = str(gen_ex)
                try:
                    available = _list_models_brief()
                    if available:
                        meta["available_models"] = available
                except Exception:
                    pass
        except Exception as up_ex:
            meta.setdefault("reasons", []).append("upload_exception")
            meta["upload_error"] = str(up_ex)
    else:
        meta["upload_used"] = False
    # If no rows from upload (or not used), fallback to text extraction
    if not rows:
        text = _extract_text(content_type, file_bytes)
        meta["extracted_text_chars"] = len(text)
        meta["empty_text"] = len(text.strip()) == 0
        rows_text, gen_meta = call_gemini_for_rows(text)
        # If the helper tracked which model was actually created, include it
        try:
            from .eob_parser import _LAST_USED_MODEL_NAME as _used  # type: ignore

            if _used:
                gen_meta = gen_meta or {}
                gen_meta["actual_model_used"] = _used
        except Exception:
            pass
        meta.update(gen_meta)
        if rows_text:
            rows = rows_text
            meta["response_mode"] = meta.get("response_mode", "text_extraction")
        else:
            # Distinguish model returning [] explicitly
            if meta.get("raw_text_chars") == 2 and meta.get("raw_snippet", "") in (
                "[]",
                "[ ]",
            ):
                meta.setdefault("reasons", []).append("model_returned_empty_array")
    if not rows:
        meta["status"] = "no_rows"
        return [], meta
    normalized = normalize_rows(rows)
    meta["status"] = "ok"
    meta["row_count"] = len(normalized)
    return normalized, meta
