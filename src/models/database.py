"""SQLAlchemy ORM models for Personal Expenses application.

This file was reconstructed after accidental truncation.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_name = Column(String, nullable=False)
    provider_group = Column(String)
    provider_address = Column(String)
    one_way_miles = Column(Numeric(10, 2))
    active_provider_flag = Column(Boolean, default=True)

    # Relationships
    charges = relationship("Charge", back_populates="provider")
    payments = relationship("Payment", back_populates="provider")

    @hybrid_property
    def last_charge_date(self):
        """Get the most recent charge date for this provider."""
        if self.charges:
            return max(
                charge.service_date for charge in self.charges if charge.service_date
            )
        return None

    @last_charge_date.expression
    def last_charge_date(cls):
        """SQL expression for the most recent charge date."""
        from sqlalchemy import func, select

        return (
            select(func.max(Charge.service_date))
            .where(Charge.provider_id == cls.id)
            .scalar_subquery()
        )

    @hybrid_property
    def last_payment_date(self):
        """Get the most recent payment date for this provider."""
        if self.payments:
            return max(
                payment.payment_date
                for payment in self.payments
                if payment.payment_date
            )
        return None

    @last_payment_date.expression
    def last_payment_date(cls):
        """SQL expression for the most recent payment date."""
        from sqlalchemy import func, select

        return (
            select(func.max(Payment.payment_date))
            .where(Payment.provider_id == cls.id)
            .scalar_subquery()
        )


class Charge(Base):
    __tablename__ = "charges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    excel_legacy_id = Column(String, comment="Legacy identifier from Excel spreadsheet")
    service_date = Column(Date)
    family_member = Column(String)
    medical_type = Column(String)
    cpt_code = Column(String)
    provider_id = Column(Integer, ForeignKey("providers.id"))
    doctor_name = Column(String)
    charge_description = Column(String)
    base_fee_amount = Column(Numeric(10, 2))
    charged_amount = Column(Numeric(10, 2))
    owed_amount = Column(Numeric(10, 2))
    reimbursement_amount = Column(
        Numeric(10, 2), comment="Amount reimbursed by HSA / FSA"
    )
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
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_date = Column(Date)
    payment_amount = Column(Numeric(10, 2))
    expense_type = Column(String)
    provider_id = Column(Integer, ForeignKey("providers.id"))
    provider_name_legacy = Column(
        String, comment="From legacy spreadsheet, now mapped via provider_id"
    )
    link_to_legacy_charges = Column(String)
    has_receipt_indicator = Column(String)
    transaction_match_flag = Column(String)
    reimbursement_amount = Column(
        Numeric(10, 2), comment="Amount reimbursed by HSA / FSA"
    )
    notes = Column(Text)

    # Relationships
    provider = relationship("Provider", back_populates="payments")
    payment_charges = relationship("ChargePaymentXref", back_populates="payment")
    attachments = relationship("Attachment", back_populates="payment")


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charge_id = Column(Integer, ForeignKey("charges.id"))
    payment_id = Column(Integer, ForeignKey("payments.id"))
    original_filename = Column(String)
    content_type = Column(String)
    file_size = Column(Integer)
    content = Column(
        Text
    )  # Depending on prior implementation this could be LargeBinary
    notes = Column(Text)

    charge = relationship("Charge", back_populates="attachments")
    payment = relationship("Payment", back_populates="attachments")


class ChargePaymentXref(Base):
    __tablename__ = "charge_payment_xref"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charge_id = Column(Integer, ForeignKey("charges.id"))
    payment_id = Column(Integer, ForeignKey("payments.id"))

    charge = relationship("Charge", back_populates="charge_payments")
    payment = relationship("Payment", back_populates="payment_charges")


class Premium(Base):
    __tablename__ = "premiums"

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False, unique=True)
    medical_premium = Column(Numeric(12, 2))
    dental_premium = Column(Numeric(12, 2))
    vision_premium = Column(Numeric(12, 2))
    hsa_fsa_paid = Column(Numeric(12, 2))
