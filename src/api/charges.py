import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from sqlalchemy.orm import Session

from database_config import get_db
from crud.charge import charge_crud
from crud.provider import provider_crud
from crud.attachment import create_attachment  # needed for bulk attach
from schemas.schemas import Charge, ChargeCreate, ChargeUpdate
from pydantic import BaseModel

router = APIRouter(tags=["charges"], prefix="/api")

# Page endpoints (non-API JSON): kept here for cohesion; could move to a pages module later.
@router.get("/charges/add-multiple", include_in_schema=False)
def add_multiple_charges_page(request: Request, db: Session = Depends(get_db)):
    providers = provider_crud.get_all(db, limit=None)
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    templates = Jinja2Templates(directory=str(Path(__file__).parent.parent.parent / "templates"))
    return templates.TemplateResponse("add_multiple_charges.html", {"request": request, "providers": providers})

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
    db: Session = Depends(get_db)
):
    svc_date_obj = None
    if service_date:
        try:
            svc_date_obj = datetime.strptime(service_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid service_date format. Use YYYY-MM-DD")
    try:
        rows = json.loads(rows_json)
        if not isinstance(rows, list):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="rows_json must be a JSON array of row objects")
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
            amt_fields = [r.get("charged_amount"), r.get("owed_amount"), r.get("base_fee_amount")]
            if not any(amt_fields):
                continue
        cpt_code = (r.get("cpt_code") or r.get("cpt") or None)
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
            cid = int(getattr(created, 'id'))
            created_ids.append(cid)
            amt_for_compare = (charge_in["charged_amount"] if charge_in["charged_amount"] is not None else 0.0)
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
            from schemas.schemas import AttachmentCreate as _AttachmentCreate  # type: ignore
            att_in = _AttachmentCreate(charge_id=highest_charge_id, payment_id=None, notes="Attachment for batch (highest charge)")
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

    return {"created_count": len(created_ids), "created_ids": created_ids, "attachment_applied_to": attached_id}

@router.post("/charges/", response_model=Charge)
def create_charge(charge: ChargeCreate, db: Session = Depends(get_db)):
    data = charge.model_dump()
    if data.get('family_member') and hasattr(data['family_member'], 'value'):
        data['family_member'] = data['family_member'].value
    if data.get('medical_type') and hasattr(data['medical_type'], 'value'):
        data['medical_type'] = data['medical_type'].value
    cpt = data.get('cpt_code')
    if cpt is not None:
        cpt_norm = cpt.strip()
        if not cpt_norm or cpt_norm.lower() in {'???','na','n/a'}:
            data['cpt_code'] = None
        else:
            data['cpt_code'] = cpt_norm
    return charge_crud.create(db, data)

@router.get("/charges/", response_model=List[Charge])
def read_charges(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
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
    if data.get('family_member') and hasattr(data['family_member'], 'value'):
        data['family_member'] = data['family_member'].value
    if data.get('medical_type') and hasattr(data['medical_type'], 'value'):
        data['medical_type'] = data['medical_type'].value
    if 'cpt_code' in data and data['cpt_code'] is not None:
        cpt_norm = data['cpt_code'].strip()
        if not cpt_norm or cpt_norm.lower() in {'???','na','n/a'}:
            data['cpt_code'] = None
        else:
            data['cpt_code'] = cpt_norm
    return charge_crud.update(db, db_charge, data)

@router.delete("/charges/{charge_id}")
def delete_charge(charge_id: int, db: Session = Depends(get_db)):
    try:
        from models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    except ImportError:
        from .models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    ch_obj = charge_crud.get(db, charge_id)
    if ch_obj is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    try:
        db.query(_AttachmentModel).filter(_AttachmentModel.charge_id == charge_id).delete(synchronize_session=False)
    except Exception:
        pass
    try:
        db.query(_Xref).filter(_Xref.charge_id == charge_id).delete(synchronize_session=False)
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
def apply_payment_to_charge(charge_id: int, payload: ApplyPaymentPayload, db: Session = Depends(get_db)):
    try:
        from models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
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
            raise HTTPException(status_code=400, detail=f"Missing required fields for new payment: {', '.join(missing)}")
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
        payment_obj = db.query(PaymentModel).filter(PaymentModel.id == payload.payment_id).first()
        if not payment_obj:
            raise HTTPException(status_code=404, detail="Payment not found")
    existing = db.query(XrefModel).filter(XrefModel.charge_id == charge_id, XrefModel.payment_id == payment_obj.id).first()
    if not existing:
        db.add(XrefModel(charge_id=charge_id, payment_id=payment_obj.id))
    db.commit()
    from sqlalchemy import func as _func
    from models.database import Payment as PaymentModel  # type: ignore
    paid_total = db.query(_func.coalesce(_func.sum(PaymentModel.payment_amount), 0)).join(
        XrefModel, XrefModel.payment_id == PaymentModel.id
    ).filter(XrefModel.charge_id == charge_id).scalar() or 0
    return {"charge_id": charge_id, "payment_id": payment_obj.id, "paid_total": float(paid_total), "created_new": creating_new, "linked": True}
