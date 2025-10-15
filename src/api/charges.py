import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..crud.attachment import create_attachment  # needed for bulk attach
from ..crud.charge import charge_crud
from ..database_config import get_db
from ..schemas.schemas import Charge, ChargeCreate, ChargeUpdate

router = APIRouter(tags=["charges"], prefix="/api")

# Page endpoints moved to pages router


@router.post("/charges/bulk-create")
async def bulk_create_charges(
    request: Request,
    service_date: str = Form(...),
    family_member: str = Form(...),
    medical_type: str = Form(...),
    provider_id: int = Form(...),
    doctor_name: str | None = Form(None),
    eob_claim_num: str | None = Form(None),
    rows_json: str = Form(...),
    attachment: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    svc_date_obj = None
    if service_date:
        try:
            svc_date_obj = datetime.strptime(service_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid service_date format. Use YYYY-MM-DD"
            )
    try:
        rows = json.loads(rows_json)
        if not isinstance(rows, list):
            raise ValueError
    except Exception:
        raise HTTPException(
            status_code=400, detail="rows_json must be a JSON array of row objects"
        )
    if not rows:
        raise HTTPException(status_code=400, detail="At least one row is required")

    created_ids: list[int] = []
    highest_charge_id: int | None = None
    highest_amount: float = -1.0

    def _num(val):
        try:
            if val in (None, ""):
                return None
            return float(val)
        except Exception:
            return None

    for r in rows:
        if not isinstance(r, dict):
            continue
        desc = (r.get("description") or r.get("charge_description") or "").strip()
        if not desc:
            amt_fields = [
                r.get("charged_amount"),
                r.get("owed_amount"),
                r.get("base_fee_amount"),
            ]
            if not any(amt_fields):
                continue
        cpt_code = r.get("cpt_code") or r.get("cpt") or None
        charge_in = {
            "service_date": svc_date_obj.isoformat() if svc_date_obj else None,
            "family_member": family_member,
            "medical_type": medical_type,
            "provider_id": provider_id,
            "doctor_name": doctor_name,
            "eob_claim_num": eob_claim_num,
            "cpt_code": cpt_code,
            "charge_description": desc,
            "charged_amount": _num(r.get("charged_amount")),
            "owed_amount": _num(r.get("owed_amount")),
            "base_fee_amount": _num(r.get("base_fee_amount")),
            "insurance_paid_amount": _num(r.get("insurance_paid_amount")),
            "insurance_discount_amount": _num(r.get("insurance_discount_amount")),
            "reimbursement_amount": _num(r.get("reimbursement_amount")),
            "notes": None,
        }
        try:
            created = charge_crud.create(db, charge_in)
            db.flush()
            cid = int(getattr(created, "id"))
            created_ids.append(cid)
            amt_for_compare = (
                charge_in["charged_amount"]
                if charge_in["charged_amount"] is not None
                else 0.0
            )
            if amt_for_compare > highest_amount:
                highest_amount = amt_for_compare
                highest_charge_id = cid
        except Exception:
            continue
    db.commit()
    if not created_ids:
        raise HTTPException(status_code=400, detail="No valid charge rows were created")

    attached_id = None
    if attachment and highest_charge_id is not None:
        try:
            file_bytes = await attachment.read()
            from schemas.schemas import (
                AttachmentCreate as _AttachmentCreate,  # type: ignore
            )

            att_in = _AttachmentCreate(
                charge_id=highest_charge_id,
                payment_id=None,
                notes="Attachment for batch (highest charge)",
            )
            create_attachment(
                db,
                data=att_in,
                original_filename=attachment.filename or "attachment",
                content_type=attachment.content_type or "application/octet-stream",
                file_size=len(file_bytes),
                content_bytes=file_bytes,
            )
            db.commit()
            attached_id = highest_charge_id
        except Exception:
            pass

    return {
        "created_count": len(created_ids),
        "created_ids": created_ids,
        "attachment_applied_to": attached_id,
    }


@router.post("/charges/", response_model=Charge)
def create_charge(charge: ChargeCreate, db: Session = Depends(get_db)):
    data = charge.model_dump()
    if data.get("family_member") and hasattr(data["family_member"], "value"):
        data["family_member"] = data["family_member"].value
    if data.get("medical_type") and hasattr(data["medical_type"], "value"):
        data["medical_type"] = data["medical_type"].value
    cpt = data.get("cpt_code")
    if cpt is not None:
        cpt_norm = cpt.strip()
        if not cpt_norm or cpt_norm.lower() in {"???", "na", "n/a"}:
            data["cpt_code"] = None
        else:
            data["cpt_code"] = cpt_norm
    return charge_crud.create(db, data)


@router.get("/charges/", response_model=List[Charge])
def read_charges(
    skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)
):
    return charge_crud.get_all(db, skip=skip, limit=limit)


@router.get("/charges/{charge_id}", response_model=Charge)
def read_charge(charge_id: int, db: Session = Depends(get_db)):
    ch = charge_crud.get(db, charge_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    return ch


@router.put("/charges/{charge_id}", response_model=Charge)
def update_charge(charge_id: int, charge: ChargeUpdate, db: Session = Depends(get_db)):
    db_charge = charge_crud.get(db, charge_id)
    if db_charge is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    data = charge.model_dump(exclude_unset=True)
    if data.get("family_member") and hasattr(data["family_member"], "value"):
        data["family_member"] = data["family_member"].value
    if data.get("medical_type") and hasattr(data["medical_type"], "value"):
        data["medical_type"] = data["medical_type"].value
    if "cpt_code" in data and data["cpt_code"] is not None:
        cpt_norm = data["cpt_code"].strip()
        if not cpt_norm or cpt_norm.lower() in {"???", "na", "n/a"}:
            data["cpt_code"] = None
        else:
            data["cpt_code"] = cpt_norm
    return charge_crud.update(db, db_charge, data)


@router.delete("/charges/{charge_id}")
def delete_charge(charge_id: int, db: Session = Depends(get_db)):
    try:
        from models.database import Attachment as _AttachmentModel  # type: ignore
        from models.database import ChargePaymentXref as _Xref
    except ImportError:
        from .models.database import Attachment as _AttachmentModel  # type: ignore
        from .models.database import ChargePaymentXref as _Xref
    ch_obj = charge_crud.get(db, charge_id)
    if ch_obj is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    try:
        db.query(_AttachmentModel).filter(
            _AttachmentModel.charge_id == charge_id
        ).delete(synchronize_session=False)
    except Exception:
        pass
    try:
        db.query(_Xref).filter(_Xref.charge_id == charge_id).delete(
            synchronize_session=False
        )
    except Exception:
        pass
    db.delete(ch_obj)
    db.commit()
    return {"message": "Charge and related attachments deleted"}


@router.get("/charges/family/{family_member}", response_model=List[Charge])
def read_charges_by_family_member(family_member: str, db: Session = Depends(get_db)):
    return charge_crud.get_by_family_member(db, family_member)


@router.get("/charges/unpaid/", response_model=List[Charge])
def read_unpaid_charges(db: Session = Depends(get_db)):
    return charge_crud.get_unpaid_charges(db)


# Apply payment to charge
class ApplyPaymentPayload(BaseModel):
    payment_id: Optional[int] = None
    payment_date: Optional[str] = None
    payment_amount: Optional[float] = None
    expense_type: Optional[str] = None
    provider_id: Optional[int] = None
    has_receipt_indicator: Optional[str] = None
    reimbursement_amount: Optional[float] = None
    notes: Optional[str] = None


@router.post("/charges/{charge_id}/apply-payment")
def apply_payment_to_charge(
    charge_id: int, payload: ApplyPaymentPayload, db: Session = Depends(get_db)
):
    try:
        from models.database import Charge as ChargeModel  # type: ignore
        from models.database import ChargePaymentXref as XrefModel
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
        from .models.database import ChargePaymentXref as XrefModel
    charge_obj = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
    if not charge_obj:
        raise HTTPException(status_code=404, detail="Charge not found")
    creating_new = payload.payment_id is None
    if creating_new:
        missing = []
        if payload.payment_amount is None:
            missing.append("payment_amount")
        if payload.provider_id is None:
            missing.append("provider_id")
        if not payload.expense_type:
            missing.append("expense_type")
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields for new payment: {', '.join(missing)}",
            )
        payment_data = {
            "payment_date": payload.payment_date,
            "payment_amount": payload.payment_amount,
            "expense_type": payload.expense_type,
            "provider_id": payload.provider_id,
            "has_receipt_indicator": payload.has_receipt_indicator,
            "reimbursement_amount": payload.reimbursement_amount,
            "notes": payload.notes,
        }
        from crud.payment import payment_crud as _payment_crud

        payment_obj = _payment_crud.create(db, payment_data)
    else:
        from models.database import Payment as PaymentModel  # type: ignore

        payment_obj = (
            db.query(PaymentModel).filter(PaymentModel.id == payload.payment_id).first()
        )
        if not payment_obj:
            raise HTTPException(status_code=404, detail="Payment not found")
    existing = (
        db.query(XrefModel)
        .filter(
            XrefModel.charge_id == charge_id, XrefModel.payment_id == payment_obj.id
        )
        .first()
    )
    if not existing:
        db.add(XrefModel(charge_id=charge_id, payment_id=payment_obj.id))
    db.commit()
    from sqlalchemy import func as _func

    from models.database import Payment as PaymentModel  # type: ignore

    paid_total = (
        db.query(_func.coalesce(_func.sum(PaymentModel.payment_amount), 0))
        .join(XrefModel, XrefModel.payment_id == PaymentModel.id)
        .filter(XrefModel.charge_id == charge_id)
        .scalar()
        or 0
    )
    return {
        "charge_id": charge_id,
        "payment_id": payment_obj.id,
        "paid_total": float(paid_total),
        "created_new": creating_new,
        "linked": True,
    }


class MergeChargePayload(BaseModel):
    target_charge_id: int


class BulkMergeChargeRequest(BaseModel):
    charge_ids: List[int]


class MergeChargeFieldsPayload(BaseModel):
    source_charge_id: int
    target_charge_id: int
    merged_fields: dict  # Contains the field values to use for the final merged charge


@router.post("/charges/{charge_id}/merge")
def merge_charge(
    charge_id: int, payload: MergeChargePayload, db: Session = Depends(get_db)
):
    """
    Merge this charge into another charge by moving all payments to the target charge
    and then deleting this charge.
    """
    try:
        from models.database import Charge as ChargeModel  # type: ignore
        from models.database import ChargePaymentXref as XrefModel
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
        from .models.database import ChargePaymentXref as XrefModel

    # Validate source charge exists
    source_charge = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
    if not source_charge:
        raise HTTPException(status_code=404, detail="Source charge not found")

    # Validate target charge exists
    target_charge = (
        db.query(ChargeModel).filter(ChargeModel.id == payload.target_charge_id).first()
    )
    if not target_charge:
        raise HTTPException(status_code=404, detail="Target charge not found")

    # Cannot merge a charge into itself
    if charge_id == payload.target_charge_id:
        raise HTTPException(status_code=400, detail="Cannot merge a charge into itself")

    # Move all payments from source charge to target charge
    payment_xrefs = db.query(XrefModel).filter(XrefModel.charge_id == charge_id).all()
    moved_payments = []

    for xref in payment_xrefs:
        # Check if target charge already has this payment linked
        existing_link = (
            db.query(XrefModel)
            .filter(
                XrefModel.charge_id == payload.target_charge_id,
                XrefModel.payment_id == xref.payment_id,
            )
            .first()
        )

        if not existing_link:
            # Create new link to target charge
            new_xref = XrefModel(
                charge_id=payload.target_charge_id, payment_id=xref.payment_id
            )
            db.add(new_xref)
            moved_payments.append(xref.payment_id)

        # Remove link from source charge
        db.delete(xref)

    # Move all attachments from source charge to target charge
    try:
        from models.database import Attachment as AttachmentModel  # type: ignore
    except ImportError:
        from .models.database import Attachment as AttachmentModel  # type: ignore

    attachments = (
        db.query(AttachmentModel).filter(AttachmentModel.charge_id == charge_id).all()
    )
    moved_attachments = []

    for attachment in attachments:
        attachment.charge_id = payload.target_charge_id
        moved_attachments.append(attachment.id)

    # Delete the source charge
    db.delete(source_charge)

    # Commit all changes
    db.commit()

    return {
        "message": "Charge merged successfully",
        "source_charge_id": charge_id,
        "target_charge_id": payload.target_charge_id,
        "moved_payments": moved_payments,
        "moved_attachments": moved_attachments,
    }


@router.post("/charges/merge-compare")
def get_merge_comparison(
    payload: BulkMergeChargeRequest, db: Session = Depends(get_db)
):
    """Get detailed comparison data for charges to be merged."""
    try:
        from models.database import Charge as ChargeModel  # type: ignore
        from models.database import Provider as ProviderModel
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
        from .models.database import Provider as ProviderModel

    if len(payload.charge_ids) != 2:
        raise HTTPException(
            status_code=400, detail="Exactly 2 charges must be selected for merging"
        )

    charges = []
    for charge_id in payload.charge_ids:
        charge = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
        if not charge:
            raise HTTPException(status_code=404, detail=f"Charge {charge_id} not found")

        # Get provider info
        provider = None
        if charge.provider_id:
            provider = (
                db.query(ProviderModel)
                .filter(ProviderModel.id == charge.provider_id)
                .first()
            )

        charge_data = {
            "id": charge.id,
            "service_date": charge.service_date.isoformat()
            if charge.service_date
            else None,
            "family_member": charge.family_member,
            "medical_type": charge.medical_type,
            "provider_id": charge.provider_id,
            "provider_name": provider.provider_name if provider else None,
            "doctor_name": charge.doctor_name,
            "charge_description": charge.charge_description,
            "cpt_code": charge.cpt_code,
            "base_fee_amount": float(charge.base_fee_amount or 0),
            "charged_amount": float(charge.charged_amount or 0),
            "owed_amount": float(charge.owed_amount or 0),
            "reimbursement_amount": float(charge.reimbursement_amount or 0),
            "eob_claim_num": charge.eob_claim_num,
            "insurance_discount_amount": float(charge.insurance_discount_amount or 0),
            "insurance_paid_amount": float(charge.insurance_paid_amount or 0),
            "notes": charge.notes,
        }
        charges.append(charge_data)

    return {"charges": charges}


@router.post("/charges/merge-with-fields")
def merge_charges_with_fields(
    payload: MergeChargeFieldsPayload, db: Session = Depends(get_db)
):
    """Merge charges using selected field values and move all payments/attachments."""
    try:
        from models.database import Attachment as AttachmentModel
        from models.database import Charge as ChargeModel  # type: ignore
        from models.database import ChargePaymentXref as XrefModel
    except ImportError:
        from .models.database import Attachment as AttachmentModel
        from .models.database import Charge as ChargeModel  # type: ignore
        from .models.database import ChargePaymentXref as XrefModel

    source_charge = (
        db.query(ChargeModel).filter(ChargeModel.id == payload.source_charge_id).first()
    )
    target_charge = (
        db.query(ChargeModel).filter(ChargeModel.id == payload.target_charge_id).first()
    )

    if not source_charge or not target_charge:
        raise HTTPException(status_code=404, detail="One or both charges not found")

    if payload.source_charge_id == payload.target_charge_id:
        raise HTTPException(status_code=400, detail="Cannot merge a charge into itself")

    # Update target charge with merged field values
    for field, value in payload.merged_fields.items():
        if hasattr(target_charge, field):
            # Handle special cases for type conversion
            if field == "service_date" and value:
                from datetime import datetime

                target_charge.service_date = datetime.fromisoformat(value).date()
            elif field in [
                "base_fee_amount",
                "charged_amount",
                "owed_amount",
                "reimbursement_amount",
                "insurance_discount_amount",
                "insurance_paid_amount",
            ]:
                setattr(
                    target_charge, field, float(value) if value is not None else None
                )
            elif field == "provider_id":
                setattr(target_charge, field, int(value) if value is not None else None)
            else:
                setattr(target_charge, field, value)

    # Move all payments from source to target
    payment_xrefs = (
        db.query(XrefModel)
        .filter(XrefModel.charge_id == payload.source_charge_id)
        .all()
    )
    moved_payments = []

    for xref in payment_xrefs:
        existing_link = (
            db.query(XrefModel)
            .filter(
                XrefModel.charge_id == payload.target_charge_id,
                XrefModel.payment_id == xref.payment_id,
            )
            .first()
        )

        if not existing_link:
            new_xref = XrefModel(
                charge_id=payload.target_charge_id, payment_id=xref.payment_id
            )
            db.add(new_xref)
            moved_payments.append(xref.payment_id)

        db.delete(xref)

    # Move all attachments from source to target
    attachments = (
        db.query(AttachmentModel)
        .filter(AttachmentModel.charge_id == payload.source_charge_id)
        .all()
    )
    moved_attachments = []

    for attachment in attachments:
        attachment.charge_id = payload.target_charge_id
        moved_attachments.append(attachment.id)

    # Delete the source charge
    db.delete(source_charge)

    db.commit()

    return {
        "message": "Charges merged successfully with field selection",
        "source_charge_id": payload.source_charge_id,
        "target_charge_id": payload.target_charge_id,
        "moved_payments": moved_payments,
        "moved_attachments": moved_attachments,
    }


class MarkNoDupRequest(BaseModel):
    charge_ids: List[int]


@router.post("/charges/mark-no-dup")
def mark_charges_no_dup(payload: MarkNoDupRequest, db: Session = Depends(get_db)):
    """Append ' NO DUP' to notes for the provided charge IDs.

    Returns the list of IDs that were updated.
    """
    if not payload.charge_ids:
        raise HTTPException(status_code=400, detail="No charge_ids provided")
    updated = charge_crud.mark_as_no_dup(db, payload.charge_ids)
    return {"updated_ids": updated, "requested": payload.charge_ids}
