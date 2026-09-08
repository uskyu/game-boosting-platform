"""Add CANCELLED to order_claims.status lifecycle enum.

取消订单时未结算报名名额会被置为 CANCELLED（见 order_service.cancel_order）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028_claim_cancelled_status"
down_revision: Union[str, None] = "027_username_change_cooldown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_VALUES = ("CLAIMED", "DELIVERED", "SETTLED")
NEW_VALUES = ("CLAIMED", "DELIVERED", "SETTLED", "CANCELLED")


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="claim_lifecycle_enum")


def upgrade() -> None:
    op.alter_column(
        "order_claims",
        "status",
        existing_type=_enum(OLD_VALUES),
        type_=_enum(NEW_VALUES),
        existing_nullable=False,
        existing_server_default="CLAIMED",
    )


def downgrade() -> None:
    # 先把残留的 CANCELLED 名额退回 CLAIMED，避免缩短枚举时数据非法
    op.execute("UPDATE order_claims SET status = 'CLAIMED' WHERE status = 'CANCELLED'")
    op.alter_column(
        "order_claims",
        "status",
        existing_type=_enum(NEW_VALUES),
        type_=_enum(OLD_VALUES),
        existing_nullable=False,
        existing_server_default="CLAIMED",
    )
