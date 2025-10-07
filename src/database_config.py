"""
Database configuration and connection management.
"""

import os
import urllib.parse
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

# Handle imports whether running as module or script
try:
    from models.database import Base
except ImportError:
    from .models.database import Base

# Load environment variables
load_dotenv()


class DatabaseConfig:
    """Database configuration class with ENV_MODE (TEST|PROD) support.

    Priority of credential selection:
      1. ENV_MODE=TEST uses TEST_DB_* variables.
      2. ENV_MODE=PROD (default) uses PROD_DB_* variables.
      3. If corresponding prefixed variables absent, fall back to generic DB_* variables.
    """

    def __init__(self):
        self.env_mode = (os.getenv("ENV_MODE", "PROD") or "PROD").upper()
        if self.env_mode not in {"TEST", "PROD"}:
            print(f"WARNING: Unknown ENV_MODE '{self.env_mode}' - defaulting to PROD")
            self.env_mode = "PROD"

        # Determine prefix based on mode
        prefix = "TEST" if self.env_mode == "TEST" else "PROD"

        def first(*names: str, default: str | None = None):
            for n in names:
                v = os.getenv(n)
                if v not in (None, ""):
                    return v
            return default

        # Host / Port
        self.db_host = first(f"{prefix}_DB_HOST", "DB_HOST", default="localhost")
        self.db_port = first(f"{prefix}_DB_PORT", "DB_PORT", default="5432")
        self.db_name = first(f"{prefix}_DB_NAME", "DB_NAME", default="personal_expenses")
        self.db_user = first(f"{prefix}_DB_USER", "DB_USER", default="postgres")
        self.db_password = first(f"{prefix}_DB_PASSWORD", "DB_PASSWORD", default="")

        # Safety: If TEST mode but host looks like a prod host (heuristic), warn.
        if self.env_mode == "TEST" and self.db_host not in {"localhost", "127.0.0.1", "192.0.2.10"}:
            print(f"WARNING: ENV_MODE=TEST but host={self.db_host} is not local.")

        # Connection pool settings
        self.pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))

        # Build connection string with URL-encoded password
        encoded_password = urllib.parse.quote_plus(self.db_password or "")
        self.database_url = (
            f"postgresql://{self.db_user}:{encoded_password}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )


# Global database configuration
db_config = DatabaseConfig()

# Create database engine
engine = create_engine(
    db_config.database_url,
    pool_size=db_config.pool_size,
    max_overflow=db_config.max_overflow,
    pool_pre_ping=True,  # Verify connections before use
    echo=bool(os.getenv("DB_ECHO", "False").lower() == "true")  # SQL logging
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Get database session.
    
    Yields:
        Database session
    """
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as e:
        db.rollback()
        raise e
    finally:
        db.close()


def create_tables():
    """Create all database tables."""
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully!")
    except Exception as e:
        print(f"Error creating database tables: {e}")
        raise


def drop_tables():
    """Drop all database tables."""
    try:
        Base.metadata.drop_all(bind=engine)
        print("Database tables dropped successfully!")
    except Exception as e:
        print(f"Error dropping database tables: {e}")
        raise


def test_connection():
    """Test database connection."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("Database connection successful!")
            return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False