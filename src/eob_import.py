"""In-memory EOB import session handling (stub implementation).

Generates mock parsed charge rows from an uploaded EOB file. Each session is
stored in-process; sessions are lost on restart (acceptable for MVP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any
from uuid import uuid4
from datetime import date, timedelta
import random


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
            "service_date": self.service_date.isoformat() if self.service_date else None,
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

    def serialize(self) -> Dict[str, Any]:
        return {
            "session_id": self.id,
            "filename": self.filename,
            "row_count": len(self.rows),
            "rows": [r.to_dict() for r in self.rows],
        }


_SESSIONS: Dict[str, EOBImportSession] = {}

FAMILY_MEMBERS = ["Alex", "Jordan", "Sam", "Riley"]
CPT_POOL = ["99213", "99397", "90471", "87635", "83036", "97110", "92557"]
DESC_SNIPPETS = [
    "Office visit",
    "Preventive exam",
    "Lab panel",
    "Therapy session",
    "Hearing test",
    "Vaccination admin",
    "Physical therapy",
]


def _mock_parse(filename: str, file_bytes: bytes) -> List[ParsedChargeRow]:
    random.seed(filename + str(len(file_bytes)))
    today = date.today()
    rows: List[ParsedChargeRow] = []
    for i in range(1, 6):
        svc_date = today - timedelta(days=random.randint(5, 120))
        charged = round(random.uniform(80, 400), 2)
        owed = round(charged * random.uniform(0.2, 0.8), 2)
        insurance_paid = round(owed * random.uniform(0.1, 0.6), 2)
        discount = round(charged - owed - insurance_paid, 2)
        base_fee = charged  # assume charged is base for stub
        reimbursement = round(owed * random.uniform(0.0, 0.3), 2)
        row = ParsedChargeRow(
            index=i - 1,
            service_date=svc_date,
            cpt_code=random.choice(CPT_POOL),
            description=random.choice(DESC_SNIPPETS),
            charged_amount=charged,
            owed_amount=owed,
            provider_name="Auto Parsed Provider",
            family_member=random.choice(FAMILY_MEMBERS),
            insurance_paid_amount=insurance_paid,
            insurance_discount_amount=discount,
            base_fee_amount=base_fee,
            reimbursement_amount=reimbursement,
        )
        rows.append(row)
    return rows


def create_session(filename: str, file_bytes: bytes, content_type: str | None = None) -> EOBImportSession:
    session_id = uuid4().hex
    session = EOBImportSession(id=session_id, filename=filename)
    session.rows = _mock_parse(filename, file_bytes)
    session.file_bytes = file_bytes
    session.content_type = content_type
    _SESSIONS[session_id] = session
    return session


def get_session(session_id: str) -> EOBImportSession | None:
    return _SESSIONS.get(session_id)


def delete_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)
