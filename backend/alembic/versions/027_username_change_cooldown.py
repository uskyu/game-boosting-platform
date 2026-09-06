"""Username change cooldown: users.username_changed_at.

普通用户自助改名每 90 天仅可修改一次；管理员在后台改名不受限制。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "027_username_change_cooldown"
down_revision: Union[str, None] = "026_push_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username_changed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "username_changed_at")
