"""create users and orders tables

Revision ID: 001_initial
Revises: 
Create Date: 2024-01-01_00_00_00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create users and orders tables."""
    
    # Create user_role_enum type
    user_role_enum = sa.Enum("USER", "BOOSTER", "ADMIN", name="user_role_enum")
    user_role_enum.create(op.get_bind(), checkfirst=True)
    
    # Create order_status_enum type
    order_status_enum = sa.Enum(
        "PENDING", "LOCKED", "COMPLETED", "DISPUTED", "CANCELLED",
        name="order_status_enum"
    )
    order_status_enum.create(op.get_bind(), checkfirst=True)
    
    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column(
            "role",
            sa.Enum("USER", "BOOSTER", "ADMIN", name="user_role_enum"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False, default=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    
    # Create orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("booster_id", sa.Integer(), nullable=True),
        sa.Column("game_name", sa.String(length=100), nullable=False),
        sa.Column("current_rank", sa.String(length=50), nullable=False),
        sa.Column("target_rank", sa.String(length=50), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "LOCKED", "COMPLETED", "DISPUTED", "CANCELLED",
                name="order_status_enum"
            ),
            nullable=False,
        ),
        sa.Column("description_raw", sa.Text(), nullable=True),
        sa.Column("description_ai", sa.Text(), nullable=True),
        sa.Column("game_account", sa.String(length=255), nullable=True),
        sa.Column("game_password", sa.String(length=255), nullable=True),
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
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, default=0),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_orders_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booster_id"],
            ["users.id"],
            name=op.f("fk_orders_booster_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(op.f("ix_orders_id"), "orders", ["id"], unique=False)
    op.create_index(op.f("ix_orders_user_id"), "orders", ["user_id"], unique=False)
    op.create_index(op.f("ix_orders_booster_id"), "orders", ["booster_id"], unique=False)
    op.create_index(op.f("ix_orders_game_name"), "orders", ["game_name"], unique=False)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)


def downgrade() -> None:
    """Drop users and orders tables."""
    
    # Drop orders table
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_game_name"), table_name="orders")
    op.drop_index(op.f("ix_orders_booster_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_id"), table_name="orders")
    op.drop_table("orders")
    
    # Drop users table
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    
    # Drop enum types
    sa.Enum(name="order_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role_enum").drop(op.get_bind(), checkfirst=True)
