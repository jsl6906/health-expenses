"""
Pydantic schemas for API request and response models.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict


def normalize_date(value: Optional[date]) -> Optional[date]:
    """Browser date inputs accept 2-digit years (e.g. '25' -> 0025); treat those as 20xx and reject other pre-1900 dates."""
    if value is None:
        return None
    if value.year < 100:
        return value.replace(year=value.year + 2000)
    if value.year < 1900:
        raise ValueError(f"Invalid year {value.year}; dates must be 1900 or later")
    return value


SafeDate = Annotated[date, AfterValidator(normalize_date)]


class MedicalTypeEnum(str, Enum):
    MEDICAL = "Medical"
    DENTAL = "Dental"
    VISION = "Vision"
    MENTAL_HEALTH = "Mental Health"
    OTHER = "Other"


class ExpenseTypeEnum(str, Enum):
    MEDICAL = "Medical"
    DENTAL = "Dental"
    VISION = "Vision"
    HSA_FSA = "HSA / FSA"
    PROFESSIONAL = "Professional"
    PRESCRIPTION = "Prescription"
    EYEWEAR = "Eyewear"
    TRAVEL = "Travel"
    OTHER = "Other"


# Provider Schemas
class ProviderBase(BaseModel):
    provider_name: str
    provider_group: Optional[str] = None
    provider_address: Optional[str] = None
    phone_num: Optional[str] = None
    npi_number: Optional[str] = None
    tax_id: Optional[str] = None
    one_way_miles: Optional[Decimal] = None
    active_provider_flag: bool = True


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    provider_name: Optional[str] = None
    provider_group: Optional[str] = None
    provider_address: Optional[str] = None
    phone_num: Optional[str] = None
    npi_number: Optional[str] = None
    tax_id: Optional[str] = None
    one_way_miles: Optional[Decimal] = None
    active_provider_flag: Optional[bool] = None


class Provider(ProviderBase):
    id: int
    last_charge_date: Optional[date] = None
    last_payment_date: Optional[date] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# Charge Schemas
class ChargeBase(BaseModel):
    excel_legacy_id: Optional[str] = None
    service_date: Optional[SafeDate] = None
    family_member_id: Optional[int] = None
    medical_type: Optional[MedicalTypeEnum] = None
    cpt_code: Optional[str] = None
    diagnosis_code: Optional[str] = None
    claim_submitted_date: Optional[SafeDate] = None
    provider_id: Optional[int] = None
    doctor_name: Optional[str] = None
    charge_description: Optional[str] = None
    base_fee_amount: Optional[Decimal] = None
    charged_amount: Optional[Decimal] = None
    owed_amount: Optional[Decimal] = None
    reimbursement_amount: Optional[Decimal] = None
    eob_claim_num: Optional[str] = None
    eob_claim_id: Optional[str] = None
    insurance_discount_amount: Optional[Decimal] = None
    insurance_paid_amount: Optional[Decimal] = None
    ignore_for_issue_spotting: bool = False
    not_covered_by_insurance: bool = False
    notes: Optional[str] = None


class ChargeCreate(ChargeBase):
    pass


class ChargeUpdate(BaseModel):
    excel_legacy_id: Optional[str] = None
    service_date: Optional[SafeDate] = None
    family_member_id: Optional[int] = None
    medical_type: Optional[MedicalTypeEnum] = None
    cpt_code: Optional[str] = None
    diagnosis_code: Optional[str] = None
    claim_submitted_date: Optional[SafeDate] = None
    provider_id: Optional[int] = None
    doctor_name: Optional[str] = None
    charge_description: Optional[str] = None
    base_fee_amount: Optional[Decimal] = None
    charged_amount: Optional[Decimal] = None
    owed_amount: Optional[Decimal] = None
    reimbursement_amount: Optional[Decimal] = None
    eob_claim_num: Optional[str] = None
    eob_claim_id: Optional[str] = None
    insurance_discount_amount: Optional[Decimal] = None
    insurance_paid_amount: Optional[Decimal] = None
    ignore_for_issue_spotting: Optional[bool] = None
    not_covered_by_insurance: Optional[bool] = None
    notes: Optional[str] = None


class Charge(ChargeBase):
    id: int
    provider: Optional[Provider] = None
    family_member: Optional["FamilyMember"] = None
    insurance_submission_status: Optional[str] = None


# FamilyMember Schemas
class FamilyMemberBase(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    middle_initial: Optional[str] = None
    address: Optional[str] = None
    ssn: Optional[str] = None
    gender: Optional[str] = None
    relationship_to_primary: Optional[str] = None
    phone_number: Optional[str] = None
    dob: Optional[SafeDate] = None
    emoji: Optional[str] = None


class FamilyMemberCreate(FamilyMemberBase):
    pass


class FamilyMemberUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_initial: Optional[str] = None
    address: Optional[str] = None
    ssn: Optional[str] = None
    gender: Optional[str] = None
    relationship_to_primary: Optional[str] = None
    phone_number: Optional[str] = None
    dob: Optional[SafeDate] = None
    emoji: Optional[str] = None


class FamilyMember(FamilyMemberBase):
    id: int
    has_signature: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    # 'modified_at' removed in favor of 'updated_at'

    model_config = ConfigDict(from_attributes=True)


# Payment Schemas
class PaymentBase(BaseModel):
    payment_date: Optional[SafeDate] = None
    payment_amount: Optional[Decimal] = None
    expense_type: Optional[ExpenseTypeEnum] = None
    provider_id: Optional[int] = None
    provider_name_legacy: Optional[str] = None
    link_to_legacy_charges: Optional[str] = None
    has_receipt_indicator: Optional[str] = None
    transaction_match_flag: Optional[str] = None
    reimbursement_amount: Optional[Decimal] = None
    notes: Optional[str] = None


class PaymentCreate(PaymentBase):
    pass


class PaymentUpdate(BaseModel):
    payment_date: Optional[SafeDate] = None
    payment_amount: Optional[Decimal] = None
    expense_type: Optional[ExpenseTypeEnum] = None
    provider_id: Optional[int] = None
    provider_name_legacy: Optional[str] = None
    link_to_legacy_charges: Optional[str] = None
    has_receipt_indicator: Optional[str] = None
    transaction_match_flag: Optional[str] = None
    reimbursement_amount: Optional[Decimal] = None
    notes: Optional[str] = None


class Payment(PaymentBase):
    id: int
    provider: Optional[Provider] = None

    model_config = ConfigDict(from_attributes=True)


# Premium Schemas
class PremiumBase(BaseModel):
    year: int
    medical_premium: Optional[Decimal] = None
    dental_premium: Optional[Decimal] = None
    vision_premium: Optional[Decimal] = None
    hsa_fsa_paid: Optional[Decimal] = None


class PremiumCreate(PremiumBase):
    pass


class PremiumUpdate(BaseModel):
    year: Optional[int] = None
    medical_premium: Optional[Decimal] = None
    dental_premium: Optional[Decimal] = None
    vision_premium: Optional[Decimal] = None
    hsa_fsa_paid: Optional[Decimal] = None


class Premium(PremiumBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


# Attachment Schemas
class AttachmentBase(BaseModel):
    charge_id: Optional[int] = None
    payment_id: Optional[int] = None
    original_filename: Optional[str] = None
    content_type: Optional[str] = None
    file_size: Optional[int] = None
    notes: Optional[str] = None


class AttachmentCreate(BaseModel):
    charge_id: Optional[int] = None
    payment_id: Optional[int] = None
    notes: Optional[str] = None


class Attachment(AttachmentBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# Insurance Schemas
class InsuranceClaimDefaults(BaseModel):
    claim_email: Optional[str] = None
    claim_mailing_address: Optional[str] = None
    no_other_coverage: Optional[bool] = True
    other_policyholder_name: Optional[str] = None
    other_policy_number: Optional[str] = None
    other_insurance_company: Optional[str] = None
    other_insurance_phone: Optional[str] = None
    other_insurance_address: Optional[str] = None
    other_insurance_city: Optional[str] = None
    other_insurance_state: Optional[str] = None
    other_insurance_zip: Optional[str] = None
    medicare_number: Optional[str] = None
    medicare_part_a_date: Optional[date] = None
    medicare_part_b_date: Optional[date] = None


class InsuranceBase(InsuranceClaimDefaults):
    medical_type: Optional[MedicalTypeEnum] = None
    plan_name: Optional[str] = None
    policy_number: Optional[str] = None
    group_number: Optional[str] = None
    insured_member_id: Optional[int] = None


class InsuranceCreate(InsuranceBase):
    pass


class InsuranceUpdate(InsuranceClaimDefaults):
    no_other_coverage: Optional[bool] = None
    medical_type: Optional[MedicalTypeEnum] = None
    plan_name: Optional[str] = None
    policy_number: Optional[str] = None
    group_number: Optional[str] = None
    insured_member_id: Optional[int] = None


class Insurance(InsuranceBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
