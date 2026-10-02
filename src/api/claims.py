"""Insurance claim form endpoints: prefill, PDF rendering and email submission."""

from __future__ import annotations

import logging
from datetime import date
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from ..database_config import get_db
from ..models.database import Attachment, Charge, FamilyMember
from ..schemas.claims import (
    ClaimBatchPdfRequest,
    ClaimEmailRequest,
    ClaimEmailResponse,
    ClaimForm,
)
from ..services.claim_form import build_claim_forms, claim_warnings
from ..services.claim_pdf import (
    claim_pdf_filename,
    claims_pdf_filename,
    render_claim_pdf,
    render_claims_pdf,
)
from ..services.mailer import (
    MailConfigError,
    default_claim_cc,
    mail_config_ok,
    send_email_with_attachment,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["claims"], prefix="/api")

ALLOWED_SIGNATURE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


def _signature_bytes(db: Session, family_member_id: int | None) -> bytes | None:
    if family_member_id is None:
        return None
    fm = db.query(FamilyMember).filter(FamilyMember.id == family_member_id).first()
    return fm.signature_image if fm is not None else None


def _signatures_for(db: Session, forms: list[ClaimForm]) -> dict[int | None, bytes | None]:
    ids = {f.authorization.signer_family_member_id for f in forms}
    return {i: _signature_bytes(db, i) for i in ids}


def _pdf_response(pdf: bytes, filename: str, inline: bool) -> StreamingResponse:
    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        BytesIO(pdf),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"{disposition}; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        },
    )


@router.get("/claims/prefill", response_model=list[ClaimForm])
def prefill_claims(charge_ids: str, db: Session = Depends(get_db)):
    """Build prefilled claim forms (one per insurance/patient/provider) for the given charges."""
    try:
        ids = [int(x) for x in charge_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid charge_ids")
    forms = build_claim_forms(db, ids)
    if not forms:
        raise HTTPException(status_code=404, detail="No charges found")
    return forms


@router.get("/claims/warnings")
def claims_warnings(charge_ids: str, db: Session = Depends(get_db)):
    try:
        ids = [int(x) for x in charge_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid charge_ids")
    out = []
    for f in build_claim_forms(db, ids):
        sig = _signature_bytes(db, f.authorization.signer_family_member_id)
        out.append({"claim_key": f.claim_key, "warnings": claim_warnings(f, sig is not None)})
    return out


@router.post("/claims/pdf")
def claim_pdf(form: ClaimForm, inline: bool = True, db: Session = Depends(get_db)):
    """Render the submitted claim form to PDF."""
    sig = _signature_bytes(db, form.authorization.signer_family_member_id)
    return _pdf_response(render_claim_pdf(form, sig), claim_pdf_filename(form), inline)


@router.post("/claims/pdf-batch")
def claims_pdf_batch(
    req: ClaimBatchPdfRequest, inline: bool = True, db: Session = Depends(get_db)
):
    """Render several claim forms (e.g. one per provider) into a single PDF, in sequence."""
    if not req.claims:
        raise HTTPException(status_code=400, detail="No claims provided")
    pdf = render_claims_pdf(req.claims, _signatures_for(db, req.claims))
    return _pdf_response(pdf, claims_pdf_filename(req.claims), inline)


@router.get("/claims/mail-status")
def claim_mail_status():
    return {"configured": mail_config_ok(), "default_cc": default_claim_cc() or None}


@router.post("/claims/email", response_model=ClaimEmailResponse)
def email_claim(req: ClaimEmailRequest, db: Session = Depends(get_db)):
    """Render one or more claims into a single PDF, email it, and optionally mark the charges as submitted."""
    forms = req.all_claims()
    if not forms:
        raise HTTPException(status_code=400, detail="No claims provided")
    form = forms[0]
    # cc omitted (None) -> configured default; empty string -> explicitly no CC
    cc = default_claim_cc() if req.cc is None else req.cc.strip()
    pdf = render_claims_pdf(forms, _signatures_for(db, forms))
    filename = claims_pdf_filename(forms)
    all_charge_ids = [cid for f in forms for cid in f.charge_ids]

    def _patient(f: ClaimForm) -> str:
        return f"{f.patient.first_name or f.member.first_name or ''} {f.patient.last_name or f.member.last_name or ''}".strip()

    member_line = (
        f"Member: {form.member.first_name or ''} {form.member.last_name or ''}\n"
        f"Member ID: {form.member.member_id or ''}\n"
    )
    if len(forms) == 1:
        subject = req.subject or (
            f"Medical Claim Form - {_patient(form)} - Member ID {form.member.member_id or ''}".strip(" -")
        )
        body = req.body or (
            "Please find the attached medical claim form for processing.\n\n"
            + member_line
            + f"Patient: {_patient(form)}\n"
            f"Provider: {form.provider_statement.provider_name or ''}\n"
            f"Total charges: ${form.provider_statement.total_charge or 0:,.2f}\n\n"
            "Thank you."
        )
    else:
        subject = req.subject or (
            f"Medical Claim Forms ({len(forms)}) - Member ID {form.member.member_id or ''}".strip(" -")
        )
        lines = [
            f"  {i}. {_patient(f)} - {f.provider_statement.provider_name or ''} - ${f.provider_statement.total_charge or 0:,.2f}"
            for i, f in enumerate(forms, 1)
        ]
        body = req.body or (
            f"Please find the attached {len(forms)} medical claim forms for processing (one per provider/patient).\n\n"
            + member_line
            + "Claims included:\n"
            + "\n".join(lines)
            + "\n\nThank you."
        )

    extras: list[tuple[str, bytes, str]] = []
    if req.attachment_ids:
        # Only allow attachments that belong to the charges on these claims
        rows = (
            db.query(Attachment)
            .filter(
                Attachment.id.in_(req.attachment_ids),
                Attachment.charge_id.in_(all_charge_ids or [-1]),
            )
            .all()
        )
        found = {a.id for a in rows}
        missing = [i for i in req.attachment_ids if i not in found]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Attachments not found on this claim's charges: {missing}",
            )
        for a in rows:
            if a.content:
                extras.append(
                    (
                        a.original_filename or f"attachment_{a.id}",
                        a.content,
                        a.content_type or "application/octet-stream",
                    )
                )

    try:
        send_email_with_attachment(
            to=req.to,
            cc=cc or None,
            subject=subject,
            body=body,
            attachment_bytes=pdf,
            attachment_filename=filename,
            extra_attachments=extras,
        )
    except MailConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # SMTP/network failures
        logger.exception("Failed to send claim email")
        raise HTTPException(status_code=502, detail=f"Email send failed: {e}")

    marked = 0
    if req.mark_submitted and all_charge_ids:
        submitted = req.submitted_date or date.today()
        marked = (
            db.query(Charge)
            .filter(Charge.id.in_(all_charge_ids))
            .update({Charge.claim_submitted_date: submitted}, synchronize_session=False)
        )
        db.commit()

    return ClaimEmailResponse(
        sent=True,
        to=req.to,
        cc=cc or None,
        subject=subject,
        filename=filename,
        charges_marked=int(marked or 0),
        attachments_included=[name for name, _, _ in extras],
        claims_included=len(forms),
    )


# ---- Family member signature image ----


@router.post("/family_members/{family_member_id}/signature")
async def upload_signature(
    family_member_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    fm = db.query(FamilyMember).filter(FamilyMember.id == family_member_id).first()
    if fm is None:
        raise HTTPException(status_code=404, detail="Family member not found")
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_SIGNATURE_TYPES:
        raise HTTPException(status_code=400, detail="Signature must be a PNG, JPEG, GIF or WebP image")
    data = await file.read()
    if len(data) > MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=400, detail="Signature image must be under 2 MB")
    fm.signature_image = data
    fm.signature_content_type = content_type
    db.commit()
    return {"family_member_id": family_member_id, "has_signature": True, "size": len(data)}


@router.get("/family_members/{family_member_id}/signature")
def get_signature(family_member_id: int, db: Session = Depends(get_db)):
    fm = db.query(FamilyMember).filter(FamilyMember.id == family_member_id).first()
    if fm is None or not fm.signature_image:
        raise HTTPException(status_code=404, detail="No signature on file")
    return Response(
        content=fm.signature_image,
        media_type=fm.signature_content_type or "image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/family_members/{family_member_id}/signature")
def delete_signature(family_member_id: int, db: Session = Depends(get_db)):
    fm = db.query(FamilyMember).filter(FamilyMember.id == family_member_id).first()
    if fm is None:
        raise HTTPException(status_code=404, detail="Family member not found")
    fm.signature_image = None
    fm.signature_content_type = None
    db.commit()
    return {"family_member_id": family_member_id, "has_signature": False}
