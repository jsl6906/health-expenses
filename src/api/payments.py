from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database_config import get_db
from crud.payment import payment_crud
from schemas.schemas import Payment, PaymentCreate, PaymentUpdate

router = APIRouter(tags=["payments"], prefix="/api")

@router.post("/payments/", response_model=Payment)
def create_payment(payment: PaymentCreate, db: Session = Depends(get_db)):
    data = payment.model_dump()
    if data.get('expense_type') and hasattr(data['expense_type'], 'value'):
        data['expense_type'] = data['expense_type'].value
    return payment_crud.create(db, data)

@router.get("/payments/", response_model=List[Payment])
def read_payments(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
    return payment_crud.get_all(db, skip=skip, limit=limit)

@router.get("/payments/{payment_id}", response_model=Payment)
def read_payment(payment_id: int, db: Session = Depends(get_db)):
    p = payment_crud.get(db, payment_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return p

@router.put("/payments/{payment_id}", response_model=Payment)
def update_payment(payment_id: int, payment: PaymentUpdate, db: Session = Depends(get_db)):
    db_payment = payment_crud.get(db, payment_id)
    if db_payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    data = payment.model_dump(exclude_unset=True)
    if data.get('expense_type') and hasattr(data['expense_type'], 'value'):
        data['expense_type'] = data['expense_type'].value
    return payment_crud.update(db, db_payment, data)

@router.delete("/payments/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    try:
        from models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    except ImportError:
        from .models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    pay_obj = payment_crud.get(db, payment_id)
    if pay_obj is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        db.query(_AttachmentModel).filter(_AttachmentModel.payment_id == payment_id).delete(synchronize_session=False)
    except Exception:
        pass
    try:
        db.query(_Xref).filter(_Xref.payment_id == payment_id).delete(synchronize_session=False)
    except Exception:
        pass
    db.delete(pay_obj)
    db.commit()
    return {"message": "Payment and related attachments deleted"}


class PaymentWithChargesPayload(BaseModel):
    payment_date: Optional[str] = None
    payment_amount: float
    expense_type: str
    provider_id: int
    has_receipt_indicator: Optional[str] = None
    reimbursement_amount: Optional[float] = None
    notes: Optional[str] = None
    charge_ids: List[int] = []

@router.post("/payments/with-charges")
def create_payment_with_charges(payload: PaymentWithChargesPayload, db: Session = Depends(get_db)):
    try:
        from models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
    payment_data = {
        "payment_date": payload.payment_date,
        "payment_amount": payload.payment_amount,
        "expense_type": payload.expense_type,
        "provider_id": payload.provider_id,
        "has_receipt_indicator": payload.has_receipt_indicator,
        "reimbursement_amount": payload.reimbursement_amount,
        "notes": payload.notes,
    }
    payment_obj = payment_crud.create(db, payment_data)
    if payload.charge_ids:
        existing_charges = {c.id for c in db.query(ChargeModel.id).filter(ChargeModel.id.in_(payload.charge_ids)).all()}
        for cid in payload.charge_ids:
            if cid in existing_charges:
                db.add(XrefModel(charge_id=cid, payment_id=payment_obj.id))
        db.commit()
    else:
        db.commit()
    return {"payment_id": payment_obj.id, "linked_charge_ids": payload.charge_ids, "charge_link_count": len(payload.charge_ids)}