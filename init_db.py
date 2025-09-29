"""
Database initialization script.
Creates tables and optionally loads sample data.
"""

import sys
from pathlib import Path
from datetime import date
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).parent / "src"))
from src.database_config import create_tables, test_connection, get_db
from src.models.database import Provider, Charge, Payment, Premium

def create_sample_data():
    """Create sample data for testing."""
    print("Creating sample data...")
    
    db = next(get_db())
    
    try:
        # Create sample providers
        providers_data = [
            {
                "provider_name": "City General Hospital",
                "provider_group": "Hospital System",
                "provider_address": "123 Main St, Anytown, ST 12345",
                "one_way_miles": Decimal("5.2"),
                "active_provider_flag": True
            },
            {
                "provider_name": "Dr. Smith Family Practice",
                "provider_group": "Primary Care",
                "provider_address": "456 Oak Ave, Anytown, ST 12345",
                "one_way_miles": Decimal("3.1"),
                "active_provider_flag": True
            },
            {
                "provider_name": "Bright Smiles Dental",
                "provider_group": "Dental Care",
                "provider_address": "789 Pine St, Anytown, ST 12345",
                "one_way_miles": Decimal("4.7"),
                "active_provider_flag": True
            }
        ]
        
        providers = []
        for provider_data in providers_data:
            provider = Provider(**provider_data)
            db.add(provider)
            providers.append(provider)
        
        db.commit()
        
        # Refresh to get IDs
        for provider in providers:
            db.refresh(provider)
        
        # Create sample charges
        charges_data = [
            {
                "service_date": date(2024, 1, 15),
                "family_member": "Alex",
                "medical_type": "Medical",
                "provider_id": providers[0].id,
                "charge_description": "Annual Physical Exam",
                "charged_amount": Decimal("250.00"),
                "owed_amount": Decimal("35.00"),
                "insurance_paid_amount": Decimal("215.00")
            },
            {
                "service_date": date(2024, 2, 10),
                "family_member": "Jordan",
                "medical_type": "Dental",
                "provider_id": providers[2].id,
                "charge_description": "Dental Cleaning",
                "charged_amount": Decimal("150.00"),
                "owed_amount": Decimal("0.00"),
                "insurance_paid_amount": Decimal("150.00")
            }
        ]
        
        for charge_data in charges_data:
            charge = Charge(**charge_data)
            db.add(charge)
        
        # Create sample payments
        payments_data = [
            {
                "payment_date": date(2024, 1, 20),
                "payment_amount": Decimal("35.00"),
                "expense_type": "Medical",
                "provider_id": providers[0].id
            }
        ]
        
        for payment_data in payments_data:
            payment = Payment(**payment_data)
            db.add(payment)
        
        # Create sample premium
        premium_data = {
            "year": 2024,
            "medical_premium": Decimal("3600.00"),
            "dental_premium": Decimal("480.00"),
            "vision_premium": Decimal("240.00"),
            "hsa_fsa_paid": Decimal("2000.00")
        }
        
        premium = Premium(**premium_data)
        db.add(premium)
        
        db.commit()
        print("Sample data created successfully!")
        
    except Exception as e:
        print(f"Error creating sample data: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def main():
    """Main initialization function."""
    print("Personal Health Expenses Database Initialization")
    print("=" * 50)
    
    # Test database connection
    print("Testing database connection...")
    if not test_connection():
        print("Database connection failed. Please check your configuration.")
        return False
    
    # Create tables
    print("Creating database tables...")
    try:
        create_tables()
    except Exception as e:
        print(f"Error creating tables: {e}")
        return False
    
    # Ask if user wants sample data
    create_sample = input("Would you like to create sample data? (y/n): ").lower().strip()
    if create_sample in ['y', 'yes']:
        try:
            create_sample_data()
        except Exception as e:
            print(f"Error creating sample data: {e}")
            return False
    
    print("\nDatabase initialization completed successfully!")
    print("You can now start the application with: python -m src.app")
    return True


if __name__ == "__main__":
    main()