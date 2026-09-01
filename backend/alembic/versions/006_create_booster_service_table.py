"""create booster services table

Revision ID: 006_create_booster_service_table
Revises: 005_add_order_new_fields
Create Date: 2026-03-31_02_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "006_create_booster_service_table"
down_revision: Union[str, None] = "005_add_order_new_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booster_services",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("booster_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("service_type", sa.String(length=100), nullable=False),
        sa.Column("price_per_hour", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["booster_id"],
            ["users.id"],
            name=op.f("fk_booster_services_booster_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name=op.f("fk_booster_services_game_id_games"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booster_services")),
    )
    op.create_index(op.f("ix_booster_services_id"), "booster_services", ["id"], unique=False)
    op.create_index(op.f("ix_booster_services_booster_id"), "booster_services", ["booster_id"], unique=False)
    op.create_index(op.f("ix_booster_services_game_id"), "booster_services", ["game_id"], unique=False)
    op.create_index(op.f("ix_booster_services_title"), "booster_services", ["title"], unique=False)
    op.create_index(op.f("ix_booster_services_service_type"), "booster_services", ["service_type"], unique=False)
    op.create_index(op.f("ix_booster_services_is_available"), "booster_services", ["is_available"], unique=False)

    op.create_foreign_key(
        op.f("fk_orders_service_id_booster_services"),
        "orders",
        "booster_services",
        ["service_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_orders_service_id_booster_services"), "orders", type_="foreignkey")
    op.drop_index(op.f("ix_booster_services_is_available"), table_name="booster_services")
    op.drop_index(op.f("ix_booster_services_service_type"), table_name="booster_services")
    op.drop_index(op.f("ix_booster_services_title"), table_name="booster_services")
    op.drop_index(op.f("ix_booster_services_game_id"), table_name="booster_services")
    op.drop_index(op.f("ix_booster_services_booster_id"), table_name="booster_services")
    op.drop_index(op.f("ix_booster_services_id"), table_name="booster_services")
    op.drop_table("booster_services")
