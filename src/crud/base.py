"""
Base CRUD operations for all models.
"""

from typing import Any, Dict, Generic, List, Optional, Type, TypeVar

from sqlalchemy import func
from sqlalchemy.orm import Session

# Use a more specific bound that includes the id attribute
ModelType = TypeVar("ModelType", bound=Any)


class BaseCRUD(Generic[ModelType]):
    """Base class for CRUD operations."""

    def __init__(self, model: Type[ModelType]):
        self.model = model

    def create(
        self, db: Session, obj_data: Dict[str, Any], user: str = "system"
    ) -> ModelType:
        """Create a new record."""
        # Only pass fields that exist in the model's columns using __mapper__
        if hasattr(self.model, "__mapper__"):
            model_columns = set(self.model.__mapper__.c.keys())  # type: ignore
        else:
            model_columns = set(obj_data.keys())
        valid_fields = {
            field: value for field, value in obj_data.items() if field in model_columns
        }
        # Set audit fields
        if "created_at" in model_columns:
            valid_fields["created_at"] = func.now()
        if "updated_at" in model_columns:
            valid_fields["updated_at"] = func.now()
        if "created_by" in model_columns:
            valid_fields["created_by"] = user
        if "updated_by" in model_columns:
            valid_fields["updated_by"] = user
        db_obj = self.model(**valid_fields)  # type: ignore
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj  # type: ignore

    def get(self, db: Session, id: int) -> Optional[ModelType]:
        """Get a record by ID."""
        return db.query(self.model).filter(self.model.id == id).first()  # type: ignore

    def get_all(
        self, db: Session, skip: int = 0, limit: Optional[int] = None
    ) -> List[ModelType]:
        """Get all records with pagination."""
        query = db.query(self.model)
        if skip > 0:
            query = query.offset(skip)
        if limit is not None and limit > 0:
            query = query.limit(limit)
        return query.all()

    def update(
        self,
        db: Session,
        db_obj: ModelType,
        obj_data: Dict[str, Any],
        user: str = "system",
    ) -> ModelType:
        """Update an existing record."""
        for field, value in obj_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        # Set audit fields
        if hasattr(db_obj, "updated_at"):
            db_obj.updated_at = func.now()  # type: ignore
        if hasattr(db_obj, "updated_by"):
            db_obj.updated_by = user  # type: ignore
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete(self, db: Session, id: int) -> bool:
        """Delete a record by ID."""
        db_obj = db.query(self.model).filter(self.model.id == id).first()  # type: ignore
        if db_obj:
            db.delete(db_obj)
            db.commit()
            return True
        return False

    def search(self, db: Session, **filters) -> List[ModelType]:
        """Search records by filters."""
        query = db.query(self.model)
        for field, value in filters.items():
            if hasattr(self.model, field) and value is not None:
                query = query.filter(getattr(self.model, field) == value)  # type: ignore
        return query.all()
