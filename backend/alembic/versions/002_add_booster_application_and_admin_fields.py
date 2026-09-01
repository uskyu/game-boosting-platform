"""add booster application workflow fields

Revision ID: 002_booster_application
Revises: 001_initial
Create Date: 2026-03-05_00_00_00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_booster_application"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    booster_application_status_enum = sa.Enum(
        "NONE",
        "PENDING",
        "APPROVED",
        "REJECTED",
        name="booster_application_status_enum",
    )
    booster_application_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "booster_application_status",
            sa.Enum(
                "NONE",
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="booster_application_status_enum",
            ),
            nullable=False,
            server_default="NONE",
        ),
    )
    op.add_column("users", sa.Column("booster_application_game", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("booster_application_current_rank", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("booster_application_target_rank", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("booster_application_proof_url", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("booster_application_note", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("booster_quota", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("reviewed_by_admin_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("review_note", sa.Text(), nullable=True))

    op.create_index(op.f("ix_users_booster_application_status"), "users", ["booster_application_status"], unique=False)
    op.create_foreign_key(
        op.f("fk_users_reviewed_by_admin_id_users"),
        "users",
        "users",
        ["reviewed_by_admin_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column("users", "booster_application_status", server_default=None)
    op.alter_column("users", "booster_quota", server_default=None)


def downgrade() -> None:
    op.drop_constraint(op.f("fk_users_reviewed_by_admin_id_users"), "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_booster_application_status"), table_name="users")

    op.drop_column("users", "review_note")
    op.drop_column("users", "reviewed_at")
    op.drop_column("users", "reviewed_by_admin_id")
    op.drop_column("users", "booster_quota")
    op.drop_column("users", "booster_application_note")
    op.drop_column("users", "booster_application_proof_url")
    op.drop_column("users", "booster_application_target_rank")
    op.drop_column("users", "booster_application_current_rank")
    op.drop_column("users", "booster_application_game")
    op.drop_column("users", "booster_application_status")

    sa.Enum(name="booster_application_status_enum").drop(op.get_bind(), checkfirst=True)
