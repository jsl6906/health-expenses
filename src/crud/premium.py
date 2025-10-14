"""
CRUD operations for Premium model.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Premium
except ImportError:
    from ..models.database import Premium
    from .base import BaseCRUD


class PremiumCRUD(BaseCRUD[Premium]):
    """CRUD operations for Premium model."""

    def __init__(self):
        super().__init__(Premium)

    def get_by_year(self, db: Session, year: int) -> Optional[Premium]:
        """Get premium by year."""
        return db.query(Premium).filter(Premium.year == year).first()

    def get_years_range(
        self, db: Session, start_year: int, end_year: int
    ) -> List[Premium]:
        """Get premiums within year range."""
        return (
            db.query(Premium)
            .filter(Premium.year >= start_year, Premium.year <= end_year)
            .all()
        )


# Create instance
premium_crud = PremiumCRUD()
