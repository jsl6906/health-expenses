"""Allocation/aggregation helper functions for charges & payments.

Centralizes logic that was previously duplicated across page routes for:
 - Even allocation of payment amounts across linked charges
 - Computing total paid per charge via even-split logic
 - Identifying open charges (remaining > 0)

All functions are read-only helpers working off the SQLAlchemy session and
existing ORM models (Charge, Payment, ChargePaymentXref).
"""
from __future__ import annotations
from typing import Iterable, Dict, List, Any

from sqlalchemy.orm import Session

# We import inside functions to avoid circular imports when used in app startup.


def even_allocation_map(db: Session, charge_ids: Iterable[int]) -> Dict[int, float]:
    """Return mapping charge_id -> total paid (even allocation across its linked payments).

    Each payment's amount is divided equally among all charges it links to via the
    ChargePaymentXref association table. The returned total for a charge is the sum
    of its share from each linked payment.
    """
    charge_ids = list({int(cid) for cid in charge_ids if cid is not None})
    if not charge_ids:
        return {}
    try:
        from models.database import ChargePaymentXref as XrefModel, Payment as PaymentModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import ChargePaymentXref as XrefModel, Payment as PaymentModel  # type: ignore

    # Fetch all xrefs for provided charge ids.
    xrefs = db.query(XrefModel.payment_id, XrefModel.charge_id).filter(XrefModel.charge_id.in_(charge_ids)).all()
    if not xrefs:
        return {cid: 0.0 for cid in charge_ids}

    payment_ids = {pid for pid, _ in xrefs}
    # Load payment amounts once.
    payments_map: Dict[int, float] = {}
    if payment_ids:
        for p in db.query(PaymentModel).filter(PaymentModel.id.in_(payment_ids)).all():
            raw_amt = getattr(p, 'payment_amount', None)
            try:
                payments_map[int(getattr(p, 'id'))] = float(raw_amt) if raw_amt is not None else 0.0
            except Exception:
                payments_map[int(getattr(p, 'id'))] = 0.0

    # Build mapping payment -> list of charge ids.
    payment_to_charges: Dict[int, List[int]] = {}
    for pid, cid in xrefs:
        payment_to_charges.setdefault(pid, []).append(cid)

    allocation: Dict[int, float] = {cid: 0.0 for cid in charge_ids}
    for pid, cids in payment_to_charges.items():
        amount = payments_map.get(pid, 0.0)
        if not cids:
            continue
        share = amount / len(cids) if len(cids) else 0.0
        for cid in cids:
            allocation[cid] = allocation.get(cid, 0.0) + share
    return allocation


def annotate_charges_with_paid(db: Session, charges: List[Any]) -> None:
    """Attach paid_amount_agg attribute to each charge object in-place.

    Uses even_allocation_map for the set of charges.
    """
    ids = [getattr(c, 'id', None) for c in charges]
    allocation = even_allocation_map(db, [cid for cid in ids if cid is not None])
    for ch in charges:
        cid = getattr(ch, 'id', None)
        paid = allocation.get(cid, 0.0) if cid is not None else 0.0
        setattr(ch, 'paid_amount_agg', paid)


def get_open_charges_for_provider(db: Session, provider_id: int) -> List[dict]:
    """Return list of open charges (remaining > 0) for a provider.

    Each dict contains: id, service_date (ISO str or None), description, remaining,
    owed, paid_allocated.
    """
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Charge as ChargeModel  # type: ignore

    charges = db.query(ChargeModel).filter(ChargeModel.provider_id == provider_id).all()
    if not charges:
        return []
    annotate_charges_with_paid(db, charges)

    open_result: List[dict] = []
    for c in charges:
        owed_raw = getattr(c, 'owed_amount', None)
        try:
            owed = float(owed_raw) if owed_raw is not None else 0.0
        except Exception:
            owed = 0.0
        paid = getattr(c, 'paid_amount_agg', 0.0) or 0.0
        remaining = max(owed - paid, 0.0)
        if remaining > 0.0001:  # treat tiny fractional remainder as zero
            open_result.append({
                'id': getattr(c, 'id', None),
                'service_date': c.service_date.isoformat() if getattr(c, 'service_date', None) else None,
                'description': getattr(c, 'charge_description', None),
                'remaining': remaining,
                'owed': owed,
                'paid_allocated': paid,
            })
    # Sort most recent service_date then remaining desc
    open_result.sort(key=lambda r: (r['service_date'] or '', -r['remaining']), reverse=True)
    return open_result
