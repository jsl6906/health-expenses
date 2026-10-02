from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import database as models
from src.services.claim_form import build_claim_forms, claim_warnings
from src.services.claim_pdf import (
    claim_pdf_filename,
    claims_pdf_filename,
    render_claim_pdf,
    render_claims_pdf,
)


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGBA", (300, 80), (0, 0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    alex = models.FamilyMember(
        id=1, first_name="Alex", last_name="Smith", middle_initial="A",
        address="1 Main St, Town, ST 00000", phone_number="5551234567",
        signature_image=PNG, signature_content_type="image/png",
    )
    kid = models.FamilyMember(
        id=2, first_name="Sam", last_name="Smith", dob=date(2015, 6, 1),
        relationship_to_primary="Child",
    )
    prov = models.Provider(
        id=1, provider_name="Therapy LLC", tax_id="12-3456789", npi_number="1234567890",
        provider_address="2 Clinic Rd", phone_num="5559876543",
    )
    ins = models.Insurance(
        id=1, medical_type="Mental Health", plan_name="MHBP Standard",
        policy_number="R12345678", group_number="G1", insured_member_id=1,
        claim_email="MHBPeligibility@aetna.com", no_other_coverage=True,
    )
    s.add_all([alex, kid, prov, ins])
    s.add_all(
        [
            models.Charge(id=10, provider_id=1, family_member_id=2, medical_type="Mental Health",
                          service_date=date(2026, 1, 5), cpt_code="90837", diagnosis_code="F41.1, F33.1",
                          charge_description="Psychotherapy 60 min", base_fee_amount=Decimal("150.00")),
            models.Charge(id=11, provider_id=1, family_member_id=2, medical_type="Mental Health",
                          service_date=date(2026, 1, 12), cpt_code="90837", diagnosis_code="F41.1",
                          charge_description="Psychotherapy 60 min", base_fee_amount=Decimal("150.00")),
            models.Charge(id=12, provider_id=1, family_member_id=1, medical_type="Mental Health",
                          service_date=date(2026, 1, 7), cpt_code="90834", base_fee_amount=Decimal("120.00")),
            models.Payment(id=100, provider_id=1, payment_date=date(2026, 1, 20), payment_amount=Decimal("300.00")),
        ]
    )
    s.add_all(
        [
            models.ChargePaymentXref(payment_id=100, charge_id=10, allocated_amount=150),
            models.ChargePaymentXref(payment_id=100, charge_id=11, allocated_amount=150),
        ]
    )
    s.commit()
    yield s
    s.close()


def test_build_claim_forms_groups_by_patient_and_provider(db):
    forms = build_claim_forms(db, [10, 11, 12])
    assert len(forms) == 2
    by_patient = {f.patient.family_member_id: f for f in forms}

    kid = by_patient[2]
    assert kid.member.member_id == "R12345678"
    assert kid.member.last_name == "Smith" and kid.member.first_name == "Alex"
    assert kid.patient.is_dependent and kid.patient.relationship == "Child"
    assert kid.provider_statement.diagnosis_codes == ["F41.1", "F33.1"]
    assert len(kid.provider_statement.service_lines) == 2
    assert kid.provider_statement.total_charge == Decimal("300.00")
    assert kid.provider_statement.amount_paid == Decimal("300.00")
    assert kid.provider_statement.balance_due == Decimal("0.00")
    assert kid.insurance.claim_email == "MHBPeligibility@aetna.com"
    assert kid.authorization.signer_family_member_id == 1
    assert kid.assignment_of_benefits.include_signature is False

    me = by_patient[1]
    assert me.patient.is_dependent is False
    assert me.provider_statement.balance_due == Decimal("120.00")
    assert any("diagnosis" in w for w in claim_warnings(me, signature_on_file=True))


def test_render_pdf_with_signature_and_many_lines(db):
    form = build_claim_forms(db, [10, 11])[0]
    # Simulate a long claim: many service lines should paginate without error
    line = form.provider_statement.service_lines[0]
    form.provider_statement.service_lines = [
        line.model_copy(update={"date_from": date(2026, 1, 1 + i % 28), "date_to": date(2026, 1, 1 + i % 28)})
        for i in range(40)
    ]
    pdf = render_claim_pdf(form, PNG)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2000
    assert claim_pdf_filename(form).startswith("claim_MHBP_Standard_Smith_Sam_")


def test_render_pdf_without_signature_and_optional_sections(db):
    form = build_claim_forms(db, [12])[0]
    form.accident.is_accident = True
    form.accident.description = "Fell"
    form.medicare.eligible = True
    form.medicare.medicare_number = "1EG4-TE5-MK72"
    form.other_insurance.no_other_coverage = False
    form.other_insurance.company = "Other Co"
    form.assignment_of_benefits.include_signature = True
    pdf = render_claim_pdf(form, None)
    assert pdf.startswith(b"%PDF")


def test_render_combined_pdf_for_multiple_claims(db):
    from pypdf import PdfReader

    forms = build_claim_forms(db, [10, 11, 12])
    assert len(forms) == 2
    single_pages = sum(
        len(PdfReader(BytesIO(render_claim_pdf(f, PNG))).pages) for f in forms
    )
    combined = render_claims_pdf(forms, {1: PNG})
    assert combined.startswith(b"%PDF")
    assert len(PdfReader(BytesIO(combined)).pages) == single_pages
    assert claims_pdf_filename(forms).startswith("claims_MHBP_Standard_Smith_2forms_")
