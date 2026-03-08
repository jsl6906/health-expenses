"""SQLAlchemy ORM models for Personal Expenses application.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class FamilyMember(Base):
    __tablename__ = "family_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=True)
    middle_initial = Column(String, nullable=True)
    address = Column(String, nullable=True)
    dob = Column(Date, nullable=True)
    # New fields added: SSN, gender, relationship_to_primary, phone_number
    ssn = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    relationship_to_primary = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    # Use updated_at for modification timestamps; remove duplicate modified_at field
    # Single-character emoji for condensed display (may be multi-codepoint but stored as short text)
    emoji = Column(String(4), nullable=True)
    created_by = Column(String)
    updated_by = Column(String)

    charges = relationship("Charge", back_populates="family_member")

    @hybrid_property
    def full_name(self):
        """Full name including middle initial."""
        parts = [self.first_name or ""]  # type: ignore
        if self.middle_initial:  # type: ignore
            parts.append(self.middle_initial)
        if self.last_name:  # type: ignore
            parts.append(self.last_name)
        return " ".join(parts).strip() or None  # type: ignore


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_name = Column(String, nullable=False)
    provider_group = Column(String)
    provider_address = Column(String)
    phone_num = Column(String)
    npi_number = Column(String)
    tax_id = Column(String)
    one_way_miles = Column(Numeric(10, 2))
    active_provider_flag = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)

    # Relationships
    charges = relationship("Charge", back_populates="provider")
    payments = relationship("Payment", back_populates="provider")

    @hybrid_property  # type: ignore
    def last_charge_date(self):  # type: ignore
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

    @hybrid_property  # type: ignore
    def last_payment_date(self):  # type: ignore
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
    family_member_id = Column(Integer, ForeignKey("family_members.id"))
    medical_type = Column(String)
    cpt_code = Column(String)
    diagnosis_code = Column(String)
    claim_submitted_date = Column(Date)
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
    not_covered_by_insurance = Column(
        Boolean, default=False, comment="Self-pay: charge is not subject to insurance"
    )
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)

    # Relationships
    provider = relationship("Provider", back_populates="charges")
    family_member = relationship("FamilyMember", back_populates="charges")
    charge_payments = relationship("ChargePaymentXref", back_populates="charge")
    attachments = relationship("Attachment", back_populates="charge")

    @hybrid_property  # type: ignore
    def insurance_submission_status(self):  # type: ignore
        """Determine insurance submission status based on charge fields."""
        # Self-pay charges are never subject to insurance
        if self.not_covered_by_insurance:  # type: ignore
            return "Self Pay"
        # $0 owed is always N/A regardless of other fields
        if not self.owed_amount or self.owed_amount <= 0:  # type: ignore
            return "N/A"
        if self.insurance_paid_amount or self.eob_claim_num:  # type: ignore
            # Check if any linked payment is a reimbursement-type payment
            has_reimb_payment = any(
                xref.payment
                and (
                    (
                        xref.payment.payment_amount is not None
                        and xref.payment.payment_amount < 0
                    )
                    or (
                        xref.payment.reimbursement_amount is not None
                        and xref.payment.reimbursement_amount != 0
                    )
                )
                for xref in (self.charge_payments or [])  # type: ignore
            )
            # Compute total applied from non-reimbursement payments only
            # (exclude negative/refund payments and payments with reimbursement_amount)
            non_reimb_applied = sum(
                float(xref.allocated_amount or 0)
                for xref in (self.charge_payments or [])  # type: ignore
                if xref.payment
                and (
                    (
                        xref.payment.payment_amount is None
                        or xref.payment.payment_amount >= 0
                    )
                    and not (
                        xref.payment.reimbursement_amount
                        and xref.payment.reimbursement_amount != 0
                    )
                )
            )
            owed = float(self.owed_amount or 0)  # type: ignore
            # If non-reimbursement payments exceed owed AND no reimbursement
            # payment is linked, the reimbursement check hasn't been cashed
            # yet — classify as Insurance Processed
            if non_reimb_applied > owed and not has_reimb_payment:
                return "Insurance Processed"
            # Charge with an insurance_paid_amount is reimbursed
            if self.insurance_paid_amount:  # type: ignore
                return "Insurance Reimbursed"
            # Otherwise check if a corresponding reimbursement payment exists
            if self.charge_payments:  # type: ignore
                for xref in self.charge_payments:  # type: ignore
                    pmt = xref.payment
                    if pmt and (
                        (pmt.payment_amount is not None and pmt.payment_amount < 0)
                        or (
                            pmt.reimbursement_amount is not None
                            and pmt.reimbursement_amount != 0
                        )
                    ):
                        return "Insurance Reimbursed"
            return "Insurance Processed"
        elif self.claim_submitted_date:  # type: ignore
            return "Insurance Submitted"
        else:
            return "Not Submitted"

    @insurance_submission_status.expression
    def insurance_submission_status(cls):
        """SQL expression for insurance submission status."""
        from sqlalchemy import and_, case, exists, func, or_, select

        has_reimbursement = exists(
            select(ChargePaymentXref.id)
            .join(Payment, ChargePaymentXref.payment_id == Payment.id)
            .where(
                ChargePaymentXref.charge_id == cls.id,
                or_(
                    Payment.payment_amount < 0,
                    func.coalesce(Payment.reimbursement_amount, 0) != 0,
                ),
            )
        )

        insurance_processed = or_(
            func.coalesce(cls.insurance_paid_amount, 0) > 0,
            cls.eob_claim_num.isnot(None),
        )

        has_insurance_paid = func.coalesce(cls.insurance_paid_amount, 0) > 0

        has_owed = func.coalesce(cls.owed_amount, 0) > 0

        # Sum of non-reimbursement applied payments for this charge
        # (exclude negative/refund payments and payments with reimbursement_amount)
        non_reimb_applied = (
            select(func.coalesce(func.sum(ChargePaymentXref.allocated_amount), 0))
            .join(Payment, ChargePaymentXref.payment_id == Payment.id)
            .where(
                ChargePaymentXref.charge_id == cls.id,
                Payment.payment_amount >= 0,
                func.coalesce(Payment.reimbursement_amount, 0) == 0,
            )
            .correlate(cls)
            .scalar_subquery()
        )
        overpaid = non_reimb_applied > func.coalesce(cls.owed_amount, 0)

        is_self_pay = func.coalesce(cls.not_covered_by_insurance, False) == True

        return case(
            # Self-pay charges are never subject to insurance
            (is_self_pay, "Self Pay"),
            # $0 owed is always N/A
            (~has_owed, "N/A"),
            # Overpaid + insurance processed but NO reimbursement payment
            # → check not cashed yet
            (
                and_(insurance_processed, overpaid, ~has_reimbursement),
                "Insurance Processed",
            ),
            (has_insurance_paid, "Insurance Reimbursed"),
            (and_(insurance_processed, has_reimbursement), "Insurance Reimbursed"),
            (insurance_processed, "Insurance Processed"),
            (cls.claim_submitted_date.isnot(None), "Insurance Submitted"),
            else_="Not Submitted",
        )


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
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)

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
    content = Column(LargeBinary)  # store raw file bytes for attachments
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)

    charge = relationship("Charge", back_populates="attachments")
    payment = relationship("Payment", back_populates="attachments")


class ChargePaymentXref(Base):
    __tablename__ = "charge_payment_xref"

    id = Column(Integer, primary_key=True, autoincrement=True)
    charge_id = Column(Integer, ForeignKey("charges.id"))
    payment_id = Column(Integer, ForeignKey("payments.id"))
    allocated_amount = Column(Numeric(10, 2), nullable=False, default=0.0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)

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
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)


class RefExpenseTypes(Base):
    __tablename__ = "ref_expense_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    expense_type_name = Column(String)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)


class Insurance(Base):
    __tablename__ = "insurance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    medical_type = Column(String)
    plan_name = Column(String)
    policy_number = Column(String)
    group_number = Column(String)
    insured_member_id = Column(Integer, ForeignKey("family_members.id"))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_by = Column(String)
    updated_by = Column(String)

    insured_member = relationship("FamilyMember")
