import math
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# We'll create an in-memory sqlite DB and create minimal tables using the project's models
from src.models import database as models

# Import the functions under test
from src.services.allocation import (
    allocate_payment_to_charges,
    annotate_charges_with_paid,
    even_allocation_map,
    get_open_charges_for_provider,
)


@pytest.fixture(scope="module")
def db_session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_allocate_positive_payment_simple(db_session):
    # Create two charges, owed amounts 100 and 50
    c1 = models.Charge(owed_amount=100.0)
    c2 = models.Charge(owed_amount=50.0)
    db_session.add_all([c1, c2])
    db_session.commit()

    # Allocate a payment of 75 across them
    # Use integer ids (SQLAlchemy Column proxies may present typed Column objects
    # until flushed; cast to int to ensure plain ints are passed)
    cid1 = cast(int, getattr(c1, "id"))
    cid2 = cast(int, getattr(c2, "id"))
    alloc = allocate_payment_to_charges(db_session, 75.0, [cid1, cid2])
    # First should get 75 (up to owed of 100) and second 0 in priority logic
    assert math.isclose(alloc[cid1], 75.0, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(alloc[cid2], 0.0, rel_tol=1e-9, abs_tol=1e-9)


def test_allocate_negative_payment_refund(db_session):
    # Reset DB for clarity
    db_session.query(models.ChargePaymentXref).delete()
    db_session.query(models.Payment).delete()
    db_session.query(models.Charge).delete()
    db_session.commit()

    # Create two charges and a payment previously applied
    c1 = models.Charge(owed_amount=100.0)
    c2 = models.Charge(owed_amount=50.0)
    db_session.add_all([c1, c2])
    db_session.commit()

    # Simulate an existing payment of 100 linked evenly to both charges
    p = models.Payment(payment_amount=100.0)
    db_session.add(p)
    db_session.commit()
    # Link payment to charges (even allocation fallback path)
    x1 = models.ChargePaymentXref(
        charge_id=c1.id, payment_id=p.id, allocated_amount=50.0
    )
    x2 = models.ChargePaymentXref(
        charge_id=c2.id, payment_id=p.id, allocated_amount=50.0
    )
    db_session.add_all([x1, x2])
    db_session.commit()

    # Now process a refund of -30; expect refund to reduce paid amounts preferring higher-paid/owed
    cid1 = cast(int, getattr(c1, "id"))
    cid2 = cast(int, getattr(c2, "id"))
    alloc = allocate_payment_to_charges(db_session, -30.0, [cid1, cid2])
    # Because c1 has larger owed and/or paid, it should typically absorb more of the refund
    # The exact distribution per our algorithm is proportional to remaining paid capacities sorted desc
    assert math.isclose(sum(alloc.values()), -30.0, rel_tol=1e-9, abs_tol=1e-9)
    # Allocations should be negative or zero
    assert all(a <= 0 for a in alloc.values())


def test_even_allocation_map_respects_negative_allocs(db_session):
    # Clear and setup
    db_session.query(models.ChargePaymentXref).delete()
    db_session.query(models.Payment).delete()
    db_session.query(models.Charge).delete()
    db_session.commit()

    c1 = models.Charge(owed_amount=100.0)
    c2 = models.Charge(owed_amount=100.0)
    db_session.add_all([c1, c2])
    db_session.commit()

    p = models.Payment(payment_amount=-40.0)
    db_session.add(p)
    db_session.commit()

    # Explicit allocated negative amounts
    x1 = models.ChargePaymentXref(
        charge_id=c1.id, payment_id=p.id, allocated_amount=-10.0
    )
    x2 = models.ChargePaymentXref(
        charge_id=c2.id, payment_id=p.id, allocated_amount=-30.0
    )
    db_session.add_all([x1, x2])
    db_session.commit()

    cid1 = cast(int, getattr(c1, "id"))
    cid2 = cast(int, getattr(c2, "id"))
    alloc = even_allocation_map(db_session, [cid1, cid2])
    assert math.isclose(alloc[cid1], -10.0, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(alloc[cid2], -30.0, rel_tol=1e-9, abs_tol=1e-9)


def test_charge_payment_status_uses_cents(db_session):
    db_session.query(models.ChargePaymentXref).delete()
    db_session.query(models.Payment).delete()
    db_session.query(models.Charge).delete()
    db_session.commit()

    charge = models.Charge(owed_amount=0.80)
    db_session.add(charge)
    db_session.flush()
    for amount in (0.10, 0.70):
        payment = models.Payment(payment_amount=amount)
        db_session.add(payment)
        db_session.flush()
        db_session.add(
            models.ChargePaymentXref(
                charge_id=charge.id, payment_id=payment.id, allocated_amount=amount
            )
        )
    db_session.commit()

    annotate_charges_with_paid(db_session, [charge])
    assert charge.paid_amount_agg < float(charge.owed_amount)
    assert charge.payment_status == "paid"

    charge.owed_amount = 0.90
    annotate_charges_with_paid(db_session, [charge])
    assert charge.payment_status == "partial"

    charge.owed_amount = 0
    annotate_charges_with_paid(db_session, [charge])
    assert charge.payment_status == "na"


def test_fully_paid_charge_is_not_open_for_provider(db_session):
    db_session.query(models.ChargePaymentXref).delete()
    db_session.query(models.Payment).delete()
    db_session.query(models.Charge).delete()
    db_session.commit()

    charge = models.Charge(provider_id=42, owed_amount=0.80)
    db_session.add(charge)
    db_session.flush()
    for amount in (0.10, 0.70):
        payment = models.Payment(payment_amount=amount)
        db_session.add(payment)
        db_session.flush()
        db_session.add(
            models.ChargePaymentXref(
                charge_id=charge.id, payment_id=payment.id, allocated_amount=amount
            )
        )
    db_session.commit()

    assert get_open_charges_for_provider(db_session, 42) == []
