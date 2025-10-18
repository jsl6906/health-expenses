from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

try:
    from models.database import Attachment  # type: ignore
    from schemas.schemas import AttachmentCreate  # type: ignore
except ImportError:  # pragma: no cover fallback
    from ..models.database import Attachment  # type: ignore
    from ..schemas.schemas import AttachmentCreate  # type: ignore


def create_attachment(
    db: Session,
    data: AttachmentCreate,
    original_filename: str,
    content_type: str,
    file_size: int,
    content_bytes: bytes,
    user: str = "system",
) -> Attachment:
    # Enforce that exactly one of charge_id or payment_id is set.
    if bool(data.charge_id) == bool(data.payment_id):
        raise ValueError("Exactly one of charge_id or payment_id must be provided")

    from sqlalchemy import func

    att = Attachment(
        charge_id=data.charge_id,
        payment_id=data.payment_id,
        original_filename=original_filename,
        content_type=content_type,
        file_size=file_size,
        content=content_bytes,
        notes=data.notes,
        created_at=func.now(),
        updated_at=func.now(),
        created_by=user,
        updated_by=user,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return att


def list_attachments_for_charge(db: Session, charge_id: int) -> List[Attachment]:
    stmt = (
        select(Attachment)
        .where(Attachment.charge_id == charge_id)
        .order_by(Attachment.id.desc())
    )
    return list(db.scalars(stmt))


def list_attachments_for_payment(db: Session, payment_id: int) -> List[Attachment]:
    stmt = (
        select(Attachment)
        .where(Attachment.payment_id == payment_id)
        .order_by(Attachment.id.desc())
    )
    return list(db.scalars(stmt))


def get_attachment(db: Session, attachment_id: int) -> Optional[Attachment]:
    return db.get(Attachment, attachment_id)


def delete_attachment(db: Session, attachment_id: int) -> Optional[Attachment]:
    att = db.get(Attachment, attachment_id)
    if att is None:
        return None
    db.delete(att)
    db.commit()
    return att
