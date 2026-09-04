"""User restrictions: users.can_publish / users.can_accept.

管理员可单独禁止某用户发单（can_publish）或接单（can_accept），
与全局封号 is_active 解耦。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024_user_restrictions"
down_revision: Union[str, None] = "023_payout_delay_hours"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("can_publish", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column("can_accept", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "can_accept")
    op.drop_column("users", "can_publish")
