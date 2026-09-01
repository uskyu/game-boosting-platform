"""deactivate seed games and change games.is_active default to 0

业务背景：本平台为"老板个人使用的三角洲派单系统"。
- 004 迁移 seed 的 59 个游戏全部默认上架，但实际业务中老板只需要
  自己添加并上架要用的游戏（如三角洲行动），其余游戏不应出现在
  对外列表中。
- 本次迁移做两件事：
  1. UPDATE games SET is_active=0 —— 全部下架（老板可在后台自行上架）；
  2. 将 games.is_active 的列默认值改为 0（server_default='0'），
     让后台新建的游戏默认下架，与模型层 default=False 保持一致。

Revision ID: 013_deactivate_seed_games
Revises: 012_wallet_and_withdrawals
Create Date: 2026-09-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "013_deactivate_seed_games"
down_revision: Union[str, None] = "012_wallet_and_withdrawals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """全部游戏下架，并把 is_active 列默认值改为 0。"""
    op.execute("UPDATE games SET is_active = 0")
    # MySQL: ALTER TABLE ... ALTER COLUMN ... SET DEFAULT
    op.alter_column(
        "games",
        "is_active",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    """恢复：默认值改回 1，全部游戏重新上架。"""
    op.alter_column(
        "games",
        "is_active",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("1"),
    )
    op.execute("UPDATE games SET is_active = 1")
