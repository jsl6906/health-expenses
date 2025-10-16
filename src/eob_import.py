"""In-memory EOB import session handling (stub implementation).

Generates mock parsed charge rows from an uploaded EOB file. Each session is
stored in-process; sessions are lost on restart (acceptable for MVP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List
from uuid import uuid4

_LLM_IMPORT_ERROR: str | None = None
_llm_parse = None
try:
    from .llm.eob_parser import parse_eob_bytes as _llm_parse  # type: ignore
except Exception as ex1:  # relative import failed
    _LLM_IMPORT_ERROR = f"relative_import_failed: {ex1}"
    try:
        from llm.eob_parser import parse_eob_bytes as _llm_parse  # type: ignore
    except Exception as ex2:
        if _LLM_IMPORT_ERROR:
            _LLM_IMPORT_ERROR += f" | abs_import_failed: {ex2}"
        else:
            _LLM_IMPORT_ERROR = f"abs_import_failed: {ex2}"
        try:
            # Sometimes running via 'python -m src.app' means modules are under 'src'
            from src.llm.eob_parser import parse_eob_bytes as _llm_parse  # type: ignore
        except Exception as ex3:  # pragma: no cover
            if _LLM_IMPORT_ERROR:
                _LLM_IMPORT_ERROR += f" | src_import_failed: {ex3}"
            else:
                _LLM_IMPORT_ERROR = f"src_import_failed: {ex3}"


@dataclass
class ParsedChargeRow:
    index: int
    service_date: date | None
    cpt_code: str | None
    description: str
    charged_amount: float | None
    owed_amount: float | None
    provider_name: str | None
    family_member: str | None = None
    insurance_paid_amount: float | None = None
    insurance_discount_amount: float | None = None
    base_fee_amount: float | None = None
    reimbursement_amount: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "service_date": self.service_date.isoformat()
            if self.service_date
            else None,
            "cpt_code": self.cpt_code,
            "description": self.description,
            "charged_amount": self.charged_amount,
            "owed_amount": self.owed_amount,
            "provider_name": self.provider_name,
            "family_member": self.family_member,
            "insurance_paid_amount": self.insurance_paid_amount,
            "insurance_discount_amount": self.insurance_discount_amount,
            "base_fee_amount": self.base_fee_amount,
            "reimbursement_amount": self.reimbursement_amount,
        }


@dataclass
class EOBImportSession:
    id: str
    filename: str
    rows: List[ParsedChargeRow] = field(default_factory=list)
    file_bytes: bytes | None = None
    content_type: str | None = None
    debug_info: Dict[str, Any] | None = None
    # Optional top-level metadata captured from the upload form
    family_member: str | None = None
    provider_id: int | None = None
    doctor_name: str | None = None
    eob_number: str | None = None
    medical_type: str | None = None

    def serialize(self) -> Dict[str, Any]:
        return {
            "session_id": self.id,
            "filename": self.filename,
            "family_member": self.family_member,
            "provider_id": self.provider_id,
            "medical_type": self.medical_type,
            "doctor_name": self.doctor_name,
            "eob_number": self.eob_number,
            "row_count": len(self.rows),
            "rows": [r.to_dict() for r in self.rows],
            "debug": self.debug_info or {},
        }


_SESSIONS: Dict[str, EOBImportSession] = {}


def create_session(
    filename: str, file_bytes: bytes, content_type: str | None = None
) -> EOBImportSession:
    session_id = uuid4().hex
    session = EOBImportSession(id=session_id, filename=filename)
    parsed_rows: list[ParsedChargeRow] = []
    # Attempt LLM parsing first
    if _llm_parse is not None:
        meta: Dict[str, Any] | None = None
        try:
            llm_rows, meta = _llm_parse(filename, file_bytes, content_type)  # type: ignore[misc]
        except Exception as ex:  # capture failure reason
            llm_rows = []  # type: ignore[assignment]
            meta = {"status": "error", "error": str(ex)}
        # Convert normalized dict rows into ParsedChargeRow instances
        if isinstance(meta, dict):
            session.debug_info = meta
        # Persist top-level metadata if returned by the LLM (meta may include parsed_metadata)
        try:
            parsed_meta = None
            if isinstance(meta, dict):
                parsed_meta = meta.get("parsed_metadata") or {}
            # Some older responses may include metadata inside the first dict under key 'metadata'
            if not parsed_meta and isinstance(llm_rows, list) and len(llm_rows) == 0:
                parsed_meta = {}
            if isinstance(parsed_meta, dict):
                fm = parsed_meta.get("family_member") or parsed_meta.get("patient_name")
                if fm:
                    session.family_member = fm
                dn = parsed_meta.get("doctor_name")
                if dn:
                    session.doctor_name = dn
                en = parsed_meta.get("eob_claim_num") or parsed_meta.get("eob_number")
                if en:
                    session.eob_number = en
                # provider_name can't be reliably mapped to provider_id here (no DB access), but store provider_name in debug_info
                prov_name = parsed_meta.get("provider_name")
                if prov_name:
                    session.debug_info = session.debug_info or {}
                    session.debug_info["parsed_provider_name"] = prov_name
        except Exception:
            pass
        for idx, r in enumerate(llm_rows or []):  # type: ignore[union-attr]
            try:
                svc_date = None
                svc = r.get("service_date")
                if svc:
                    from datetime import date as _d

                    parts = str(svc).split("-")
                    if len(parts) == 3:
                        try:
                            svc_date = _d(int(parts[0]), int(parts[1]), int(parts[2]))
                        except Exception:
                            svc_date = None
                parsed_rows.append(
                    ParsedChargeRow(
                        index=idx,
                        service_date=svc_date,
                        cpt_code=r.get("cpt_code"),
                        description=r.get("description") or "(No description)",
                        charged_amount=r.get("charged_amount"),
                        owed_amount=r.get("owed_amount"),
                        provider_name=r.get("provider_name"),
                        family_member=r.get("family_member"),
                        insurance_paid_amount=r.get("insurance_paid_amount"),
                        insurance_discount_amount=r.get("insurance_discount_amount"),
                        base_fee_amount=r.get("base_fee_amount"),
                        reimbursement_amount=r.get("reimbursement_amount"),
                    )
                )
            except Exception:
                continue
    else:
        # Parser module not importable, surface reason & details to UI
        session.debug_info = {
            "status": "parser_unavailable",
            "reasons": ["import_error"],
            "message": "LLM parser module could not be imported",
            "import_error_detail": _LLM_IMPORT_ERROR,
        }
    # If no rows parsed, leave session empty (UI can display zero rows)
    session.rows = parsed_rows
    session.file_bytes = file_bytes
    session.content_type = content_type
    _SESSIONS[session_id] = session
    return session


def get_session(session_id: str) -> EOBImportSession | None:
    return _SESSIONS.get(session_id)


def delete_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)
