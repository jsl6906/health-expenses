"""
CRUD operations for Charge model.
"""

from typing import List, Optional
from datetime import date
from sqlalchemy.orm import Session

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Charge, FamilyMember, MedicalType
except ImportError:
    from .base import BaseCRUD
    from ..models.database import Charge, FamilyMember, MedicalType


class ChargeCRUD(BaseCRUD[Charge]):
    """CRUD operations for Charge model."""
    
    def __init__(self):
        super().__init__(Charge)
    
    def get_by_family_member(self, db: Session, family_member: str) -> List[Charge]:
        """Get charges by family member."""
        return db.query(Charge).filter(Charge.family_member == family_member).all()
    
    def get_by_medical_type(self, db: Session, medical_type: str) -> List[Charge]:
        """Get charges by medical type."""
        return db.query(Charge).filter(Charge.medical_type == medical_type).all()
    
    def get_by_date_range(self, db: Session, start_date: date, end_date: date) -> List[Charge]:
        """Get charges within date range."""
        return db.query(Charge).filter(
            Charge.service_date >= start_date,
            Charge.service_date <= end_date
        ).all()
    
    def get_by_provider(self, db: Session, provider_id: int) -> List[Charge]:
        """Get charges by provider."""
        return db.query(Charge).filter(Charge.provider_id == provider_id).all()
    
    def get_unpaid_charges(self, db: Session) -> List[Charge]:
        """Get charges with outstanding amount owed."""
        return db.query(Charge).filter(Charge.owed_amount > 0).all()


# Create instance
charge_crud = ChargeCRUD()