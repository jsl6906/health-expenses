"""add allocated_amount to charge_payment_xref

Revision ID: da3610b04855
Revises: 20251018_add_insurance
Create Date: 2025-10-19 16:25:14.536928

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "da3610b04855"
down_revision: Union[str, Sequence[str], None] = "20251018_add_insurance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "charge_payment_xref",
        sa.Column("allocated_amount", sa.Numeric(10, 2), nullable=True, default=0.0),
    )
    # Update existing rows to 0.0
    op.execute(
        "UPDATE charge_payment_xref SET allocated_amount = 0.0 WHERE allocated_amount IS NULL"
    )
    # Make it not null
    op.alter_column("charge_payment_xref", "allocated_amount", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("charge_payment_xref", "allocated_amount")
