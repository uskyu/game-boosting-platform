"""保证金档位结账时效改为可留空（NULL = 按订单到账时效）。

老板 2026-10-18 拍板 R3：**订单到账时效为准，档位结账时效只是可选加速器**。

- ``deposit_tiers.settle_hours`` 由 ``NOT NULL DEFAULT 72`` 改为 ``NULL``；
- 存量行**保留原值不动**（当前 5 行仍是 72/72/72/48/1），因此上线本身
  不改变任何既有档位的承诺时长，只新增「可以留空」的能力；
- 留空语义由应用层解释（``deposit_service`` / ``order_service``）：
  该档不承诺加速 → 按订单自身 ``payout_delay_days`` / ``payout_delay_hours``；
  且 0 元档（``threshold = 0``，即无保证金）留空即「按订单时效」。

降级：先把 NULL 回填为 72（与 032 建表时的默认值一致，语义退化为
「所有档位统一 72 小时」），再收紧为 NOT NULL —— 必须先回填再收紧，
否则 ALTER 会因现存 NULL 行失败。

Revision ID: 057_deposit_tier_settle_nullable
Revises: 056_aftersale_deposit_tx_type
Create Date: 2026-10-18 11:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "057_deposit_tier_settle_nullable"
down_revision: Union[str, None] = "056_aftersale_deposit_tx_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 032 建表时的默认值；降级回填沿用同一数值
_LEGACY_DEFAULT_SETTLE_HOURS = 72


def upgrade() -> None:
    # 仅改可空性，不动存量数据（现有行的 72 等原值一律保留）
    op.alter_column(
        "deposit_tiers",
        "settle_hours",
        existing_type=sa.Integer(),
        nullable=True,
        existing_server_default="72",
        server_default=None,
    )


def downgrade() -> None:
    # 必须先回填：存在 NULL 时 NOT NULL 收紧会失败
    op.execute(
        f"UPDATE deposit_tiers SET settle_hours = {_LEGACY_DEFAULT_SETTLE_HOURS} "
        "WHERE settle_hours IS NULL"
    )
    op.alter_column(
        "deposit_tiers",
        "settle_hours",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=str(_LEGACY_DEFAULT_SETTLE_HOURS),
    )
