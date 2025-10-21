import json
import logging
import traceback
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
    family_member_id: int = Form(...),
    medical_type: str = Form(...),
    provider_id: int = Form(...),
    doctor_name: str | None = Form(None),
    diagnosis_code: str | None = Form(None),
    eob_claim_num: str | None = Form(None),
    rows_json: str = Form(...),
    attachment: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    # Log incoming request basic metadata for troubleshooting
    logger = logging.getLogger(__name__)
    try:
        client_host = request.client.host if request.client else None
    except Exception:
        client_host = None
    try:
        logger.info(
            f"bulk_create_charges called from {client_host}; content-type={request.headers.get('content-type')}"
        )
    except Exception:
        pass

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

    try:
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
            # Row-level diagnosis takes precedence; otherwise use batch-level diagnosis_code
            diagnosis_code = r.get("diagnosis_code") or diagnosis_code or None
            claim_submitted_date = r.get("claim_submitted_date") or None
            charge_in = {
                "service_date": svc_date_obj.isoformat() if svc_date_obj else None,
                "family_member_id": family_member_id,
                "medical_type": medical_type,
                "provider_id": provider_id,
                "doctor_name": doctor_name,
                "eob_claim_num": eob_claim_num,
                "cpt_code": cpt_code,
                "diagnosis_code": diagnosis_code,
                "claim_submitted_date": claim_submitted_date,
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
                created = charge_crud.create(db, charge_in, user="system")
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
                # Continue creating the rest even if one row fails
                logger.exception("Failed to create charge row")
                continue
    except Exception:
        # Unexpected top-level exception during processing rows; log traceback and return 500
        tb = traceback.format_exc()
        logger.error(f"Unexpected error in bulk_create_charges: {tb}")
        raise HTTPException(
            status_code=500, detail="Internal server error processing bulk create"
        )
    db.commit()
    if not created_ids:
        raise HTTPException(status_code=400, detail="No valid charge rows were created")

    attached_id = None
    if attachment and highest_charge_id is not None:
        # Defensive read: ensure we catch any errors reading the uploaded file
        # and return a clear error to the client instead of letting the connection
        # be dropped which manifests as a "Failed to fetch" in the browser.
        logger = logging.getLogger(__name__)
        try:
            file_bytes = await attachment.read()
        except Exception as read_exc:
            logger.exception(
                "Error reading uploaded attachment '%s' (content_type=%s): %s",
                getattr(attachment, "filename", None),
                getattr(attachment, "content_type", None),
                read_exc,
            )
            # Return a JSON error so the client receives a proper response
            raise HTTPException(
                status_code=400,
                detail=f"Failed to read uploaded file: {read_exc}",
            )

        from schemas.schemas import (
            AttachmentCreate as _AttachmentCreate,  # type: ignore
        )

        att_in = _AttachmentCreate(
            charge_id=highest_charge_id,
            payment_id=None,
            notes=f"Created via bulk upload, attachment on charge #{highest_charge_id}",
        )
        try:
            create_attachment(
                db,
                data=att_in,
                original_filename="Bulk Charge Uploaded Attachment",
                content_type=attachment.content_type or "application/octet-stream",
                file_size=len(file_bytes),
                content_bytes=file_bytes,
                user="system",
            )
            db.commit()
            attached_id = highest_charge_id
            # Update notes on other created charges to reference where the attachment was stored
            try:
                note_text = f"Created via bulk upload, attachment on charge #{highest_charge_id}"
                logger.debug(f"Updating notes on other charges to: {note_text}")
                for cid in created_ids:
                    if cid == highest_charge_id:
                        continue
                    try:
                        from models.database import (
                            Charge as ChargeModel,  # type: ignore
                        )
                    except ImportError:
                        from ..models.database import (
                            Charge as ChargeModel,  # type: ignore
                        )
                    ch_obj = db.query(ChargeModel).filter(ChargeModel.id == cid).first()
                    if not ch_obj:
                        continue
                    existing = (getattr(ch_obj, "notes", None) or "").strip()
                    if existing:
                        new_notes = f"{existing}; {note_text}"
                    else:
                        new_notes = note_text
                    setattr(ch_obj, "notes", new_notes)
                db.commit()
                logger.debug(
                    f"Updated notes for {len(created_ids)-1} charges (excluding attached)"
                )
            except Exception as note_exc:
                logger.exception(
                    "Failed to update notes on non-attached charges: %s", note_exc
                )
        except Exception as create_exc:
            # Log details so we can inspect failures creating attachment records
            logger.exception(
                "Failed to store attachment for '%s' (content_type=%s): %s",
                getattr(attachment, "filename", None),
                getattr(attachment, "content_type", None),
                create_exc,
            )
            # Do not fail the entire bulk create; return success for created rows but
            # indicate attachment was not applied by leaving attached_id as None.
            attached_id = None

    return {
        "created_count": len(created_ids),
        "created_ids": created_ids,
        "attachment_applied_to": attached_id,
    }


@router.post("/charges/", response_model=Charge)
def create_charge(charge: ChargeCreate, db: Session = Depends(get_db)):
    data = charge.model_dump()
    if data.get("medical_type") and hasattr(data["medical_type"], "value"):
        data["medical_type"] = data["medical_type"].value
    cpt = data.get("cpt_code")
    if cpt is not None:
        cpt_norm = cpt.strip()
        if not cpt_norm or cpt_norm.lower() in {"???", "na", "n/a"}:
            data["cpt_code"] = None
        else:
            data["cpt_code"] = cpt_norm
    return charge_crud.create(db, data, user="system")


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
    return charge_crud.update(db, db_charge, data, user="system")


@router.delete("/charges/{charge_id}")
def delete_charge(charge_id: int, db: Session = Depends(get_db)):
    try:
        from models.database import Attachment as _AttachmentModel  # type: ignore
        from models.database import ChargePaymentXref as _Xref
    except ImportError:
        from ..models.database import Attachment as _AttachmentModel  # type: ignore
        from ..models.database import ChargePaymentXref as _Xref
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
        from ..models.database import Charge as ChargeModel  # type: ignore
        from ..models.database import ChargePaymentXref as XrefModel
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
        from sqlalchemy import func

        db.add(
            XrefModel(
                charge_id=charge_id,
                payment_id=payment_obj.id,
                created_at=func.now(),
                updated_at=func.now(),
                created_by="system",
                updated_by="system",
            )
        )
    db.commit()

    # Recompute allocations for this payment so allocated_amount fields are set.
    try:
        from ..services.allocation import allocate_payment_to_charges
    except Exception:
        from services.allocation import allocate_payment_to_charges

    # Get all linked charge ids for this payment and compute allocations
    all_xrefs = db.query(XrefModel).filter(XrefModel.payment_id == payment_obj.id).all()
    all_charge_ids = [xref.charge_id for xref in all_xrefs]
    try:
        allocations = allocate_payment_to_charges(
            db,
            float(getattr(payment_obj, "payment_amount", 0.0) or 0.0),
            all_charge_ids,
        )
    except Exception:
        allocations = {cid: 0.0 for cid in all_charge_ids}

    # Update xrefs with new allocated amounts
    try:
        from sqlalchemy import func as _func

        for xref in all_xrefs:
            xref.allocated_amount = allocations.get(xref.charge_id, 0.0)
            xref.updated_at = _func.now()
            xref.updated_by = "system"
        db.commit()
    except Exception:
        # If updates fail, log nothing here but ensure we don't crash the endpoint
        db.rollback()
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
        from ..models.database import Charge as ChargeModel  # type: ignore
        from ..models.database import ChargePaymentXref as XrefModel

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
            from sqlalchemy import func

            new_xref = XrefModel(
                charge_id=payload.target_charge_id,
                payment_id=xref.payment_id,
                created_at=func.now(),
                updated_at=func.now(),
                created_by="system",
                updated_by="system",
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
        setattr(attachment, "charge_id", payload.target_charge_id)
        moved_attachments.append(getattr(attachment, "id", None))

    # Ensure DB session is flushed so newly-created xrefs are visible to subsequent queries
    # This makes allocation recompute see the updated links before we query/update xrefs.
    try:
        db.flush()
    except Exception:
        # If flush fails, continue and allow the recompute block to attempt best-effort updates.
        pass

    # Recompute allocations for payments that are now linked to the target charge
    try:
        try:
            from ..services.allocation import allocate_payment_to_charges
        except Exception:
            from services.allocation import allocate_payment_to_charges

        try:
            from models.database import Payment as PaymentModel  # type: ignore
        except ImportError:
            from .models.database import Payment as PaymentModel  # type: ignore

        # Get distinct payment_ids now linked to the target charge
        linked_pay_rows = (
            db.query(XrefModel.payment_id)
            .filter(XrefModel.charge_id == payload.target_charge_id)
            .distinct()
            .all()
        )
        linked_payment_ids = [r[0] for r in linked_pay_rows if r and r[0] is not None]

        for pid in set(linked_payment_ids):
            # Get all charges linked to this payment
            xrefs_for_payment = (
                db.query(XrefModel).filter(XrefModel.payment_id == pid).all()
            )
            charge_ids = [getattr(x, "charge_id", None) for x in xrefs_for_payment]
            if not charge_ids:
                continue
            pay_obj = db.query(PaymentModel).filter(PaymentModel.id == pid).first()
            amt = float(getattr(pay_obj, "payment_amount", 0.0) or 0.0)
            try:
                allocs = allocate_payment_to_charges(db, amt, charge_ids)
            except Exception:
                allocs = {cid: 0.0 for cid in charge_ids}

            # Update xrefs for this payment
            from sqlalchemy import func as _func

            for x in xrefs_for_payment:
                try:
                    x.allocated_amount = allocs.get(x.charge_id, 0.0)
                    x.updated_at = _func.now()
                    x.updated_by = "system"
                except Exception:
                    # Best-effort: if updating one xref fails, continue with others
                    continue
    except Exception:
        # Do not fail the merge if allocation recompute errors; proceed to commit
        pass

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
        from ..models.database import Charge as ChargeModel  # type: ignore
        from ..models.database import Provider as ProviderModel

    if len(payload.charge_ids) != 2:
        raise HTTPException(
            status_code=400, detail="Exactly 2 charges must be selected for merging"
        )

    charges = []
    for charge_id in payload.charge_ids:
        charge = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
        if not charge:
            raise HTTPException(status_code=404, detail=f"Charge {charge_id} not found")

        # Get provider info safely
        provider = None
        provider_id_val = getattr(charge, "provider_id", None)
        if provider_id_val is not None:
            provider = (
                db.query(ProviderModel)
                .filter(ProviderModel.id == provider_id_val)
                .first()
            )

        # Safely extract values from SQLAlchemy objects to avoid static analysis issues
        svc = getattr(charge, "service_date", None)
        claim_sub = getattr(charge, "claim_submitted_date", None)

        def _to_float_attr(obj, attr):
            v = getattr(obj, attr, None)
            try:
                return float(v) if v is not None else 0.0
            except Exception:
                return 0.0

        # Build a safe, JSON-friendly representation for family member: include id and display string
        fm_obj = getattr(charge, "family_member", None)
        fm_id = getattr(charge, "family_member_id", None)
        fm_display = None
        if fm_obj:
            try:
                fm_display = (
                    fm_obj.emoji
                    if getattr(fm_obj, "emoji", None)
                    else (
                        (fm_obj.first_name or "")
                        + (
                            " " + fm_obj.last_name
                            if getattr(fm_obj, "last_name", None)
                            else ""
                        )
                    )
                )
            except Exception:
                fm_display = None

        charge_data = {
            "id": getattr(charge, "id", None),
            "service_date": svc.isoformat() if svc is not None else None,
            # family_member: provide both an id (used for updates) and a human-friendly display string for the UI
            "family_member": fm_id,
            "family_member_display": fm_display,
            "medical_type": getattr(charge, "medical_type", None),
            "provider_id": provider_id_val,
            "provider_name": provider.provider_name if provider else None,
            "doctor_name": getattr(charge, "doctor_name", None),
            "charge_description": getattr(charge, "charge_description", None),
            "cpt_code": getattr(charge, "cpt_code", None),
            "diagnosis_code": getattr(charge, "diagnosis_code", None),
            "claim_submitted_date": claim_sub.isoformat()
            if claim_sub is not None
            else None,
            "base_fee_amount": _to_float_attr(charge, "base_fee_amount"),
            "charged_amount": _to_float_attr(charge, "charged_amount"),
            "owed_amount": _to_float_attr(charge, "owed_amount"),
            "reimbursement_amount": _to_float_attr(charge, "reimbursement_amount"),
            "eob_claim_num": getattr(charge, "eob_claim_num", None),
            "insurance_discount_amount": _to_float_attr(
                charge, "insurance_discount_amount"
            ),
            "insurance_paid_amount": _to_float_attr(charge, "insurance_paid_amount"),
            "notes": getattr(charge, "notes", None),
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
        from ..models.database import Attachment as AttachmentModel
        from ..models.database import Charge as ChargeModel  # type: ignore
        from ..models.database import ChargePaymentXref as XrefModel

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

                setattr(
                    target_charge, "service_date", datetime.fromisoformat(value).date()
                )
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
            elif field in ("provider_id", "family_member_id"):
                # Coerce to int for foreign key columns
                try:
                    setattr(
                        target_charge,
                        field,
                        int(value) if value is not None and value != "" else None,
                    )
                except Exception:
                    setattr(target_charge, field, None)
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
            from sqlalchemy import func

            new_xref = XrefModel(
                charge_id=payload.target_charge_id,
                payment_id=xref.payment_id,
                created_at=func.now(),
                updated_at=func.now(),
                created_by="system",
                updated_by="system",
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
        setattr(attachment, "charge_id", payload.target_charge_id)
        moved_attachments.append(getattr(attachment, "id", None))

    # Ensure DB session is flushed so newly-created xrefs are visible to subsequent queries
    # This makes allocation recompute see the updated links before we query/update xrefs.
    try:
        db.flush()
    except Exception:
        # If flush fails, continue and allow the recompute block to attempt best-effort updates.
        pass

    # Recompute allocations for payments that are now linked to the target charge
    try:
        try:
            from ..services.allocation import allocate_payment_to_charges
        except Exception:
            from services.allocation import allocate_payment_to_charges

        try:
            from models.database import Payment as PaymentModel  # type: ignore
        except ImportError:
            from .models.database import Payment as PaymentModel  # type: ignore

        linked_pay_rows = (
            db.query(XrefModel.payment_id)
            .filter(XrefModel.charge_id == payload.target_charge_id)
            .distinct()
            .all()
        )
        linked_payment_ids = [r[0] for r in linked_pay_rows if r and r[0] is not None]

        for pid in set(linked_payment_ids):
            xrefs_for_payment = (
                db.query(XrefModel).filter(XrefModel.payment_id == pid).all()
            )
            charge_ids = [getattr(x, "charge_id", None) for x in xrefs_for_payment]
            if not charge_ids:
                continue
            pay_obj = db.query(PaymentModel).filter(PaymentModel.id == pid).first()
            amt = float(getattr(pay_obj, "payment_amount", 0.0) or 0.0)
            try:
                allocs = allocate_payment_to_charges(db, amt, charge_ids)
            except Exception:
                allocs = {cid: 0.0 for cid in charge_ids}

            from sqlalchemy import func as _func

            for x in xrefs_for_payment:
                try:
                    x.allocated_amount = allocs.get(x.charge_id, 0.0)
                    x.updated_at = _func.now()
                    x.updated_by = "system"
                except Exception:
                    continue
    except Exception:
        pass

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


class BulkUpdateRequest(BaseModel):
    charge_ids: List[int]
    update_data: dict


@router.post("/charges/bulk-update")
def bulk_update_charges(payload: BulkUpdateRequest, db: Session = Depends(get_db)):
    """Update multiple charges with the provided data.

    Only updates fields that are present in update_data.
    Returns the list of charge IDs that were updated.
    """
    if not payload.charge_ids:
        raise HTTPException(status_code=400, detail="No charge_ids provided")
    if not payload.update_data:
        raise HTTPException(status_code=400, detail="No update_data provided")
    # Normalize/validate certain incoming fields before applying
    upd = dict(payload.update_data)
    # CPT code normalization: trim and reject empty-like markers
    if "cpt_code" in upd:
        c = upd.get("cpt_code")
        if c is None:
            upd.pop("cpt_code", None)
        else:
            c_norm = str(c).strip()
            if not c_norm or c_norm.lower() in {"???", "na", "n/a"}:
                upd.pop("cpt_code", None)
            else:
                upd["cpt_code"] = c_norm

    # Diagnosis code - trim whitespace
    if "diagnosis_code" in upd:
        d = upd.get("diagnosis_code")
        if d is None or str(d).strip() == "":
            upd.pop("diagnosis_code", None)
        else:
            upd["diagnosis_code"] = str(d).strip()

    # Claim submitted date: accept YYYY-MM-DD strings and coerce to ISO date string
    if "claim_submitted_date" in upd:
        cs = upd.get("claim_submitted_date")
        if not cs:
            upd.pop("claim_submitted_date", None)
        else:
            try:
                dt = datetime.fromisoformat(str(cs))
                upd["claim_submitted_date"] = dt.date().isoformat()
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid claim_submitted_date format; use YYYY-MM-DD",
                )

    updated = charge_crud.bulk_update(db, payload.charge_ids, upd)
    return {"updated_ids": updated, "requested": payload.charge_ids}
