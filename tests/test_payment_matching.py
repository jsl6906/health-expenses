from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import database as models
from src.services.payment_matching import (
    apply_link_changes,
    build_recommendations,
    find_amount_candidates,
    load_provider_snapshot,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    other = models.Provider(id=2, provider_name="Other")
    prov = models.Provider(id=1, provider_name="Clinic")
    s.add_all([prov, other])
    s.add_all(
        [
            models.Charge(id=10, provider_id=1, service_date=date(2026, 1, 5), owed_amount=40),
            models.Charge(id=11, provider_id=1, service_date=date(2026, 1, 5), owed_amount=25),
            models.Charge(id=12, provider_id=1, service_date=date(2026, 3, 1), owed_amount=65),
            models.Charge(id=20, provider_id=2, service_date=date(2026, 1, 5), owed_amount=10),
            models.Payment(id=100, provider_id=1, payment_date=date(2026, 2, 1), payment_amount=65),
            models.Payment(id=101, provider_id=1, payment_date=date(2026, 3, 20), payment_amount=65),
        ]
    )
    # Payment 101 is wrongly linked to the January charges
    s.add_all(
        [
            models.ChargePaymentXref(payment_id=101, charge_id=10, allocated_amount=40),
            models.ChargePaymentXref(payment_id=101, charge_id=11, allocated_amount=25),
        ]
    )
    s.commit()
    yield s
    s.close()


def test_snapshot_and_candidates(db):
    snap = load_provider_snapshot(db, 1)
    assert {c["id"] for c in snap["charges"]} == {10, 11, 12}
    assert {p["id"] for p in snap["payments"]} == {100, 101}

    cands = find_amount_candidates(snap)
    # Payment 100 (Feb) best matches the two Jan line items summing to 65
    assert cands[100][0]["charge_ids"] == [10, 11]
    # Payment 101 (late Mar) best matches the single Mar charge
    assert cands[101][0]["charge_ids"] == [12]


def test_build_recommendations_filters_and_rebalances(db):
    snap = load_provider_snapshot(db, 1)
    ai = {
        "summary": "s",
        "proposals": [
            {"payment_id": 100, "allocations": [{"charge_id": 10, "amount": 40}, {"charge_id": 11, "amount": 25}], "confidence": "high"},
            {"payment_id": 101, "allocations": [{"charge_id": 12, "amount": 60}], "confidence": "medium"},
            # unchanged mapping is dropped
            {"payment_id": 101, "allocations": [{"charge_id": 10, "amount": 40}]},
            # unknown payment / charge ids are ignored
            {"payment_id": 999, "allocations": []},
        ],
        "issues": [{"description": "dup", "charge_ids": [10, 20], "payment_ids": [999]}],
    }
    out = build_recommendations(snap, ai)
    recs = {r["payment"]["id"]: r for r in out["recommendations"]}
    assert set(recs) == {100, 101}
    assert recs[101]["proposed"][0]["amount"] == 65.0
    assert recs[101]["warnings"]
    assert out["issues"][0]["charge_ids"] == [10]
    assert out["issues"][0]["payment_ids"] == []


def test_apply_link_changes(db):
    counts = apply_link_changes(
        db,
        1,
        [
            {"payment_id": 100, "allocations": [{"charge_id": 10, "amount": 40}, {"charge_id": 11, "amount": 25}]},
            {"payment_id": 101, "allocations": [{"charge_id": 12, "amount": 65}]},
        ],
    )
    assert counts == {"payments_updated": 2, "links_added": 3, "links_updated": 0, "links_removed": 2}
    links = {(x.payment_id, x.charge_id) for x in db.query(models.ChargePaymentXref).all()}
    assert links == {(100, 10), (100, 11), (101, 12)}


@pytest.mark.parametrize(
    "change",
    [
        {"payment_id": 100, "allocations": [{"charge_id": 20, "amount": 65}]},  # other provider's charge
        {"payment_id": 100, "allocations": [{"charge_id": 12, "amount": 50}]},  # wrong total
        {"payment_id": 555, "allocations": []},  # unknown payment
    ],
)
def test_apply_link_changes_rejects_invalid(db, change):
    with pytest.raises(ValueError):
        apply_link_changes(db, 1, [change])
