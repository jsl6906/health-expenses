"""
Alembic migration: add insurance claim-form defaults and family member signature image
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20261001_claim_form"
down_revision = "20261001_provider_aliases"
branch_labels = None
depends_on = None

INSURANCE_COLUMNS = [
    sa.Column("claim_email", sa.String, nullable=True),
    sa.Column("claim_mailing_address", sa.String, nullable=True),
    sa.Column("no_other_coverage", sa.Boolean, nullable=True, server_default=sa.true()),
    sa.Column("other_policyholder_name", sa.String, nullable=True),
    sa.Column("other_policy_number", sa.String, nullable=True),
    sa.Column("other_insurance_company", sa.String, nullable=True),
    sa.Column("other_insurance_phone", sa.String, nullable=True),
    sa.Column("other_insurance_address", sa.String, nullable=True),
    sa.Column("other_insurance_city", sa.String, nullable=True),
    sa.Column("other_insurance_state", sa.String, nullable=True),
    sa.Column("other_insurance_zip", sa.String, nullable=True),
    sa.Column("medicare_number", sa.String, nullable=True),
    sa.Column("medicare_part_a_date", sa.Date, nullable=True),
    sa.Column("medicare_part_b_date", sa.Date, nullable=True),
]


def upgrade():
    for col in INSURANCE_COLUMNS:
        op.add_column("insurance", col)
    op.add_column(
        "family_members", sa.Column("signature_image", sa.LargeBinary, nullable=True)
    )
    op.add_column(
        "family_members",
        sa.Column("signature_content_type", sa.String, nullable=True),
    )


def downgrade():
    op.drop_column("family_members", "signature_content_type")
    op.drop_column("family_members", "signature_image")
    for col in reversed(INSURANCE_COLUMNS):
        op.drop_column("insurance", col.name)
