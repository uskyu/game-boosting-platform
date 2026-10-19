"""后台「支付记录」：充值单支持人工改判成功/失败 + 冲正流水 + 审计字段。

老板 2026-10-19 需求：管理员要能看到平台所有用户的支付记录（发起了支付、
回调是否成功），并能把单条记录改判为成功或失败：

- 改判成功 → 充值金额入账；
- 改判失败 → 已入账的金额冲正扣回。

金额一律由「状态是否跨越」决定（``recharge_service.admin_update_status``），
不是由点了哪个按钮决定：PENDING 本来没入账，改失败不动钱；SUCCESS 已入账，
改失败才产生冲正。因此本迁移做四件事：

1. ``recharge_status_enum`` 末尾追加 ``FAILED``（人工置为失败）。
   原来只有 PENDING / SUCCESS / CLOSED，其中 CLOSED 是「用户放弃/超时」，
   语义上不能复用成「支付失败」，分开存才能查得清。
2. ``wallet_transaction_type_enum`` 末尾追加 ``RECHARGE_REVERSAL``
   （充值冲正，可用余额 −）。
3. ``recharge_orders`` 追加审计三列：``marked_by``（操作管理员，
   users.id ON DELETE SET NULL）、``marked_at``、``admin_note``（改判原因）。
4. ``notification_type_enum`` 末尾追加 ``RECHARGE_UPDATED``：人工改动余额
   必须让用户端看得到，静默改钱会直接变成客服工单。

**刻意不动 ``uq_wallet_tx_recharge_order``**：那条「单列唯一」是易支付重复
回调的第三道防线（回调只写 ``RECHARGE`` 且必带 ``recharge_order_id``）。
人工冲正与人工补正的流水**不带** ``recharge_order_id``（置 NULL，remark 写
商户单号），因此该唯一键照旧只约束回调路径，人工可反复改判而不会被
唯一键挡住；MySQL 唯一索引不把多个 NULL 视为重复，这一点与 029 的前提一致。

追加在枚举末尾的写法与 029/032/044/052/054/056 保持一致：MySQL
``MODIFY`` 整体替换枚举定义，追加值不触发表数据重写。

Revision ID: 058_admin_recharge_records
Revises: 057_deposit_tier_settle_nullable
Create Date: 2026-10-19 15:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "058_admin_recharge_records"
down_revision: Union[str, None] = "057_deposit_tier_settle_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 058 之前 recharge_orders.status 的三个取值（029 建表）
_OLD_RECHARGE_STATUS = "'PENDING','SUCCESS','CLOSED'"
# FAILED = 人工置为失败（与 CLOSED「用户放弃/超时关闭」区分开）
_NEW_RECHARGE_STATUS = _OLD_RECHARGE_STATUS + ",'FAILED'"

# 057 时 wallet_transactions.type 的全部取值（056 已追加售后保证金抵扣）
_OLD_WALLET_TX_TYPES = (
    "'ORDER_INCOME','ADMIN_ADJUST','WITHDRAWAL_FREEZE',"
    "'WITHDRAWAL_REFUND','WITHDRAWAL_PAID',"
    "'ESCROW_HOLD','ESCROW_RELEASE','ORDER_PAYMENT',"
    "'DEPOSIT_HOLD','DEPOSIT_RELEASE','COMPENSATION_DEDUCT','RECHARGE',"
    "'DEPOSIT_TRANSFER_IN','DEPOSIT_TRANSFER_OUT',"
    "'CANCEL_COMPENSATION_IN','CANCEL_COMPENSATION_DEDUCT',"
    "'AFTERSALE_DEDUCTION','AFTERSALE_REFUND','AFTERSALE_DEPOSIT_DEDUCTION'"
)
# RECHARGE_REVERSAL：充值冲正，可用余额扣减（人工把已到账充值改判失败）
_NEW_WALLET_TX_TYPES = _OLD_WALLET_TX_TYPES + ",'RECHARGE_REVERSAL'"

# 052 之后 notifications.type 的全部取值
_OLD_NOTIFICATION_TYPES = (
    "'ORDER_ACCEPTED','ORDER_DELIVERED','ORDER_CONFIRMED','ORDER_DISPUTED',"
    "'ORDER_CANCELLED','NEW_MESSAGE','APPLICATION_APPROVED','APPLICATION_REJECTED',"
    "'REVIEW_RECEIVED','SYSTEM_ANNOUNCEMENT',"
    "'ORDER_CANCEL_REQUESTED','ORDER_CANCEL_REQUEST_RESOLVED',"
    "'AFTERSALES_CREATED','AFTERSALES_RESOLVED'"
)
# RECHARGE_UPDATED：充值记录被管理员人工改判，余额已随之变动
_NEW_NOTIFICATION_TYPES = _OLD_NOTIFICATION_TYPES + ",'RECHARGE_UPDATED'"


def upgrade() -> None:
    # 1) 充值单状态追加 FAILED（只追加，不改存量行的值）
    op.execute(
        f"ALTER TABLE recharge_orders MODIFY COLUMN `status` "
        f"ENUM({_NEW_RECHARGE_STATUS}) NOT NULL DEFAULT 'PENDING'"
    )

    # 2) 钱包流水追加冲正类型
    # 仅MySQL：MODIFY 整体替换枚举定义；本项目生产/CI 均为 MySQL，切库需重写本迁移
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_NEW_WALLET_TX_TYPES}) NOT NULL"
    )

    # 3) 人工改判审计三列：谁改的、什么时候改的、为什么改
    op.add_column(
        "recharge_orders", sa.Column("marked_by", sa.Integer(), nullable=True)
    )
    op.add_column(
        "recharge_orders", sa.Column("marked_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "recharge_orders", sa.Column("admin_note", sa.String(length=255), nullable=True)
    )
    op.create_foreign_key(
        "fk_recharge_orders_marked_by",
        "recharge_orders",
        "users",
        ["marked_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # 4) 通知类型追加 RECHARGE_UPDATED
    op.execute(
        f"ALTER TABLE notifications MODIFY COLUMN `type` "
        f"ENUM({_NEW_NOTIFICATION_TYPES}) NOT NULL"
    )


def downgrade() -> None:
    # 枚举缩短前先把新值改写成语义最接近的既有取值，**保留全部账务与通知记录**，
    # 不删除任何一行。

    # 通知：人工改判通知退化为系统公告（同为用户可见的站内提示）
    op.execute(
        "UPDATE notifications SET `type` = 'SYSTEM_ANNOUNCEMENT' "
        "WHERE `type` = 'RECHARGE_UPDATED'"
    )
    op.execute(
        f"ALTER TABLE notifications MODIFY COLUMN `type` "
        f"ENUM({_OLD_NOTIFICATION_TYPES}) NOT NULL"
    )

    # 审计列先删（外键依赖其上的索引，必须先删外键再删列）
    op.drop_constraint(
        "fk_recharge_orders_marked_by", "recharge_orders", type_="foreignkey"
    )
    op.drop_column("recharge_orders", "admin_note")
    op.drop_column("recharge_orders", "marked_at")
    op.drop_column("recharge_orders", "marked_by")

    # 冲正流水退化为 ADMIN_ADJUST：同为「可用余额调整」，amount 与
    # balance_before/after 快照全部保留，账仍对得上。
    op.execute(
        "UPDATE wallet_transactions SET type = 'ADMIN_ADJUST' "
        "WHERE type = 'RECHARGE_REVERSAL'"
    )
    op.execute(
        f"ALTER TABLE wallet_transactions MODIFY COLUMN `type` "
        f"ENUM({_OLD_WALLET_TX_TYPES}) NOT NULL"
    )

    # 人工置失败的充值单退化为 CLOSED（终态且不入账，与 FAILED 的资金效果一致）
    op.execute("UPDATE recharge_orders SET status = 'CLOSED' WHERE status = 'FAILED'")
    op.execute(
        f"ALTER TABLE recharge_orders MODIFY COLUMN `status` "
        f"ENUM({_OLD_RECHARGE_STATUS}) NOT NULL DEFAULT 'PENDING'"
    )
