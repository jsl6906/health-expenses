"""
Alembic migration: add insurance table
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20251018_add_insurance"
down_revision = "20251018_family_members"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "insurance",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("medical_type", sa.String),
        sa.Column("plan_name", sa.String),
        sa.Column("policy_number", sa.String),
        sa.Column("group_number", sa.String),
        sa.Column("insured_member_id", sa.Integer, sa.ForeignKey("family_members.id")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("created_by", sa.String),
        sa.Column("updated_by", sa.String),
    )


def downgrade():
    op.drop_table("insurance")
