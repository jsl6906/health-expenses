from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from database_config import get_db
from crud.provider import provider_crud
from schemas.schemas import Provider, ProviderCreate, ProviderUpdate

router = APIRouter(tags=["providers"], prefix="/api")

@router.post("/providers/", response_model=Provider)
def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    existing = db.query(provider_crud.model).filter(
        provider_crud.model.provider_name.ilike(provider.provider_name.strip())
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Provider name already exists")
    return provider_crud.create(db, provider.model_dump())

@router.get("/providers/exists")
def provider_exists(name: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    normalized = name.strip()
    if not normalized:
        return {"exists": False}
    exists = db.query(provider_crud.model.id).filter(
        provider_crud.model.provider_name.ilike(normalized)
    ).first() is not None
    return {"exists": exists}

@router.get("/providers/", response_model=List[Provider])
def read_providers(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
    return provider_crud.get_all(db, skip=skip, limit=limit)

@router.get("/providers/{provider_id}", response_model=Provider)
def read_provider(provider_id: int, db: Session = Depends(get_db)):
    provider = provider_crud.get(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider

@router.put("/providers/{provider_id}", response_model=Provider)
def update_provider(provider_id: int, provider: ProviderUpdate, db: Session = Depends(get_db)):
    db_provider = provider_crud.get(db, provider_id)
    if db_provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider_crud.update(db, db_provider, provider.model_dump(exclude_unset=True))

@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db)):
    try:
        from ..models.database import Provider as PModel, Charge as CModel, Payment as PayModel, Attachment as AttModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Provider as PModel, Charge as CModel, Payment as PayModel, Attachment as AttModel, ChargePaymentXref as XrefModel  # type: ignore
    provider = db.query(PModel).filter(PModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    charge_ids = [cid for (cid,) in db.query(CModel.id).filter(CModel.provider_id == provider_id).all()]
    payment_ids = [pid for (pid,) in db.query(PayModel.id).filter(PayModel.provider_id == provider_id).all()]
    if charge_ids:
        try:
            db.query(AttModel).filter(AttModel.charge_id.in_(charge_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    if payment_ids:
        try:
            db.query(AttModel).filter(AttModel.payment_id.in_(payment_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    if charge_ids or payment_ids:
        try:
            db.query(XrefModel).filter(
                (XrefModel.charge_id.in_(charge_ids)) | (XrefModel.payment_id.in_(payment_ids))
            ).delete(synchronize_session=False)
        except Exception:
            pass
    if charge_ids:
        try:
            db.query(CModel).filter(CModel.id.in_(charge_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    if payment_ids:
        try:
            db.query(PayModel).filter(PayModel.id.in_(payment_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    db.delete(provider)
    db.commit()
    return {"message": "Provider and related entities deleted"}

@router.get("/providers/active/", response_model=List[Provider])
def read_active_providers(db: Session = Depends(get_db)):
    return provider_crud.get_active_providers(db)

@router.get("/provider-groups")
def list_provider_groups(q: Optional[str] = Query(None, description="Optional case-insensitive substring filter"), db: Session = Depends(get_db)):
    Model = provider_crud.model
    query = db.query(Model.provider_group).filter(
        Model.provider_group.isnot(None),
        func.length(func.trim(Model.provider_group)) > 0
    )
    if q:
        like_pattern = f"%{q.strip()}%"
        query = query.filter(Model.provider_group.ilike(like_pattern))
    raw_groups = [row[0] for row in query.distinct().all()]
    normalized = []
    seen = set()
    for g in raw_groups:
        if g is None:
            continue
        trimmed = g.strip()
        low = trimmed.lower()
        if low not in seen:
            seen.add(low)
            normalized.append(trimmed)
    normalized.sort(key=lambda s: s.lower())
    return {"groups": normalized}

@router.get("/cpt-codes")
def list_cpt_codes(q: Optional[str] = Query(None, description="Optional case-insensitive substring filter"), db: Session = Depends(get_db)):
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Charge as ChargeModel  # type: ignore
    query = db.query(ChargeModel.cpt_code).filter(
        ChargeModel.cpt_code.isnot(None),
        func.length(func.trim(ChargeModel.cpt_code)) > 0
    )
    if q:
        like_pattern = f"%{q.strip()}%"
        query = query.filter(ChargeModel.cpt_code.ilike(like_pattern))
    raw_codes = [row[0] for row in query.distinct().all()]
    normalized = []
    seen = set()
    for c in raw_codes:
        if c is None:
            continue
        trimmed = c.strip()
        key = trimmed.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(trimmed)
    normalized.sort(key=lambda s: s.lower())
    return {"cpt_codes": normalized}
