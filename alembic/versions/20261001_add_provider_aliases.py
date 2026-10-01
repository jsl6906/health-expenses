"""
Alembic migration: add provider_aliases table used to resolve alternate provider names on import
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20261001_provider_aliases"
down_revision = "20260307_not_covered"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_aliases",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "provider_id",
            sa.Integer,
            sa.ForeignKey("providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias_name", sa.String, nullable=False),
        sa.Column("alias_address", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("created_by", sa.String, nullable=True),
    )
    op.create_index(
        "ix_provider_aliases_provider_id", "provider_aliases", ["provider_id"]
    )
    op.create_index(
        "uq_provider_aliases_name_address",
        "provider_aliases",
        [sa.text("lower(trim(alias_name))"), sa.text("lower(trim(coalesce(alias_address, '')))")],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_provider_aliases_name_address", table_name="provider_aliases")
    op.drop_index("ix_provider_aliases_provider_id", table_name="provider_aliases")
    op.drop_table("provider_aliases")
