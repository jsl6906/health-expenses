"""
CRUD operations for FamilyMember model.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from ..models.database import FamilyMember
from .base import BaseCRUD


class FamilyMemberCRUD(BaseCRUD[FamilyMember]):
    def __init__(self):
        super().__init__(FamilyMember)

    def get_by_name(
        self, db: Session, first_name: str, last_name: Optional[str] = None
    ) -> List[FamilyMember]:
        query = db.query(FamilyMember).filter(FamilyMember.first_name == first_name)
        if last_name:
            query = query.filter(FamilyMember.last_name == last_name)
        return query.all()


# Create instance
family_member_crud = FamilyMemberCRUD()
