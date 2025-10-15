from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..crud.provider import provider_crud
from ..database_config import get_db
from ..schemas.schemas import Provider, ProviderCreate, ProviderUpdate


class BulkSetGroupRequest(BaseModel):
    provider_ids: List[int] = Field(
        ..., description="List of provider IDs to update (must contain at least one ID)"
    )
    group_name: Optional[str] = Field(
        None, description="Group name to assign (null or empty to clear)"
    )

    def validate_ids(
        self,
    ):  # simple runtime guard if pydantic v1 can't enforce min_items
        if not self.provider_ids:
            raise ValueError("provider_ids must contain at least one id")
        return self


class BulkSetLocationRequest(BaseModel):
    provider_ids: List[int] = Field(..., description="List of provider IDs to update")
    provider_address: Optional[str] = Field(
        None, description="Address to set (null to clear)"
    )
    one_way_miles: Optional[float] = Field(
        None, description="One-way miles to set (null to clear)"
    )

    def validate_ids(self):
        if not self.provider_ids:
            raise ValueError("provider_ids must contain at least one id")
        return self


class BulkSetActiveRequest(BaseModel):
    provider_ids: List[int] = Field(..., description="List of provider IDs to update")
    active: bool = Field(..., description="True to set active, False to set inactive")

    def validate_ids(self):
        if not self.provider_ids:
            raise ValueError("provider_ids must contain at least one id")
        return self


router = APIRouter(tags=["providers"], prefix="/api")


@router.post("/providers/", response_model=Provider)
def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(provider_crud.model)
        .filter(provider_crud.model.provider_name.ilike(provider.provider_name.strip()))
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Provider name already exists")
    return provider_crud.create(db, provider.model_dump())


@router.get("/providers/exists")
def provider_exists(
    name: str = Query(..., min_length=1), db: Session = Depends(get_db)
):
    normalized = name.strip()
    if not normalized:
        return {"exists": False}
    exists = (
        db.query(provider_crud.model.id)
        .filter(provider_crud.model.provider_name.ilike(normalized))
        .first()
        is not None
    )
    return {"exists": exists}


@router.get("/providers/", response_model=List[Provider])
def read_providers(
    skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)
):
    return provider_crud.get_all_with_derived_fields(db, skip=skip, limit=limit)


@router.get("/providers/{provider_id}", response_model=Provider)
def read_provider(provider_id: int, db: Session = Depends(get_db)):
    from sqlalchemy.orm import selectinload

    provider = (
        db.query(provider_crud.model)
        .options(
            selectinload(provider_crud.model.charges),
            selectinload(provider_crud.model.payments),
        )
        .filter(provider_crud.model.id == provider_id)
        .first()
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.put("/providers/{provider_id}", response_model=Provider)
def update_provider(
    provider_id: int, provider: ProviderUpdate, db: Session = Depends(get_db)
):
    db_provider = provider_crud.get(db, provider_id)
    if db_provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider_crud.update(
        db, db_provider, provider.model_dump(exclude_unset=True)
    )


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db)):
    try:
        from ..models.database import Attachment as AttModel
        from ..models.database import Charge as CModel
        from ..models.database import ChargePaymentXref as XrefModel
        from ..models.database import Payment as PayModel
        from ..models.database import Provider as PModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Attachment as AttModel
        from ..models.database import Charge as CModel
        from ..models.database import ChargePaymentXref as XrefModel
        from ..models.database import Payment as PayModel
        from ..models.database import Provider as PModel  # type: ignore
    provider = db.query(PModel).filter(PModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    charge_ids = [
        cid
        for (cid,) in db.query(CModel.id)
        .filter(CModel.provider_id == provider_id)
        .all()
    ]
    payment_ids = [
        pid
        for (pid,) in db.query(PayModel.id)
        .filter(PayModel.provider_id == provider_id)
        .all()
    ]
    if charge_ids:
        try:
            db.query(AttModel).filter(AttModel.charge_id.in_(charge_ids)).delete(
                synchronize_session=False
            )
        except Exception:
            pass
    if payment_ids:
        try:
            db.query(AttModel).filter(AttModel.payment_id.in_(payment_ids)).delete(
                synchronize_session=False
            )
        except Exception:
            pass
    if charge_ids or payment_ids:
        try:
            db.query(XrefModel).filter(
                (XrefModel.charge_id.in_(charge_ids))
                | (XrefModel.payment_id.in_(payment_ids))
            ).delete(synchronize_session=False)
        except Exception:
            pass
    if charge_ids:
        try:
            db.query(CModel).filter(CModel.id.in_(charge_ids)).delete(
                synchronize_session=False
            )
        except Exception:
            pass
    if payment_ids:
        try:
            db.query(PayModel).filter(PayModel.id.in_(payment_ids)).delete(
                synchronize_session=False
            )
        except Exception:
            pass
    db.delete(provider)
    db.commit()
    return {"message": "Provider and related entities deleted"}


@router.post("/providers/{provider_id}/merge")
def merge_provider(provider_id: int, request: dict, db: Session = Depends(get_db)):
    """
    Merge a provider into another provider.

    All charges and payments from the source provider will be moved to the target provider.
    The source provider will be deleted after the merge.
    """
    target_provider_id = request.get("target_provider_id")
    if not target_provider_id:
        raise HTTPException(status_code=400, detail="target_provider_id is required")

    if provider_id == target_provider_id:
        raise HTTPException(
            status_code=400, detail="Cannot merge a provider into itself"
        )

    try:
        from models.database import Charge as CModel
        from models.database import Payment as PayModel
        from models.database import Provider as PModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Charge as CModel
        from .models.database import Payment as PayModel
        from .models.database import Provider as PModel  # type: ignore

    # Verify both providers exist
    source_provider = db.query(PModel).filter(PModel.id == provider_id).first()
    if not source_provider:
        raise HTTPException(status_code=404, detail="Source provider not found")

    target_provider = db.query(PModel).filter(PModel.id == target_provider_id).first()
    if not target_provider:
        raise HTTPException(status_code=404, detail="Target provider not found")

    # Count affected records for reporting
    charge_count = db.query(CModel).filter(CModel.provider_id == provider_id).count()
    payment_count = (
        db.query(PayModel).filter(PayModel.provider_id == provider_id).count()
    )

    # Move all charges to target provider
    db.query(CModel).filter(CModel.provider_id == provider_id).update(
        {CModel.provider_id: target_provider_id}, synchronize_session=False
    )

    # Move all payments to target provider
    db.query(PayModel).filter(PayModel.provider_id == provider_id).update(
        {PayModel.provider_id: target_provider_id}, synchronize_session=False
    )

    # Delete the source provider
    db.delete(source_provider)

    # Commit all changes
    db.commit()

    return {
        "message": f"Successfully merged {source_provider.provider_name} into {target_provider.provider_name}",
        "charges_moved": charge_count,
        "payments_moved": payment_count,
        "source_provider_id": provider_id,
        "target_provider_id": target_provider_id,
    }


@router.get("/providers/active/", response_model=List[Provider])
def read_active_providers(db: Session = Depends(get_db)):
    return provider_crud.get_active_providers(db)


@router.get("/provider-groups")
def list_provider_groups(
    q: Optional[str] = Query(
        None, description="Optional case-insensitive substring filter"
    ),
    db: Session = Depends(get_db),
):
    Model = provider_crud.model
    query = db.query(Model.provider_group).filter(
        Model.provider_group.isnot(None),
        func.length(func.trim(Model.provider_group)) > 0,
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


@router.post("/providers/bulk/set-group")
def bulk_set_group(payload: BulkSetGroupRequest, db: Session = Depends(get_db)):
    """Set or clear provider_group for multiple providers.

    If group_name is blank or only whitespace, the group will be cleared (set to None).
    Returns count of updated providers.
    """
    try:
        from models.database import Provider as PModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Provider as PModel  # type: ignore

    ids = list(
        dict.fromkeys(
            [int(pid) for pid in payload.provider_ids if isinstance(pid, int)]
        )
    )  # dedupe preserve order
    if not ids:
        raise HTTPException(status_code=400, detail="No valid provider IDs supplied")
    group_val: Optional[str]
    if payload.group_name is None:
        group_val = None
    else:
        trimmed = payload.group_name.strip()
        group_val = trimmed if trimmed else None
    try:
        updated = (
            db.query(PModel)
            .filter(PModel.id.in_(ids))
            .update({PModel.provider_group: group_val}, synchronize_session=False)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Bulk update failed: {e}")
    return {"updated": int(updated), "group": group_val}


@router.post("/providers/bulk/set-location")
def bulk_set_location(payload: BulkSetLocationRequest, db: Session = Depends(get_db)):
    """Set provider_address and/or one_way_miles for multiple providers.

    Passing null for a field will clear it. Returns count of updated providers.
    """
    try:
        from models.database import Provider as PModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Provider as PModel  # type: ignore

    ids = list(
        dict.fromkeys(
            [int(pid) for pid in payload.provider_ids if isinstance(pid, int)]
        )
    )
    if not ids:
        raise HTTPException(status_code=400, detail="No valid provider IDs supplied")

    update_values = {}
    # Distinguish between omitted and explicit null: pydantic will set None if omitted or null.
    # We allow both to indicate clearing the field.
    if payload.provider_address is not None:
        addr = payload.provider_address.strip()
        update_values[PModel.provider_address] = addr if addr else None
    if payload.one_way_miles is not None:
        update_values[PModel.one_way_miles] = float(payload.one_way_miles)

    if not update_values:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    try:
        updated = (
            db.query(PModel)
            .filter(PModel.id.in_(ids))
            .update(update_values, synchronize_session=False)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Bulk update failed: {e}")

    return {"updated": int(updated)}


@router.post("/providers/bulk/set-active")
def bulk_set_active(payload: BulkSetActiveRequest, db: Session = Depends(get_db)):
    """Set active_provider_flag for multiple providers."""
    try:
        from models.database import Provider as PModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Provider as PModel  # type: ignore

    ids = list(
        dict.fromkeys(
            [int(pid) for pid in payload.provider_ids if isinstance(pid, int)]
        )
    )
    if not ids:
        raise HTTPException(status_code=400, detail="No valid provider IDs supplied")

    try:
        updated = (
            db.query(PModel)
            .filter(PModel.id.in_(ids))
            .update(
                {PModel.active_provider_flag: bool(payload.active)},
                synchronize_session=False,
            )
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Bulk update failed: {e}")

    return {"updated": int(updated), "active": bool(payload.active)}


@router.get("/cpt-codes")
def list_cpt_codes(
    q: Optional[str] = Query(
        None, description="Optional case-insensitive substring filter"
    ),
    db: Session = Depends(get_db),
):
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Charge as ChargeModel  # type: ignore
    query = db.query(ChargeModel.cpt_code).filter(
        ChargeModel.cpt_code.isnot(None),
        func.length(func.trim(ChargeModel.cpt_code)) > 0,
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
