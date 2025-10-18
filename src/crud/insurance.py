"""
CRUD operations for Insurance model.
"""

from ..models.database import Insurance
from .base import BaseCRUD


class InsuranceCRUD(BaseCRUD[Insurance]):
    def __init__(self):
        super().__init__(Insurance)


insurance_crud = InsuranceCRUD()
