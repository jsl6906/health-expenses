"""
Database models for Personal Health Expenses application.
Based on the DBML schema for managing medical, dental, and vision expenses.
"""

from sqlalchemy import Column, Integer, String, Date, Boolean, Numeric, ForeignKey, Text, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()


class FamilyMember(enum.Enum):
    ALEX = "Alex"
    JORDAN = "Jordan"
    SAM = "Sam"
    RILEY = "Riley"


class MedicalType(enum.Enum):
    MEDICAL = "Medical"
    DENTAL = "Dental"
    VISION = "Vision"
    MENTAL_HEALTH = "Mental Health"
    OTHER = "Other"


class ExpenseType(enum.Enum):
    MEDICAL = "Medical"
    DENTAL = "Dental"
    VISION = "Vision"
    HSA_FSA = "HSA / FSA"


class Provider(Base):
    __tablename__ = 'providers'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_name = Column(String, nullable=False)
    provider_group = Column(String)
    provider_address = Column(String)
    one_way_miles = Column(Numeric(10, 2))
    active_provider_flag = Column(Boolean, default=True)
    
    # Relationships
    charges = relationship("Charge", back_populates="provider")
    payments = relationship("Payment", back_populates="provider")


class Charge(Base):
    __tablename__ = 'charges'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    excel_legacy_id = Column(String, comment="Legacy identifier from Excel spreadsheet")
    service_date = Column(Date)
    family_member = Column(String)  # Will store enum values as strings
    medical_type = Column(String)   # Will store enum values as strings
    cpt_code = Column(String)
    provider_id = Column(Integer, ForeignKey('providers.id'))
    doctor_name = Column(String)
    charge_description = Column(String)
    base_fee_amount = Column(Numeric(10, 2))
    charged_amount = Column(Numeric(10, 2))
    owed_amount = Column(Numeric(10, 2))
    reimbursement_amount = Column(Numeric(10, 2), comment="Amount reimbursed by HSA / FSA")
    eob_claim_num = Column(String)
    insurance_discount_amount = Column(Numeric(10, 2))
    insurance_paid_amount = Column(Numeric(10, 2))
    ignore_for_issue_spotting = Column(Boolean, default=False)
    notes = Column(Text)
    
    # Relationships
    provider = relationship("Provider", back_populates="charges")
    charge_payments = relationship("ChargePaymentXref", back_populates="charge")
    attachments = relationship("Attachment", back_populates="charge")


class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_date = Column(Date)
    payment_amount = Column(Numeric(10, 2))
    expense_type = Column(String)  # Will store enum values as strings
    provider_id = Column(Integer, ForeignKey('providers.id'))
    provider_name_legacy = Column(String, comment="From legacy spreadsheet, now mapped via provider_id")
    link_to_legacy_charges = Column(String)
    has_receipt_indicator = Column(String)
    transaction_match_flag = Column(String)
    reimbursement_amount = Column(Numeric(10, 2), comment="Amount reimbursed by HSA / FSA")
    notes = Column(Text)
    
    # Relationships
    provider = relationship("Provider", back_populates="payments")
    payment_charges = relationship("ChargePaymentXref", back_populates="payment")
    attachments = relationship("Attachment", back_populates="payment")


class ChargePaymentXref(Base):
    __tablename__ = 'charge_payment_xref'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    charge_id = Column(Integer, ForeignKey('charges.id'))
    payment_id = Column(Integer, ForeignKey('payments.id'))
    
    # Relationships
    charge = relationship("Charge", back_populates="charge_payments")
    payment = relationship("Payment", back_populates="payment_charges")


class Attachment(Base):
    __tablename__ = 'attachments'

    id = Column(Integer, primary_key=True, autoincrement=True)
    charge_id = Column(Integer, ForeignKey('charges.id'), nullable=True, index=True)
    payment_id = Column(Integer, ForeignKey('payments.id'), nullable=True, index=True)
    original_filename = Column(String, nullable=False)
    content_type = Column(String)
    file_size = Column(Integer)
    content = Column(LargeBinary, nullable=False)
    notes = Column(Text)
    # Future fields: created_at, updated_at, checksum, uploaded_by

    charge = relationship("Charge", back_populates="attachments")
    payment = relationship("Payment", back_populates="attachments")


class Premium(Base):
    __tablename__ = 'premiums'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    medical_premium = Column(Numeric(10, 2))
    dental_premium = Column(Numeric(10, 2))
    vision_premium = Column(Numeric(10, 2))
    hsa_fsa_paid = Column(Numeric(10, 2))


class RefExpenseType(Base):
    __tablename__ = 'ref_expense_types'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    expense_type_name = Column(String, nullable=False)