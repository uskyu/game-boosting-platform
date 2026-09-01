"""add new order fields for game and service integration

Revision ID: 005_add_order_new_fields
Revises: 004_create_game_table
Create Date: 2026-03-31_01_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005_add_order_new_fields"
down_revision: Union[str, None] = "004_create_game_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("game_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("ai_tags", sa.JSON(), nullable=True))
    op.add_column("orders", sa.Column("service_type", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("server", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("service_id", sa.Integer(), nullable=True))

    op.create_index(op.f("ix_orders_game_id"), "orders", ["game_id"], unique=False)
    op.create_index(op.f("ix_orders_service_type"), "orders", ["service_type"], unique=False)
    op.create_index(op.f("ix_orders_server"), "orders", ["server"], unique=False)
    op.create_index(op.f("ix_orders_service_id"), "orders", ["service_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_orders_game_id_games"),
        "orders",
        "games",
        ["game_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        sa.text(
            """
            UPDATE orders AS o
            INNER JOIN games AS g
                ON o.game_name = g.name
            SET o.game_id = g.id
            WHERE o.game_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE orders AS o
            INNER JOIN games AS g
                ON o.game_name LIKE CONCAT('%', g.name, '%')
            SET o.game_id = g.id
            WHERE o.game_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE orders AS o
            INNER JOIN games AS g
                ON g.name LIKE CONCAT('%', o.game_name, '%')
            SET o.game_id = g.id
            WHERE o.game_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_orders_game_id_games"), "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_service_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_server"), table_name="orders")
    op.drop_index(op.f("ix_orders_service_type"), table_name="orders")
    op.drop_index(op.f("ix_orders_game_id"), table_name="orders")

    op.drop_column("orders", "service_id")
    op.drop_column("orders", "server")
    op.drop_column("orders", "service_type")
    op.drop_column("orders", "ai_tags")
    op.drop_column("orders", "game_id")
