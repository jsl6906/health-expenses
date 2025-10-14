"""
CRUD operations for Payment model.
"""

from datetime import date
from typing import List

from sqlalchemy.orm import Session

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Payment
except ImportError:
    from ..models.database import Payment
    from .base import BaseCRUD


class PaymentCRUD(BaseCRUD[Payment]):
    """CRUD operations for Payment model."""

    def __init__(self):
        super().__init__(Payment)

    def get_by_date_range(
        self, db: Session, start_date: date, end_date: date
    ) -> List[Payment]:
        """Get payments within date range."""
        return (
            db.query(Payment)
            .filter(
                Payment.payment_date >= start_date, Payment.payment_date <= end_date
            )
            .all()
        )

    def get_by_expense_type(self, db: Session, expense_type: str) -> List[Payment]:
        """Get payments by expense type."""
        return db.query(Payment).filter(Payment.expense_type == expense_type).all()

    def get_by_provider(self, db: Session, provider_id: int) -> List[Payment]:
        """Get payments by provider."""
        return db.query(Payment).filter(Payment.provider_id == provider_id).all()


# Create instance
payment_crud = PaymentCRUD()
