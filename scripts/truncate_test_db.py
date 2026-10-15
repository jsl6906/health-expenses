#!/usr/bin/env python3
"""
Ad-hoc script to truncate all records from the TEST database.

This script will:
1. Verify we're in TEST mode
2. Truncate all tables in dependency order
3. Reset auto-increment sequences

Safety checks:
- Only runs if ENV_MODE=TEST
- Confirms database name contains 'test' (case insensitive)
- Requires user confirmation before proceeding
"""

import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

try:
    from database_config import db_config, engine, local_db_hosts
except ImportError as e:
    print(f"❌ Error importing database config: {e}")
    print("Make sure you're running this from the project root or scripts directory.")
    sys.exit(1)


def main():
    print("=== TEST Database Truncation Script ===")
    print(f"Environment Mode: {db_config.env_mode}")
    print(f"Database Host: {db_config.db_host}")
    print(f"Database Name: {db_config.db_name}")
    print(f"Database User: {db_config.db_user}")

    # Safety check 1: Must be in TEST mode
    if db_config.env_mode != "TEST":
        print(
            f"❌ ERROR: This script only runs in TEST mode. Current mode: {db_config.env_mode}"
        )
        print("Set ENV_MODE=TEST in your .env file or environment variables.")
        sys.exit(1)

    # Safety check 2: Not a production-looking host
    if db_config.db_host not in local_db_hosts():
        print(
            f"❌ WARNING: Host '{db_config.db_host}' doesn't look like a test environment"
        )
        confirm = input(
            "Are you sure this is a test database? (type 'YES' to continue): "
        )
        if confirm != "YES":
            print("Aborting for safety.")
            sys.exit(1)

    print("\n⚠️  This will DELETE ALL DATA from the TEST database!")
    print(f"Database: {db_config.db_name} on {db_config.db_host}")

    confirm = input("\nType 'DELETE ALL DATA' to confirm: ")
    if confirm != "DELETE ALL DATA":
        print("Aborting.")
        sys.exit(0)

    # Create session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = None

    try:
        db = SessionLocal()

        print("\n🧹 Starting truncation...")

        # Disable foreign key checks temporarily (PostgreSQL)
        print("Disabling foreign key constraints...")
        db.execute(text("SET session_replication_role = replica;"))

        # List of tables to truncate in dependency order (children first)
        tables_to_truncate = [
            "charge_payment_xref",
            "attachments",
            "charges",
            "payments",
            "providers",
            "premiums",
        ]

        # Truncate each table
        for table_name in tables_to_truncate:
            print(f"Truncating {table_name}...")

            # First check if table exists
            result = db.execute(
                text(
                    """--sql
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = :table_name
                )
            """
                ),
                {"table_name": table_name},
            )

            table_exists = result.scalar()

            if table_exists:
                # Get count before truncation
                count_result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count = count_result.scalar() or 0

                if count > 0:
                    # Truncate and restart identity (reset auto-increment)
                    db.execute(
                        text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE")
                    )
                    print(f"  ✅ Deleted {count} records from {table_name}")
                else:
                    print(f"  ✅ {table_name} was already empty")
            else:
                print(f"  ⚠️  Table {table_name} doesn't exist, skipping")

        # Re-enable foreign key checks
        print("Re-enabling foreign key constraints...")
        db.execute(text("SET session_replication_role = DEFAULT;"))

        # Commit all changes
        db.commit()
        print("\n✅ Database truncation completed successfully!")

        # Verify all tables are empty
        print("\n🔍 Verification - checking record counts:")
        for table_name in tables_to_truncate:
            result = db.execute(
                text(
                    """--sql
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = :table_name
                )
            """
                ),
                {"table_name": table_name},
            )

            if result.scalar():
                count_result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count = count_result.scalar()
                print(f"  {table_name}: {count} records")

        db.close()

    except Exception as e:
        print(f"\n❌ Error during truncation: {e}")
        if db is not None:
            db.rollback()
            db.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
