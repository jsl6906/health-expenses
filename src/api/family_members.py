import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..crud.family_member import family_member_crud
from ..database_config import get_db
from ..schemas.schemas import FamilyMember, FamilyMemberCreate, FamilyMemberUpdate

logger = logging.getLogger(__name__)


def _mask_ssn(value: str | None) -> str | None:
    if not value:
        return None
    # simple mask: keep last 4
    v = str(value)
    if len(v) >= 4:
        return "***-**-" + v[-4:]
    return "***"


router = APIRouter(tags=["family_members"], prefix="/api")


@router.post("/family_members/", response_model=FamilyMember)
def create_family_member(
    family_member: FamilyMemberCreate, db: Session = Depends(get_db)
):
    # Log incoming payload keys and masked SSN for debugging (do not log full SSN)
    try:
        payload = family_member.model_dump()
        masked = _mask_ssn(payload.get("ssn"))
        logger.info(
            "Create family_member payload keys=%s ssn=%s gender=%s",
            list(payload.keys()),
            masked,
            payload.get("gender"),
        )
    except Exception:
        logger.exception("Failed to log create_family_member payload")
    return family_member_crud.create(db, family_member.model_dump(), user="system")


@router.get("/family_members/", response_model=List[FamilyMember])
def read_family_members(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return family_member_crud.get_all(db, skip=skip, limit=limit)


@router.get("/family_members/{family_member_id}", response_model=FamilyMember)
def read_family_member(family_member_id: int, db: Session = Depends(get_db)):
    member = family_member_crud.get(db, family_member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Family member not found")
    return member


@router.put("/family_members/{family_member_id}", response_model=FamilyMember)
def update_family_member(
    family_member_id: int, update: FamilyMemberUpdate, db: Session = Depends(get_db)
):
    member = family_member_crud.get(db, family_member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Family member not found")
    try:
        payload = update.model_dump(exclude_unset=True)
        masked = _mask_ssn(payload.get("ssn"))
        logger.info(
            "Update family_member id=%s payload_keys=%s ssn=%s gender=%s",
            family_member_id,
            list(payload.keys()),
            masked,
            payload.get("gender"),
        )
    except Exception:
        logger.exception("Failed to log update_family_member payload")
    return family_member_crud.update(
        db, member, update.model_dump(exclude_unset=True), user="system"
    )
