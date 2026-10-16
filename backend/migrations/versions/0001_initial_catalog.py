"""Create bibliographic records and individual inventory copies."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bibliographic_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("section", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=500), nullable=False),
        sa.Column("imprint", sa.Text(), nullable=False),
        sa.Column("binding", sa.String(length=300), nullable=False),
        sa.Column("inventory_source", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bibliographic_records_section",
        "bibliographic_records",
        ["section"],
    )
    op.create_index(
        "ix_bibliographic_records_title",
        "bibliographic_records",
        ["title"],
    )
    op.create_table(
        "inventory_copies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inventory_number", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["bibliographic_records.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inventory_number", name="uq_inventory_number"),
    )
    op.create_index(
        "ix_inventory_copies_record_id",
        "inventory_copies",
        ["record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_copies_record_id", table_name="inventory_copies")
    op.drop_table("inventory_copies")
    op.drop_index("ix_bibliographic_records_title", table_name="bibliographic_records")
    op.drop_index("ix_bibliographic_records_section", table_name="bibliographic_records")
    op.drop_table("bibliographic_records")
