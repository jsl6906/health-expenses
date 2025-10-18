from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..crud.insurance import insurance_crud
from ..database_config import get_db
from ..schemas.schemas import Insurance, InsuranceCreate, InsuranceUpdate

router = APIRouter(tags=["insurance"], prefix="/api")


@router.post("/insurance/", response_model=Insurance)
def create_insurance(insurance: InsuranceCreate, db: Session = Depends(get_db)):
    return insurance_crud.create(db, insurance.model_dump(), user="system")


@router.get("/insurance/", response_model=List[Insurance])
def read_insurance(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return insurance_crud.get_all(db, skip=skip, limit=limit)


@router.get("/insurance/{insurance_id}", response_model=Insurance)
def read_insurance_item(insurance_id: int, db: Session = Depends(get_db)):
    obj = insurance_crud.get(db, insurance_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Insurance record not found")
    return obj


@router.put("/insurance/{insurance_id}", response_model=Insurance)
def update_insurance(
    insurance_id: int, update: InsuranceUpdate, db: Session = Depends(get_db)
):
    obj = insurance_crud.get(db, insurance_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Insurance record not found")
    return insurance_crud.update(
        db, obj, update.model_dump(exclude_unset=True), user="system"
    )


@router.delete("/insurance/{insurance_id}")
def delete_insurance(insurance_id: int, db: Session = Depends(get_db)):
    ok = insurance_crud.delete(db, insurance_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Insurance record not found")
    return {"deleted": True}
