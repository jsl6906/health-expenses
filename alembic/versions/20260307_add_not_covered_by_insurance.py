"""
Alembic migration: add not_covered_by_insurance column to charges table
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260307_not_covered"
down_revision = "49f58cc884d9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "charges",
        sa.Column(
            "not_covered_by_insurance",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
            comment="Self-pay: charge is not subject to insurance",
        ),
    )


def downgrade():
    op.drop_column("charges", "not_covered_by_insurance")
