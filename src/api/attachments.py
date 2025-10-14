from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from crud.attachment import (
    create_attachment,
    delete_attachment,
    get_attachment,
    list_attachments_for_charge,
    list_attachments_for_payment,
)
from database_config import get_db
from schemas.schemas import Attachment

router = APIRouter(tags=["attachments"], prefix="/api")


@router.post("/attachments/upload", response_model=Attachment)
async def upload_attachment(
    charge_id: int | None = Form(None),
    payment_id: int | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if (charge_id is None and payment_id is None) or (
        charge_id is not None and payment_id is not None
    ):
        raise HTTPException(
            status_code=400, detail="Provide exactly one of charge_id or payment_id"
        )
    try:
        content_bytes = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to read uploaded file: {e}"
        )
    from schemas.schemas import (
        AttachmentCreate as _AttachmentCreate,  # late import to avoid circular
    )

    att_in = _AttachmentCreate(charge_id=charge_id, payment_id=payment_id, notes=notes)
    try:
        att = create_attachment(
            db,
            data=att_in,
            original_filename=file.filename or "uploaded_file",
            content_type=file.content_type or "application/octet-stream",
            file_size=len(content_bytes),
            content_bytes=content_bytes,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return att


@router.get("/charges/{charge_id}/attachments", response_model=list[Attachment])
def list_charge_attachments(charge_id: int, db: Session = Depends(get_db)):
    return list_attachments_for_charge(db, charge_id)


@router.get("/payments/{payment_id}/attachments", response_model=list[Attachment])
def list_payment_attachments(payment_id: int, db: Session = Depends(get_db)):
    return list_attachments_for_payment(db, payment_id)


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    att = get_attachment(db, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    media_type = getattr(att, "content_type", None) or "application/octet-stream"
    download_name = (
        getattr(att, "original_filename", None) or f"attachment_{attachment_id}"
    )
    data = getattr(att, "content", None)
    if data is None:
        raise HTTPException(status_code=500, detail="Attachment content missing")
    return StreamingResponse(
        BytesIO(data),
        media_type=str(media_type),
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.delete("/attachments/{attachment_id}")
def remove_attachment(attachment_id: int, db: Session = Depends(get_db)):
    att = get_attachment(db, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    delete_attachment(db, attachment_id)
    return {"message": "Attachment deleted"}


@router.get("/attachments/list")
def list_attachments_endpoint(
    charge_id: int | None = Query(None),
    payment_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    if bool(charge_id) == bool(payment_id):
        raise HTTPException(
            status_code=400, detail="Provide exactly one of charge_id or payment_id"
        )
    try:
        if charge_id is not None:
            items = list_attachments_for_charge(db, charge_id)
        else:
            items = list_attachments_for_payment(db, payment_id)  # type: ignore[arg-type]
        return {
            "attachments": [
                {
                    "id": a.id,
                    "original_filename": a.original_filename,
                    "file_size": a.file_size,
                    "notes": a.notes,
                    "charge_id": a.charge_id,
                    "payment_id": a.payment_id,
                }
                for a in items
            ]
        }
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Error listing attachments: {ex}")
