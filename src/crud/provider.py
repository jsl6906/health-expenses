"""
CRUD operations for Provider model.
"""

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Charge, Payment, Provider
except ImportError:
    from ..models.database import Charge, Payment, Provider
    from .base import BaseCRUD


class ProviderCRUD(BaseCRUD[Provider]):
    """CRUD operations for Provider model."""

    def __init__(self):
        super().__init__(Provider)

    def get_all_with_derived_fields(
        self, db: Session, skip: int = 0, limit: Optional[int] = None
    ) -> List[Provider]:
        """Get all providers with derived fields computed."""
        query = db.query(
            Provider,
            Provider.last_charge_date.label("last_charge_date"),
            Provider.last_payment_date.label("last_payment_date"),
        ).options(selectinload(Provider.charges), selectinload(Provider.payments))

        if skip:
            query = query.offset(skip)
        if limit:
            query = query.limit(limit)

        results = query.all()

        # Convert to Provider objects with derived fields set
        providers = []
        for result in results:
            provider = result[0]
            # The hybrid properties will be calculated automatically
            providers.append(provider)

        return providers

    def get_by_name(self, db: Session, provider_name: str) -> Optional[Provider]:
        """Get provider by name."""
        return (
            db.query(Provider).filter(Provider.provider_name == provider_name).first()
        )

    def get_active_providers(self, db: Session) -> List[Provider]:
        """Get all active providers with derived fields."""
        return (
            db.query(Provider)
            .filter(Provider.active_provider_flag == True)
            .options(selectinload(Provider.charges), selectinload(Provider.payments))
            .all()
        )

    def search_by_group(self, db: Session, group_name: str) -> List[Provider]:
        """Search providers by group."""
        return (
            db.query(Provider)
            .filter(Provider.provider_group.ilike(f"%{group_name}%"))
            .all()
        )


# Create instance
provider_crud = ProviderCRUD()
