"""
CRUD operations for Provider model.
"""

from typing import List, Optional
from sqlalchemy.orm import Session

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Provider
except ImportError:
    from .base import BaseCRUD
    from ..models.database import Provider


class ProviderCRUD(BaseCRUD[Provider]):
    """CRUD operations for Provider model."""
    
    def __init__(self):
        super().__init__(Provider)
    
    def get_by_name(self, db: Session, provider_name: str) -> Optional[Provider]:
        """Get provider by name."""
        return db.query(Provider).filter(Provider.provider_name == provider_name).first()
    
    def get_active_providers(self, db: Session) -> List[Provider]:
        """Get all active providers."""
        return db.query(Provider).filter(Provider.active_provider_flag == True).all()
    
    def search_by_group(self, db: Session, group_name: str) -> List[Provider]:
        """Search providers by group."""
        return db.query(Provider).filter(Provider.provider_group.ilike(f"%{group_name}%")).all()


# Create instance
provider_crud = ProviderCRUD()