"""Allocation/aggregation helper functions for charges & payments.

Centralizes logic that was previously duplicated across page routes for:
 - Even allocation of payment amounts across linked charges
 - Computing total paid per charge via even-split logic
 - Identifying open charges (remaining > 0)

This module now treats negative payment amounts (refunds) symmetrically with
positive payments: explicit negative allocated_amounts are respected, and
`allocate_payment_to_charges` will distribute negative payment amounts across
linked charges (reducing previously applied paid amounts) using mirrored
priority logic.

All functions are read-only helpers working off the SQLAlchemy session and
existing ORM models (Charge, Payment, ChargePaymentXref).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from sqlalchemy.orm import Session

# We import inside functions to avoid circular imports when used in app startup.


def even_allocation_map(db: Session, charge_ids: Iterable[int]) -> Dict[int, float]:
    """Return mapping charge_id -> total paid (sum of allocated amounts or even allocation).

    If allocated_amounts are set (sum > 0 for payments), use them; otherwise, fall back to even allocation.
    """
    charge_ids = list({int(cid) for cid in charge_ids if cid is not None})
    if not charge_ids:
        return {}
    try:
        # Prefer absolute package import when running as installed/packaged
        from src.models.database import ChargePaymentXref as XrefModel  # type: ignore
        from src.models.database import Payment as PaymentModel
    except Exception:  # pragma: no cover
        # Fallback to relative import when running tests or alternative module paths
        from ..models.database import ChargePaymentXref as XrefModel  # type: ignore
        from ..models.database import Payment as PaymentModel

    # Fetch all xrefs for provided charge ids.
    xrefs = (
        db.query(XrefModel.payment_id, XrefModel.charge_id, XrefModel.allocated_amount)
        .filter(XrefModel.charge_id.in_(charge_ids))
        .all()
    )
    if not xrefs:
        return {cid: 0.0 for cid in charge_ids}

    # Check if allocated_amounts are set (non-zero sum for any payment).
    # Previously we only checked for sums > 0 which ignored negative
    # allocated amounts (refunds). Treat any non-zero sum as a sign that
    # explicit allocations exist and should be used.
    payment_allocated_sum = {}
    for pid, cid, amt in xrefs:
        payment_allocated_sum[pid] = payment_allocated_sum.get(pid, 0.0) + float(
            amt or 0.0
        )

    use_allocated = any(abs(s) > 1e-9 for s in payment_allocated_sum.values())

    if use_allocated:
        # Use allocated amounts
        allocation: Dict[int, float] = {cid: 0.0 for cid in charge_ids}
        for pid, cid, amt in xrefs:
            allocation[cid] = allocation.get(cid, 0.0) + float(amt or 0.0)
        return allocation
    else:
        # Fall back to even allocation
        payment_ids = {pid for pid, _, _ in xrefs}
        # Load payment amounts once.
        payments_map: Dict[int, float] = {}
        if payment_ids:
            for p in (
                db.query(PaymentModel).filter(PaymentModel.id.in_(payment_ids)).all()
            ):
                raw_amt = getattr(p, "payment_amount", None)
                try:
                    payments_map[int(getattr(p, "id"))] = (
                        float(raw_amt) if raw_amt is not None else 0.0
                    )
                except Exception:
                    payments_map[int(getattr(p, "id"))] = 0.0

        # Build mapping payment -> list of charge ids.
        payment_to_charges: Dict[int, List[int]] = {}
        for pid, cid, _ in xrefs:
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


def allocate_payment_to_charges(
    db: Session, payment_amount: float, charge_ids: List[int]
) -> Dict[int, float]:
    """Allocate payment amount to charges using priority logic.

    Sorts charges by current owed amount descending, then allocates payment sequentially:
    - For all but the last charge, apply as much as possible to cover owed.
    - For the last charge, apply the remaining payment amount.

    Returns dict of charge_id -> allocated_amount.
    """
    # Allow negative payment_amounts (refunds). Only a zero amount is a no-op.
    if not charge_ids or payment_amount == 0:
        return {cid: 0.0 for cid in charge_ids}

    try:
        from src.models.database import Charge as ChargeModel  # type: ignore
    except Exception:  # pragma: no cover
        from ..models.database import Charge as ChargeModel  # type: ignore

    # Get current paid for each charge
    current_paid = even_allocation_map(db, charge_ids)

    # Get charges and compute the per-charge "remaining" capacity based on
    # whether we're applying a positive payment (increase paid toward owed)
    # or a negative payment (refund, decrease previously paid amount).
    charges = db.query(ChargeModel).filter(ChargeModel.id.in_(charge_ids)).all()
    charge_owed = {}
    for c in charges:
        cid = c.id
        owed = float(getattr(c, "owed_amount", 0.0) or 0.0)
        paid = current_paid.get(cid, 0.0)
        if payment_amount >= 0:
            # How much more this charge can be paid
            remaining = max(owed - paid, 0.0)
        else:
            # For refunds, how much can we reduce (can't reduce below zero paid)
            remaining = max(paid, 0.0)
        charge_owed[cid] = remaining

    # Sort charges by remaining capacity descending
    sorted_charges = sorted(charge_owed.items(), key=lambda x: x[1], reverse=True)

    allocation = {cid: 0.0 for cid in charge_ids}
    remaining_payment = abs(payment_amount)
    sign = 1.0 if payment_amount > 0 else -1.0

    for i, (cid, rem) in enumerate(sorted_charges):
        if remaining_payment <= 0:
            break
        if i == len(sorted_charges) - 1:
            # Last charge gets all remaining (preserve sign)
            allocation[cid] = sign * remaining_payment
        else:
            # Apply as much as possible to this charge (cap to its remaining capacity)
            alloc = min(remaining_payment, rem)
            allocation[cid] = sign * alloc
            remaining_payment -= alloc

    return allocation


def annotate_charges_with_paid(db: Session, charges: List[Any]) -> None:
    """Attach paid_amount_agg and payment_status to each charge object in-place.

    Uses even_allocation_map for the set of charges.
    """
    ids = [getattr(c, "id", None) for c in charges]
    allocation = even_allocation_map(db, [cid for cid in ids if cid is not None])
    for ch in charges:
        cid = getattr(ch, "id", None)
        paid = allocation.get(cid, 0.0) if cid is not None else 0.0
        setattr(ch, "paid_amount_agg", paid)
        owed_cents = round(float(getattr(ch, "owed_amount", 0) or 0) * 100)
        paid_cents = round(paid * 100)
        status = (
            "na"
            if owed_cents <= 0
            else "paid"
            if paid_cents >= owed_cents
            else "partial"
            if paid_cents > 0
            else "outstanding"
        )
        setattr(ch, "payment_status", status)


def get_open_charges_for_provider(db: Session, provider_id: int) -> List[dict]:
    """Return list of open charges (remaining > 0) for a provider.

    Each dict contains: id, service_date (ISO str or None), description, remaining,
    owed, paid_allocated.
    """
    try:
        from src.models.database import Charge as ChargeModel  # type: ignore
    except Exception:  # pragma: no cover
        from ..models.database import Charge as ChargeModel  # type: ignore

    charges = db.query(ChargeModel).filter(ChargeModel.provider_id == provider_id).all()
    if not charges:
        return []
    annotate_charges_with_paid(db, charges)

    open_result: List[dict] = []
    for c in charges:
        owed_raw = getattr(c, "owed_amount", None)
        try:
            owed = float(owed_raw) if owed_raw is not None else 0.0
        except Exception:
            owed = 0.0
        paid = getattr(c, "paid_amount_agg", 0.0) or 0.0
        remaining = max(owed - paid, 0.0)
        if getattr(c, "payment_status") != "paid" and round(remaining * 100) > 0:
            open_result.append(
                {
                    "id": getattr(c, "id", None),
                    "service_date": c.service_date.isoformat()
                    if getattr(c, "service_date", None)
                    else None,
                    "description": getattr(c, "charge_description", None),
                    "remaining": remaining,
                    "owed": owed,
                    "paid_allocated": paid,
                }
            )
    # Sort most recent service_date then remaining desc
    open_result.sort(
        key=lambda r: (r["service_date"] or "", -r["remaining"]), reverse=True
    )
    return open_result
