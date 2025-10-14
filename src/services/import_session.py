"""Session management for JSON/FHIR import previews (and EOB if desired)."""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Dict

from pydantic import BaseModel


class JSONImportRow(BaseModel):
    """Row representation for a candidate charge parsed from JSON / FHIR.

    This mirrors the original structure but is split out so we can run a
    provider-only analyze step first, then later generate these rows once
    providers have optionally been inserted and a family member / medical type
    have been chosen.
    """

    index: int
    service_date: str | None = None
    description: str | None = None
    cpt_code: str | None = None
    charged_amount: float | int | None = None
    owed_amount: float | int | None = None
    base_fee_amount: float | int | None = None
    insurance_discount_amount: float | int | None = None
    insurance_paid_amount: float | int | None = None
    doctor_name: str | None = None
    eob_claim_num: str | None = None
    facility_location_id: str | None = None
    facility_location_name: str | None = None
    duplicate: bool = False
    resolved_provider_id: int | None = None  # Removed underscore prefix


class JSONImportPreview(BaseModel):
    session_id: str
    total_parsed: int
    duplicate_count: int
    rows: list[JSONImportRow]
    parse_mode: str | None = None
    source_mode: str | None = None
    providers: list[dict] | None = None
    provider_duplicate_count: int | None = None


class JSONProviderAnalyzeResponse(BaseModel):
    """Response model for the provider-only analyze step.

    We keep it intentionally simple: just the candidate providers and counts.
    """

    session_id: str
    total_candidates: int
    existing_count: int
    new_count: int
    providers: list[dict]
    parse_mode: str | None = None
    source_mode: str | None = None


class JSONChargeAnalyzeResponse(BaseModel):
    """Response model for the charge analyze step performed after providers are added."""

    session_id: str
    total_parsed: int
    duplicate_count: int
    rows: list[JSONImportRow]
    family_member: str | None = None
    medical_type: str | None = None
    parse_mode: str | None = None
    source_mode: str | None = None


class JSONImportCommitRequest(BaseModel):
    selected_indices: list[int]
    provider_id: int | None = None
    family_member: str | None = None
    medical_type: str | None = None
    selected_provider_location_ids: list[str] | None = None


class JSONImportCommitResult(BaseModel):
    inserted_count: int
    inserted_ids: list[int]
    skipped_duplicates: int
    session_id: str


class ImportSessionStore:
    def __init__(self):
        self._sessions: Dict[str, dict] = {}

    def create(self, data: dict) -> str:
        sid = uuid.uuid4().hex
        self._sessions[sid] = {**data, "created_at": _dt.datetime.utcnow()}
        return sid

    def get(self, session_id: str) -> dict | None:
        return self._sessions.get(session_id)

    def update(self, session_id: str, data: dict) -> None:
        """Update an existing session with new data."""
        if session_id in self._sessions:
            self._sessions[session_id].update(data)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def cleanup_expired(self, max_age_minutes: int = 120) -> int:
        cutoff = _dt.datetime.utcnow() - _dt.timedelta(minutes=max_age_minutes)
        to_delete = []
        for sid, meta in self._sessions.items():
            created_at = meta.get("created_at")
            if created_at and created_at < cutoff:
                to_delete.append(sid)
        for sid in to_delete:
            self._sessions.pop(sid, None)
        return len(to_delete)


# Global singleton store (simple in-memory)
json_import_sessions = ImportSessionStore()

__all__ = [
    "JSONImportRow",
    "JSONImportPreview",
    "JSONProviderAnalyzeResponse",
    "JSONChargeAnalyzeResponse",
    "JSONImportCommitRequest",
    "JSONImportCommitResult",
    "json_import_sessions",
]
