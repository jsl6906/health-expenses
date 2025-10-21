"""
CRUD operations for Charge model.
"""

from datetime import date
from typing import List

from sqlalchemy.orm import Session

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Charge
except ImportError:
    from ..models.database import Charge
    from .base import BaseCRUD


class ChargeCRUD(BaseCRUD[Charge]):
    """CRUD operations for Charge model."""

    def __init__(self):
        super().__init__(Charge)

    def get_by_family_member_id(
        self, db: Session, family_member_id: int
    ) -> List[Charge]:
        """Get charges by family member id."""
        return (
            db.query(Charge).filter(Charge.family_member_id == family_member_id).all()
        )

    def get_by_family_member(self, db: Session, family_member) -> List[Charge]:
        """Get charges by family member id or name.

        Accepts either an integer id, a numeric string, or a name string. If a
        name is provided, attempts to match first+last name or either first or
        last name (case-insensitive).
        """
        # Local import to avoid circular import issues in some execution modes
        try:
            from models.database import FamilyMember  # type: ignore
        except Exception:
            from ..models.database import FamilyMember  # type: ignore

        # If passed an integer, delegate to the id-based method
        if isinstance(family_member, int):
            return self.get_by_family_member_id(db, family_member)

        fam = (str(family_member or "")).strip()
        # If it's a numeric string, treat as id
        if fam.isdigit():
            return self.get_by_family_member_id(db, int(fam))

        if not fam:
            return []

        from sqlalchemy import func, or_

        parts = fam.split()
        q = db.query(Charge).join(
            FamilyMember, Charge.family_member_id == FamilyMember.id
        )

        # If looks like 'First Last' try to match both
        if len(parts) >= 2:
            first = parts[0]
            last = " ".join(parts[1:])
            return q.filter(
                func.lower(FamilyMember.first_name) == first.lower(),
                func.lower(FamilyMember.last_name) == last.lower(),
            ).all()

        # Otherwise match either first or last name (case-insensitive)
        return q.filter(
            or_(
                func.lower(FamilyMember.first_name) == fam.lower(),
                func.lower(FamilyMember.last_name) == fam.lower(),
            )
        ).all()

    def get_by_medical_type(self, db: Session, medical_type: str) -> List[Charge]:
        """Get charges by medical type."""
        return db.query(Charge).filter(Charge.medical_type == medical_type).all()

    def get_by_date_range(
        self, db: Session, start_date: date, end_date: date
    ) -> List[Charge]:
        """Get charges within date range."""
        return (
            db.query(Charge)
            .filter(Charge.service_date >= start_date, Charge.service_date <= end_date)
            .all()
        )

    def get_by_provider(self, db: Session, provider_id: int) -> List[Charge]:
        """Get charges by provider."""
        return db.query(Charge).filter(Charge.provider_id == provider_id).all()

    def get_unpaid_charges(self, db: Session) -> List[Charge]:
        """Get charges with outstanding amount owed."""
        return db.query(Charge).filter(Charge.owed_amount > 0).all()

    def mark_as_no_dup(self, db: Session, charge_ids: List[int]) -> List[int]:
        """Append ' NO DUP' to the notes field for the given charge IDs.

        Returns a list of charge IDs that were updated.
        The method avoids appending duplicate markers if 'NO DUP' (case-insensitive)
        already appears in the notes.
        """
        updated: List[int] = []
        for cid in charge_ids:
            obj = db.query(Charge).filter(Charge.id == cid).first()
            if not obj:
                continue
            current_notes = (getattr(obj, "notes", None) or "").strip()
            # If NO DUP already mentioned, skip
            if "NO DUP" in current_notes.upper():
                continue
            # Append with a leading space if there are existing notes
            if current_notes:
                new_notes = f"{current_notes} NO DUP"
            else:
                new_notes = "NO DUP"
            # Use setattr to avoid static type-checker complaints about Column attributes
            setattr(obj, "notes", new_notes)
            updated.append(cid)

        if updated:
            db.commit()
            # Optionally refresh objects if needed
        return updated

    def bulk_update(
        self, db: Session, charge_ids: List[int], update_data: dict
    ) -> List[int]:
        """Update multiple charges with the provided data.

        Only updates fields that are present in update_data.
        Returns the list of charge IDs that were updated.
        """
        updated: List[int] = []
        for cid in charge_ids:
            obj = db.query(Charge).filter(Charge.id == cid).first()
            if not obj:
                continue
            for field, value in update_data.items():
                if not hasattr(obj, field):
                    continue
                if field == "notes":
                    # Append new notes to existing notes rather than overwrite
                    existing = (getattr(obj, "notes", None) or "").strip()
                    to_append = (value or "").strip()
                    if not to_append:
                        # nothing to append
                        continue
                    if existing:
                        # separate appended notes with a semicolon + space to make them distinct
                        new_notes = f"{existing}; {to_append}"
                    else:
                        new_notes = to_append
                    setattr(obj, "notes", new_notes)
                else:
                    setattr(obj, field, value)
            updated.append(cid)
        if updated:
            db.commit()
        return updated


# Create instance
charge_crud = ChargeCRUD()
