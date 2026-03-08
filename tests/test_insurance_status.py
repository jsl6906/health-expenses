"""Tests for the insurance_submission_status hybrid property,
specifically the new 'Insurance Reimbursed' status."""

from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import database as models


@pytest.fixture(scope="module")
def db_session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean_tables(db_session):
    """Clear all relevant tables before each test."""
    db_session.query(models.ChargePaymentXref).delete()
    db_session.query(models.Payment).delete()
    db_session.query(models.Charge).delete()
    db_session.commit()
    yield


def test_insurance_reimbursed_has_insurance_paid_amount(db_session):
    """Charge with insurance_paid_amount (no linked payment) -> Insurance Reimbursed."""
    c = models.Charge(
        owed_amount=100.0,
        insurance_paid_amount=80.0,
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Reimbursed"


def test_insurance_reimbursed_with_positive_payment(db_session):
    """Charge with insurance_paid_amount and a positive payment -> Insurance Reimbursed."""
    c = models.Charge(
        owed_amount=100.0,
        insurance_paid_amount=80.0,
    )
    db_session.add(c)
    db_session.commit()

    p = models.Payment(payment_amount=20.0)
    db_session.add(p)
    db_session.commit()

    xref = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p.id, allocated_amount=20.0
    )
    db_session.add(xref)
    db_session.commit()

    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Reimbursed"


def test_insurance_processed_eob_only_no_reimbursement(db_session):
    """Charge with eob_claim_num but no insurance_paid_amount and no linked reimbursement -> Insurance Processed."""
    c = models.Charge(
        owed_amount=100.0,
        eob_claim_num="EOB-99999",
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Processed"


def test_insurance_reimbursed_negative_payment(db_session):
    """Charge with insurance_paid_amount and a negative payment -> Insurance Reimbursed."""
    c = models.Charge(
        owed_amount=100.0,
        insurance_paid_amount=80.0,
    )
    db_session.add(c)
    db_session.commit()

    p = models.Payment(payment_amount=-80.0)
    db_session.add(p)
    db_session.commit()

    xref = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p.id, allocated_amount=-80.0
    )
    db_session.add(xref)
    db_session.commit()

    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Reimbursed"


def test_insurance_reimbursed_nonzero_reimbursement_amount(db_session):
    """Charge with insurance_paid_amount and payment with reimbursement_amount -> Insurance Reimbursed."""
    c = models.Charge(
        owed_amount=100.0,
        insurance_paid_amount=80.0,
    )
    db_session.add(c)
    db_session.commit()

    p = models.Payment(payment_amount=20.0, reimbursement_amount=80.0)
    db_session.add(p)
    db_session.commit()

    xref = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p.id, allocated_amount=20.0
    )
    db_session.add(xref)
    db_session.commit()

    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Reimbursed"


def test_insurance_reimbursed_eob_claim_num_only(db_session):
    """Charge with eob_claim_num (no insurance_paid_amount) and negative payment -> Insurance Reimbursed."""
    c = models.Charge(
        owed_amount=50.0,
        eob_claim_num="EOB-12345",
    )
    db_session.add(c)
    db_session.commit()

    p = models.Payment(payment_amount=-50.0)
    db_session.add(p)
    db_session.commit()

    xref = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p.id, allocated_amount=-50.0
    )
    db_session.add(xref)
    db_session.commit()

    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Reimbursed"


def test_insurance_submitted_status(db_session):
    """Charge with claim_submitted_date but no insurance processing -> Insurance Submitted."""
    from datetime import date

    c = models.Charge(
        owed_amount=100.0,
        claim_submitted_date=date(2025, 6, 1),
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Submitted"


def test_not_submitted_status(db_session):
    """Charge with owed_amount but no insurance info -> Not Submitted."""
    c = models.Charge(owed_amount=100.0)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Not Submitted"


def test_na_status(db_session):
    """Charge with no owed_amount and no insurance info -> N/A."""
    c = models.Charge()
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "N/A"


def test_na_status_zero_owed_with_insurance_paid(db_session):
    """Charge with $0 owed is N/A even if insurance_paid_amount exists."""
    c = models.Charge(
        owed_amount=0.0,
        insurance_paid_amount=80.0,
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "N/A"


def test_insurance_processed_when_overpaid(db_session):
    """Charge with insurance_paid_amount but applied payments > owed -> Insurance Processed (check not cashed)."""
    c = models.Charge(
        owed_amount=100.0,
        insurance_paid_amount=80.0,
    )
    db_session.add(c)
    db_session.commit()

    # Applied payment of 150 which exceeds the 100 owed
    p = models.Payment(payment_amount=150.0)
    db_session.add(p)
    db_session.commit()

    xref = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p.id, allocated_amount=150.0
    )
    db_session.add(xref)
    db_session.commit()

    db_session.refresh(c)
    assert c.insurance_submission_status == "Insurance Processed"


def test_insurance_reimbursed_not_downgraded_by_reimbursement_payment(db_session):
    """Charge with mixed reimbursement + non-reimbursement payments should not be
    downgraded to Insurance Processed when the total includes reimbursement payments."""
    c = models.Charge(
        owed_amount=75.0,
        insurance_paid_amount=100.0,
        eob_claim_num="EOB-TEST",
    )
    db_session.add(c)
    db_session.commit()

    # A large positive payment that has reimbursement_amount (excluded from overpaid check)
    p1 = models.Payment(payment_amount=5365.0, reimbursement_amount=3500.0)
    db_session.add(p1)
    db_session.commit()
    xref1 = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p1.id, allocated_amount=185.0
    )
    db_session.add(xref1)

    # A negative reimbursement payment (excluded from overpaid check)
    p2 = models.Payment(payment_amount=-100.0)
    db_session.add(p2)
    db_session.commit()
    xref2 = models.ChargePaymentXref(
        charge_id=c.id, payment_id=p2.id, allocated_amount=-100.0
    )
    db_session.add(xref2)
    db_session.commit()

    db_session.refresh(c)
    # Both linked payments are reimbursement-type, so non-reimbursement applied = 0
    # insurance_paid_amount = 100 -> should be Insurance Reimbursed
    assert c.insurance_submission_status == "Insurance Reimbursed"


def test_self_pay_overrides_all_other_statuses(db_session):
    """Charge with not_covered_by_insurance=True should always return 'Self Pay',
    regardless of owed_amount, insurance_paid_amount, or eob_claim_num."""
    # Self Pay even when it would otherwise be "Insurance Reimbursed"
    c = models.Charge(
        owed_amount=100.0,
        insurance_paid_amount=80.0,
        eob_claim_num="EOB-SELF-PAY",
        not_covered_by_insurance=True,
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Self Pay"


def test_self_pay_with_zero_owed(db_session):
    """Self Pay takes priority over N/A ($0 owed)."""
    c = models.Charge(
        owed_amount=0,
        not_covered_by_insurance=True,
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Self Pay"


def test_not_self_pay_unchanged(db_session):
    """Charge with not_covered_by_insurance=False should behave normally."""
    c = models.Charge(
        owed_amount=50.0,
        not_covered_by_insurance=False,
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    assert c.insurance_submission_status == "Not Submitted"
