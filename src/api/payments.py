from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..crud.payment import payment_crud
from ..database_config import get_db
from ..schemas.schemas import Payment, PaymentCreate, PaymentUpdate

router = APIRouter(tags=["payments"], prefix="/api")


@router.post("/payments/", response_model=Payment)
def create_payment(payment: PaymentCreate, db: Session = Depends(get_db)):
    data = payment.model_dump()
    if data.get("expense_type") and hasattr(data["expense_type"], "value"):
        data["expense_type"] = data["expense_type"].value
    return payment_crud.create(db, data, user="system")


@router.get("/payments/", response_model=List[Payment])
def read_payments(
    skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)
):
    return payment_crud.get_all(db, skip=skip, limit=limit)


@router.get("/payments/{payment_id}", response_model=Payment)
def read_payment(payment_id: int, db: Session = Depends(get_db)):
    p = payment_crud.get(db, payment_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return p


@router.put("/payments/{payment_id}", response_model=Payment)
def update_payment(
    payment_id: int, payment: PaymentUpdate, db: Session = Depends(get_db)
):
    db_payment = payment_crud.get(db, payment_id)
    if db_payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    data = payment.model_dump(exclude_unset=True)
    if data.get("expense_type") and hasattr(data["expense_type"], "value"):
        data["expense_type"] = data["expense_type"].value
    return payment_crud.update(db, db_payment, data, user="system")


@router.delete("/payments/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    try:
        from models.database import Attachment as _AttachmentModel  # type: ignore
        from models.database import ChargePaymentXref as _Xref
    except ImportError:
        from .models.database import Attachment as _AttachmentModel  # type: ignore
        from .models.database import ChargePaymentXref as _Xref
    pay_obj = payment_crud.get(db, payment_id)
    if pay_obj is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        db.query(_AttachmentModel).filter(
            _AttachmentModel.payment_id == payment_id
        ).delete(synchronize_session=False)
    except Exception:
        pass
    try:
        db.query(_Xref).filter(_Xref.payment_id == payment_id).delete(
            synchronize_session=False
        )
    except Exception:
        pass
    db.delete(pay_obj)
    db.commit()
    return {"message": "Payment and related attachments deleted"}


class LinkChargesPayload(BaseModel):
    charge_ids: List[int] = []


@router.post("/payments/{payment_id}/link-charges")
def link_charges_to_payment(
    payment_id: int, payload: LinkChargesPayload, db: Session = Depends(get_db)
):
    try:
        from models.database import Charge as ChargeModel  # type: ignore
        from models.database import ChargePaymentXref as XrefModel
        from models.database import Payment as PaymentModel
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
        from .models.database import ChargePaymentXref as XrefModel
        from .models.database import Payment as PaymentModel

    payment = db.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Get existing linked charges
    existing_xrefs = (
        db.query(XrefModel).filter(XrefModel.payment_id == payment_id).all()
    )
    existing_charge_ids = {xref.charge_id for xref in existing_xrefs}

    # Validate new charges
    if payload.charge_ids:
        existing_charges = {
            c.id
            for c in db.query(ChargeModel.id)
            .filter(ChargeModel.id.in_(payload.charge_ids))
            .all()
        }
        valid_new_charge_ids = [
            cid
            for cid in payload.charge_ids
            if cid in existing_charges and cid not in existing_charge_ids
        ]
    else:
        valid_new_charge_ids = []

    if not valid_new_charge_ids:
        return {"message": "No new charges to link"}

    # All charge_ids for this payment
    all_charge_ids = list(existing_charge_ids) + valid_new_charge_ids

    # Recompute allocations
    try:
        from ..services.allocation import allocate_payment_to_charges
    except ImportError:
        from services.allocation import allocate_payment_to_charges
    allocations = allocate_payment_to_charges(
        db, float(payment.payment_amount or 0.0), all_charge_ids
    )

    # Update existing xrefs
    for xref in existing_xrefs:
        new_alloc = allocations.get(xref.charge_id, 0.0)
        xref.allocated_amount = new_alloc
        xref.updated_at = func.now()
        xref.updated_by = "system"

    # Add new xrefs
    for cid in valid_new_charge_ids:
        alloc_amt = allocations.get(cid, 0.0)
        db.add(
            XrefModel(charge_id=cid, payment_id=payment_id, allocated_amount=alloc_amt)
        )

    db.commit()

    return {
        "payment_id": payment_id,
        "new_linked_charge_ids": valid_new_charge_ids,
        "total_linked_charges": len(all_charge_ids),
    }


@router.delete("/payments/{payment_id}/charges/{charge_id}")
def unlink_charge_from_payment(
    payment_id: int, charge_id: int, db: Session = Depends(get_db)
):
    try:
        from models.database import ChargePaymentXref as XrefModel
        from models.database import Payment as PaymentModel
    except ImportError:
        from .models.database import ChargePaymentXref as XrefModel
        from .models.database import Payment as PaymentModel

    payment = db.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Check if xref exists
    xref = (
        db.query(XrefModel)
        .filter(XrefModel.payment_id == payment_id, XrefModel.charge_id == charge_id)
        .first()
    )
    if not xref:
        raise HTTPException(status_code=404, detail="Charge not linked to this payment")

    # Delete the xref
    db.delete(xref)

    # Get remaining linked charges
    remaining_xrefs = (
        db.query(XrefModel).filter(XrefModel.payment_id == payment_id).all()
    )
    remaining_charge_ids = [xref.charge_id for xref in remaining_xrefs]

    if remaining_charge_ids:
        # Recompute allocations
        try:
            from ..services.allocation import allocate_payment_to_charges
        except ImportError:
            from services.allocation import allocate_payment_to_charges
        allocations = allocate_payment_to_charges(
            db, float(payment.payment_amount or 0.0), remaining_charge_ids
        )

        # Update remaining xrefs
        for rxref in remaining_xrefs:
            new_alloc = allocations.get(rxref.charge_id, 0.0)
            rxref.allocated_amount = new_alloc
            rxref.updated_at = func.now()
            rxref.updated_by = "system"

    db.commit()

    return {
        "payment_id": payment_id,
        "unlinked_charge_id": charge_id,
        "remaining_linked_charges": len(remaining_charge_ids),
    }


@router.delete("/payments/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    try:
        from models.database import Attachment as _AttachmentModel  # type: ignore
        from models.database import ChargePaymentXref as _Xref
    except ImportError:
        from .models.database import Attachment as _AttachmentModel  # type: ignore
        from .models.database import ChargePaymentXref as _Xref
    pay_obj = payment_crud.get(db, payment_id)
    if pay_obj is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        db.query(_AttachmentModel).filter(
            _AttachmentModel.payment_id == payment_id
        ).delete(synchronize_session=False)
    except Exception:
        pass
    try:
        db.query(_Xref).filter(_Xref.payment_id == payment_id).delete(
            synchronize_session=False
        )
    except Exception:
        pass
    db.delete(pay_obj)
    db.commit()
    return {"message": "Payment and related attachments deleted"}
