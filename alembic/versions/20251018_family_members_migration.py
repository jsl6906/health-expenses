"""
Alembic migration: Add family_members table, migrate charges.family_member to charges.family_member_id
"""
import sqlalchemy as sa
from sqlalchemy import Integer, String
from sqlalchemy.sql import column, table

from alembic import op

# revision identifiers, used by Alembic.
# Revision id shortened to fit typical alembic_version.version_num length
revision = "20251018_family_members"
down_revision = "a640ae50d275"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create family_members table
    op.create_table(
        "family_members",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("first_name", sa.String, nullable=False),
        sa.Column("last_name", sa.String),
        sa.Column("middle_initial", sa.String),
        sa.Column("address", sa.String),
        sa.Column("dob", sa.Date),
        sa.Column("ssn", sa.String),
        sa.Column("gender", sa.String),
        sa.Column("relationship_to_primary", sa.String),
        sa.Column("phone_number", sa.String),
        sa.Column("emoji", sa.String(length=4), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("created_by", sa.String),
        sa.Column("updated_by", sa.String),
    )

    # 2. Insert initial family members from the previous enum values in use
    connection = op.get_bind()
    names = sorted(
        str(n)
        for n in connection.execute(
            sa.text(
                "SELECT DISTINCT family_member::text FROM charges WHERE family_member IS NOT NULL"
            )
        ).scalars()
    )
    family_members_table = table(
        "family_members", column("id", Integer), column("first_name", String)
    )
    if names:
        op.bulk_insert(family_members_table, [{"first_name": n} for n in names])

    # 3. Add family_member_id column to charges
    op.add_column(
        "charges",
        sa.Column("family_member_id", sa.Integer, sa.ForeignKey("family_members.id")),
    )

    # 4. Migrate data from charges.family_member to charges.family_member_id
    for name in names:
        result = connection.execute(
            sa.text("SELECT id FROM family_members WHERE first_name = :name"),
            {"name": name},
        )
        member_id = result.scalar()
        connection.execute(
            sa.text(
                "UPDATE charges SET family_member_id = :member_id WHERE family_member = :name"
            ),
            {"member_id": member_id, "name": name},
        )

    # 5. Drop old family_member column
    op.drop_column("charges", "family_member")

    # 6. (Optional) Remove enum type if present
    op.execute("DROP TYPE IF EXISTS family_member")


def downgrade():
    # Downgrade logic (not implemented)
    pass
