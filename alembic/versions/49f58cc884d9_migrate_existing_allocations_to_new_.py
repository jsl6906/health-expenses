"""migrate_existing_allocations_to_new_logic

Revision ID: 49f58cc884d9
Revises: da3610b04855
Create Date: 2025-10-19 19:15:14.885339

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.orm import Session

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49f58cc884d9"
down_revision: Union[str, Sequence[str], None] = "da3610b04855"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: migrate existing allocations to new priority-based logic."""
    # Get a connection
    bind = op.get_bind()
    session = Session(bind=bind)

    try:
        # Import models - adjust paths as needed for your project structure
        from src.models.database import ChargePaymentXref, Payment

        # Get all payments that have linked charges
        payments_with_charges = (
            session.query(Payment.id, Payment.payment_amount)
            .join(ChargePaymentXref, ChargePaymentXref.payment_id == Payment.id)
            .distinct()
            .all()
        )

        for payment_id, payment_amount in payments_with_charges:
            # Get all charge IDs linked to this payment
            charge_ids = [
                xref.charge_id
                for xref in session.query(ChargePaymentXref.charge_id)
                .filter(ChargePaymentXref.payment_id == payment_id)
                .all()
            ]

            if not charge_ids:
                continue

            # Compute new allocations using the logic
            allocations = compute_allocations(
                session, float(payment_amount or 0.0), charge_ids
            )

            # Update the allocated_amount for each xref
            for charge_id in charge_ids:
                alloc_amount = allocations.get(charge_id, 0.0)
                session.query(ChargePaymentXref).filter(
                    ChargePaymentXref.payment_id == payment_id,
                    ChargePaymentXref.charge_id == charge_id,
                ).update({"allocated_amount": alloc_amount})

        session.commit()

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def compute_allocations(session, payment_amount: float, charge_ids: list):
    """Compute allocations using the new priority logic."""
    if not charge_ids or payment_amount <= 0:
        return {cid: 0.0 for cid in charge_ids}

    from src.models.database import Charge, ChargePaymentXref

    # Get current paid for each charge (excluding this payment's allocations)
    # For migration, we need to compute based on other payments
    current_paid = {}
    for cid in charge_ids:
        # Sum allocated_amount from all payments (including this one, but we'll adjust)
        total_paid = (
            session.query(sa.func.sum(ChargePaymentXref.allocated_amount))
            .filter(ChargePaymentXref.charge_id == cid)
            .scalar()
            or 0.0
        )
        current_paid[cid] = float(total_paid)

    # Get charges with owed amounts
    charges = session.query(Charge).filter(Charge.id.in_(charge_ids)).all()
    charge_owed = {}
    for c in charges:
        cid = c.id
        owed = float(getattr(c, "owed_amount", 0.0) or 0.0)
        paid = current_paid.get(cid, 0.0)
        remaining = max(owed - paid, 0.0)
        charge_owed[cid] = remaining

    # Sort charges by owed descending
    sorted_charges = sorted(charge_owed.items(), key=lambda x: x[1], reverse=True)

    allocation = {cid: 0.0 for cid in charge_ids}
    remaining_payment = payment_amount

    for i, (cid, owed) in enumerate(sorted_charges):
        if remaining_payment <= 0:
            break
        if i == len(sorted_charges) - 1:
            # Last charge gets all remaining
            allocation[cid] = remaining_payment
        else:
            # Apply as much as possible to this charge
            alloc = min(remaining_payment, owed)
            allocation[cid] = alloc
            remaining_payment -= alloc

    return allocation


def downgrade() -> None:
    """Downgrade schema: reset allocated_amount to 0.0 for all existing records."""
    # Reset all allocated_amount to 0.0
    op.execute("UPDATE charge_payment_xref SET allocated_amount = 0.0")
