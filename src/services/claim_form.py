"""Build prefilled insurance claim forms from selected charges.

One `ClaimForm` is produced per (insurance, patient, provider) group so each
form matches the structure of a paper claim (single member/patient section plus
one provider statement with any number of service lines).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, List

from sqlalchemy.orm import Session, joinedload

try:
    from src.models.database import Charge, FamilyMember, Insurance  # type: ignore
    from src.schemas.claims import (  # type: ignore
        ClaimAccidentInfo,
        ClaimForm,
        ClaimInsuranceInfo,
        ClaimMedicareInfo,
        ClaimMemberInfo,
        ClaimOtherInsurance,
        ClaimPatientInfo,
        ClaimProviderStatement,
        ClaimServiceLine,
        ClaimSignature,
    )
    from src.services.allocation import even_allocation_map  # type: ignore
except ImportError:  # pragma: no cover
    from ..models.database import Charge, FamilyMember, Insurance  # type: ignore
    from ..schemas.claims import (  # type: ignore
        ClaimAccidentInfo,
        ClaimForm,
        ClaimInsuranceInfo,
        ClaimMedicareInfo,
        ClaimMemberInfo,
        ClaimOtherInsurance,
        ClaimPatientInfo,
        ClaimProviderStatement,
        ClaimServiceLine,
        ClaimSignature,
    )
    from .allocation import even_allocation_map  # type: ignore

DEFAULT_PLACE_OF_SERVICE = "11"  # Office


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except Exception:
        return Decimal(0)


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _member_info(fm: FamilyMember | None, member_id: str | None) -> ClaimMemberInfo:
    if fm is None:
        return ClaimMemberInfo(member_id=member_id)
    return ClaimMemberInfo(
        family_member_id=fm.id,
        last_name=fm.last_name,
        first_name=fm.first_name,
        middle_initial=fm.middle_initial,
        member_id=member_id,
        address=fm.address,
        phone=fm.phone_number,
    )


def _patient_info(
    fm: FamilyMember | None, insured: FamilyMember | None, member_id: str | None
) -> ClaimPatientInfo:
    if fm is None:
        return ClaimPatientInfo()
    is_dependent = insured is None or fm.id != insured.id
    return ClaimPatientInfo(
        family_member_id=fm.id,
        is_dependent=is_dependent,
        last_name=fm.last_name,
        first_name=fm.first_name,
        middle_initial=fm.middle_initial,
        patient_id=member_id,
        dob=fm.dob,
        relationship=fm.relationship_to_primary if is_dependent else "Self",
    )


def _other_insurance(ins: Insurance | None) -> ClaimOtherInsurance:
    if ins is None:
        return ClaimOtherInsurance()
    no_other = ins.no_other_coverage
    return ClaimOtherInsurance(
        no_other_coverage=True if no_other is None else bool(no_other),
        policyholder_name=ins.other_policyholder_name,
        policy_number=ins.other_policy_number,
        company=ins.other_insurance_company,
        phone=ins.other_insurance_phone,
        address=ins.other_insurance_address,
        city=ins.other_insurance_city,
        state=ins.other_insurance_state,
        zip=ins.other_insurance_zip,
    )


def _medicare(ins: Insurance | None) -> ClaimMedicareInfo:
    if ins is None or not ins.medicare_number:
        return ClaimMedicareInfo()
    return ClaimMedicareInfo(
        eligible=True,
        medicare_number=ins.medicare_number,
        part_a_date=ins.medicare_part_a_date,
        part_b_date=ins.medicare_part_b_date,
    )


def build_claim_forms(db: Session, charge_ids: Iterable[int]) -> List[ClaimForm]:
    ids = [int(c) for c in charge_ids]
    if not ids:
        return []

    charges = (
        db.query(Charge)
        .options(joinedload(Charge.provider), joinedload(Charge.family_member))
        .filter(Charge.id.in_(ids))
        .all()
    )
    if not charges:
        return []

    paid_map = even_allocation_map(db, [c.id for c in charges])

    insurance_by_type: dict[str, Insurance | None] = {}
    for mt in {str(c.medical_type) for c in charges if c.medical_type}:
        insurance_by_type[mt] = (
            db.query(Insurance)
            .options(joinedload(Insurance.insured_member))
            .filter(Insurance.medical_type == mt)
            .first()
        )

    groups: dict[tuple, list[Charge]] = {}
    for ch in charges:
        mt = str(ch.medical_type) if ch.medical_type else ""
        ins = insurance_by_type.get(mt)
        key = (
            ins.id if ins else f"noins:{mt}",
            ch.family_member_id,
            ch.provider_id,
        )
        groups.setdefault(key, []).append(ch)

    today = date.today()
    forms: List[ClaimForm] = []
    for (ins_key, fm_id, prov_id), group in sorted(
        groups.items(), key=lambda kv: str(kv[0])
    ):
        group.sort(key=lambda c: (c.service_date is None, c.service_date, c.id))
        first = group[0]
        mt = str(first.medical_type) if first.medical_type else ""
        ins = insurance_by_type.get(mt)
        insured = ins.insured_member if ins else None
        patient = first.family_member
        provider = first.provider
        member_id = ins.policy_number if ins else None

        lines: list[ClaimServiceLine] = []
        total = Decimal(0)
        paid = Decimal(0)
        diagnosis_codes: list[str] = []
        for ch in group:
            amount = _dec(ch.base_fee_amount)
            total += amount
            paid += _dec(paid_map.get(ch.id, 0))
            for code in str(ch.diagnosis_code or "").replace(";", ",").split(","):
                code = code.strip()
                if code and code not in diagnosis_codes:
                    diagnosis_codes.append(code)
            lines.append(
                ClaimServiceLine(
                    charge_id=ch.id,
                    date_from=ch.service_date,
                    date_to=ch.service_date,
                    place_of_service=DEFAULT_PLACE_OF_SERVICE,
                    cpt_code=ch.cpt_code,
                    description=ch.charge_description,
                    charges=_q(amount),
                    units=Decimal(1),
                )
            )

        balance = total - paid
        statement = ClaimProviderStatement(
            provider_id=provider.id if provider else None,
            diagnosis_codes=diagnosis_codes,
            service_lines=lines,
            total_charge=_q(total),
            amount_paid=_q(paid),
            balance_due=_q(balance if balance > 0 else Decimal(0)),
            provider_name=provider.provider_name if provider else None,
            tax_id=provider.tax_id if provider else None,
            npi_number=provider.npi_number if provider else None,
            provider_address=provider.provider_address if provider else None,
            provider_phone=provider.phone_num if provider else None,
        )

        signer_id = insured.id if insured else (patient.id if patient else None)
        signer_name = (
            insured.full_name if insured else (patient.full_name if patient else None)
        )
        forms.append(
            ClaimForm(
                claim_key=f"ins{ins_key}_fm{fm_id}_prov{prov_id}",
                charge_ids=[c.id for c in group],
                insurance=ClaimInsuranceInfo(
                    id=ins.id if ins else None,
                    plan_name=ins.plan_name if ins else None,
                    medical_type=mt or None,
                    group_number=ins.group_number if ins else None,
                    claim_email=ins.claim_email if ins else None,
                    claim_mailing_address=ins.claim_mailing_address if ins else None,
                ),
                member=_member_info(insured, member_id),
                patient=_patient_info(patient, insured, member_id),
                accident=ClaimAccidentInfo(),
                medicare=_medicare(ins),
                other_insurance=_other_insurance(ins),
                authorization=ClaimSignature(
                    signer_family_member_id=signer_id,
                    signer_name=signer_name,
                    signature_date=today,
                    include_signature=True,
                ),
                assignment_of_benefits=ClaimSignature(
                    signer_family_member_id=signer_id,
                    signer_name=signer_name,
                    signature_date=today,
                    include_signature=False,
                ),
                provider_statement=statement,
            )
        )
    return forms


def claim_warnings(form: ClaimForm, signature_on_file: bool) -> list[str]:
    """Human-readable data-quality warnings for a prefilled claim."""
    w: list[str] = []
    if form.insurance.id is None:
        w.append(
            f"No insurance record found for medical type '{form.insurance.medical_type or '-'}'."
        )
    if not form.member.member_id:
        w.append("Member ID number is missing (insurance policy number).")
    if not form.member.last_name or not form.member.first_name:
        w.append("Member name is incomplete.")
    if form.patient.is_dependent and not form.patient.dob:
        w.append("Patient date of birth is missing.")
    if not form.provider_statement.diagnosis_codes:
        w.append("No diagnosis codes found on the selected charges.")
    for line in form.provider_statement.service_lines:
        if not line.cpt_code:
            w.append(f"Charge {line.charge_id}: CPT code missing.")
        if not line.charges:
            w.append(f"Charge {line.charge_id}: charge amount missing.")
    ps = form.provider_statement
    missing = [
        label
        for label, val in (
            ("Tax ID", ps.tax_id),
            ("NPI", ps.npi_number),
            ("address", ps.provider_address),
            ("phone", ps.provider_phone),
        )
        if not val
    ]
    if missing:
        w.append(f"Provider is missing: {', '.join(missing)}.")
    if not signature_on_file:
        w.append(
            "No signature image on file for the signer; the signature line will be blank."
        )
    return w
