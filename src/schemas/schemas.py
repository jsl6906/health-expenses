"""
Pydantic schemas for API request and response models.
"""

from datetime import date
from typing import Optional
from decimal import Decimal
from pydantic import BaseModel, ConfigDict
from enum import Enum


class FamilyMemberEnum(str, Enum):
    ALEX = "Alex"
    JORDAN = "Jordan"
    SAM = "Sam"
    RILEY = "Riley"


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
    one_way_miles: Optional[Decimal] = None
    active_provider_flag: bool = True


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    provider_name: Optional[str] = None
    provider_group: Optional[str] = None
    provider_address: Optional[str] = None
    one_way_miles: Optional[Decimal] = None
    active_provider_flag: Optional[bool] = None


class Provider(ProviderBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# Charge Schemas
class ChargeBase(BaseModel):
    excel_legacy_id: Optional[str] = None
    service_date: Optional[date] = None
    family_member: Optional[FamilyMemberEnum] = None
    medical_type: Optional[MedicalTypeEnum] = None
    cpt_code: Optional[str] = None
    provider_id: Optional[int] = None
    doctor_name: Optional[str] = None
    charge_description: Optional[str] = None
    base_fee_amount: Optional[Decimal] = None
    charged_amount: Optional[Decimal] = None
    owed_amount: Optional[Decimal] = None
    reimbursement_amount: Optional[Decimal] = None
    eob_claim_num: Optional[str] = None
    insurance_discount_amount: Optional[Decimal] = None
    insurance_paid_amount: Optional[Decimal] = None
    ignore_for_issue_spotting: bool = False
    notes: Optional[str] = None


class ChargeCreate(ChargeBase):
    pass


class ChargeUpdate(BaseModel):
    excel_legacy_id: Optional[str] = None
    service_date: Optional[date] = None
    family_member: Optional[FamilyMemberEnum] = None
    medical_type: Optional[MedicalTypeEnum] = None
    cpt_code: Optional[str] = None
    provider_id: Optional[int] = None
    doctor_name: Optional[str] = None
    charge_description: Optional[str] = None
    base_fee_amount: Optional[Decimal] = None
    charged_amount: Optional[Decimal] = None
    owed_amount: Optional[Decimal] = None
    reimbursement_amount: Optional[Decimal] = None
    eob_claim_num: Optional[str] = None
    insurance_discount_amount: Optional[Decimal] = None
    insurance_paid_amount: Optional[Decimal] = None
    ignore_for_issue_spotting: Optional[bool] = None
    notes: Optional[str] = None


class Charge(ChargeBase):
    id: int
    provider: Optional[Provider] = None
    
    model_config = ConfigDict(from_attributes=True)


# Payment Schemas
class PaymentBase(BaseModel):
    payment_date: Optional[date] = None
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
    payment_date: Optional[date] = None
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