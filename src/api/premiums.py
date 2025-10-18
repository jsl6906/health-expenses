from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..crud.premium import premium_crud
from ..database_config import get_db
from ..schemas.schemas import Premium, PremiumCreate, PremiumUpdate

router = APIRouter(tags=["premiums"], prefix="/api")


@router.post("/premiums/", response_model=Premium)
def create_premium(premium: PremiumCreate, db: Session = Depends(get_db)):
    existing = premium_crud.get_by_year(db, premium.year)
    if existing:
        raise HTTPException(
            status_code=400, detail=f"Premium already exists for year {premium.year}"
        )
    return premium_crud.create(db, premium.model_dump(), user="system")


@router.get("/premiums/", response_model=List[Premium])
def read_premiums(
    skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)
):
    return premium_crud.get_all(db, skip=skip, limit=limit)


@router.get("/premiums/{premium_id}", response_model=Premium)
def read_premium(premium_id: int, db: Session = Depends(get_db)):
    premium = premium_crud.get(db, premium_id)
    if premium is None:
        raise HTTPException(status_code=404, detail="Premium not found")
    return premium


@router.get("/premiums/year/{year}", response_model=Premium)
def read_premium_by_year(year: int, db: Session = Depends(get_db)):
    premium = premium_crud.get_by_year(db, year)
    if premium is None:
        raise HTTPException(status_code=404, detail="Premium not found for this year")
    return premium


@router.put("/premiums/{premium_id}", response_model=Premium)
def update_premium(
    premium_id: int, premium: PremiumUpdate, db: Session = Depends(get_db)
):
    db_premium = premium_crud.get(db, premium_id)
    if db_premium is None:
        raise HTTPException(status_code=404, detail="Premium not found")
    return premium_crud.update(
        db, db_premium, premium.model_dump(exclude_unset=True), user="system"
    )


@router.delete("/premiums/{premium_id}")
def delete_premium(premium_id: int, db: Session = Depends(get_db)):
    success = premium_crud.delete(db, premium_id)
    if not success:
        raise HTTPException(status_code=404, detail="Premium not found")
    return {"message": "Premium deleted successfully"}
