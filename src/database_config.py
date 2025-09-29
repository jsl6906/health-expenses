"""
Database configuration and connection management.
"""

import os
import urllib.parse
from typing import Generator
from sqlalchemy import create_engine
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
    """Database configuration class."""
    
    def __init__(self):
        self.db_host = os.getenv("DB_HOST", "localhost")
        self.db_port = os.getenv("DB_PORT", "5432")
        self.db_name = os.getenv("DB_NAME", "personal_expenses")
        self.db_user = os.getenv("DB_USER", "postgres")
        self.db_password = os.getenv("DB_PASSWORD", "")
        
        # Connection pool settings
        self.pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        
        # Build connection string with URL-encoded password
        encoded_password = urllib.parse.quote_plus(self.db_password)
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
            connection.execute("SELECT 1")
            print("Database connection successful!")
            return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False