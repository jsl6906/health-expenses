"""Schemas for the insurance paper-claim workflow (MHBP / Aetna style form).

The same `ClaimForm` structure is used to prefill the HTML editor, to render
the PDF, and as the payload of the email endpoint.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class ClaimInsuranceInfo(BaseModel):
    id: Optional[int] = None
    plan_name: Optional[str] = None
    medical_type: Optional[str] = None
    group_number: Optional[str] = None
    claim_email: Optional[str] = None
    claim_mailing_address: Optional[str] = None


class ClaimMemberInfo(BaseModel):
    family_member_id: Optional[int] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_initial: Optional[str] = None
    member_id: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None


class ClaimPatientInfo(BaseModel):
    family_member_id: Optional[int] = None
    is_dependent: bool = False
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_initial: Optional[str] = None
    patient_id: Optional[str] = None
    dob: Optional[date] = None
    relationship: Optional[str] = None


class ClaimAccidentInfo(BaseModel):
    is_accident: bool = False
    accident_date: Optional[date] = None
    location: Optional[str] = None
    employment_related: Optional[bool] = None
    auto: bool = False
    other: bool = False
    description: Optional[str] = None
    first_consulted_date: Optional[date] = None
    similar_symptoms: Optional[bool] = None


class ClaimMedicareInfo(BaseModel):
    eligible: bool = False
    medicare_number: Optional[str] = None
    part_a_date: Optional[date] = None
    part_b_date: Optional[date] = None


class ClaimOtherInsurance(BaseModel):
    no_other_coverage: bool = True
    policyholder_name: Optional[str] = None
    policy_number: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class ClaimSignature(BaseModel):
    signer_family_member_id: Optional[int] = None
    signer_name: Optional[str] = None
    signature_date: Optional[date] = None
    include_signature: bool = True


class ClaimServiceLine(BaseModel):
    charge_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    place_of_service: Optional[str] = "11"
    cpt_code: Optional[str] = None
    description: Optional[str] = None
    charges: Optional[Decimal] = None
    units: Optional[Decimal] = Decimal(1)


class ClaimProviderStatement(BaseModel):
    provider_id: Optional[int] = None
    facility_name_address: Optional[str] = None
    date_admitted: Optional[date] = None
    date_discharged: Optional[date] = None
    diagnosis_codes: List[str] = Field(default_factory=list)
    service_lines: List[ClaimServiceLine] = Field(default_factory=list)
    total_charge: Optional[Decimal] = None
    amount_paid: Optional[Decimal] = None
    balance_due: Optional[Decimal] = None
    provider_name: Optional[str] = None
    tax_id: Optional[str] = None
    npi_number: Optional[str] = None
    provider_address: Optional[str] = None
    provider_phone: Optional[str] = None


class ClaimForm(BaseModel):
    claim_key: Optional[str] = None
    charge_ids: List[int] = Field(default_factory=list)
    insurance: ClaimInsuranceInfo = Field(default_factory=ClaimInsuranceInfo)
    member: ClaimMemberInfo = Field(default_factory=ClaimMemberInfo)
    patient: ClaimPatientInfo = Field(default_factory=ClaimPatientInfo)
    accident: ClaimAccidentInfo = Field(default_factory=ClaimAccidentInfo)
    medicare: ClaimMedicareInfo = Field(default_factory=ClaimMedicareInfo)
    other_insurance: ClaimOtherInsurance = Field(default_factory=ClaimOtherInsurance)
    authorization: ClaimSignature = Field(default_factory=ClaimSignature)
    assignment_of_benefits: ClaimSignature = Field(
        default_factory=lambda: ClaimSignature(include_signature=False)
    )
    provider_statement: ClaimProviderStatement = Field(
        default_factory=ClaimProviderStatement
    )
    notes: Optional[str] = None


class ClaimEmailRequest(BaseModel):
    # Either a single claim or several; all are combined into one PDF / one email
    claim: Optional[ClaimForm] = None
    claims: List[ClaimForm] = Field(default_factory=list)
    to: str
    cc: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    mark_submitted: bool = True
    submitted_date: Optional[date] = None
    # Existing charge attachments (itemized bills / receipts) to include in the email
    attachment_ids: List[int] = Field(default_factory=list)

    def all_claims(self) -> List[ClaimForm]:
        return ([self.claim] if self.claim else []) + list(self.claims)


class ClaimBatchPdfRequest(BaseModel):
    claims: List[ClaimForm]


class ClaimEmailResponse(BaseModel):
    sent: bool
    to: str
    cc: Optional[str] = None
    subject: str
    filename: str
    charges_marked: int = 0
    attachments_included: List[str] = Field(default_factory=list)
    claims_included: int = 1
