"""Add soft delete (papelera) and allow reusing inventory numbers.

Los registros eliminados pasan a la papelera (`deleted_at` con fecha) en lugar de
borrarse. Para poder reutilizar esos números de inventario en un registro nuevo,
se quita la restricción única global de `inventory_copies`; la unicidad se valida
en la aplicación entre los registros activos.
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_trash"
down_revision = "0001_initial_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bibliographic_records",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_bibliographic_records_deleted_at",
        "bibliographic_records",
        ["deleted_at"],
    )
    with op.batch_alter_table("inventory_copies") as batch_op:
        batch_op.drop_constraint("uq_inventory_number", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("inventory_copies") as batch_op:
        batch_op.create_unique_constraint(
            "uq_inventory_number", ["inventory_number"]
        )
    op.drop_index(
        "ix_bibliographic_records_deleted_at",
        table_name="bibliographic_records",
    )
    op.drop_column("bibliographic_records", "deleted_at")
