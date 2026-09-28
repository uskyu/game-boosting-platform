"""withdrawal payout batches（提现打款批次）+ withdrawal_requests.payout_batch_id

管理员把选中的提现申请生成批量转账文件上传到渠道（支付宝/微信），再把
渠道返回的回单导入：回单汇总（笔数/金额、成功失败拆分、渠道批次号）快照
到 ``withdrawal_payout_batches``，提现申请通过 ``withdrawal_requests.payout_batch_id``
关联到所属批次。

Revision ID: 042_withdrawal_payout_batches
Revises: 041_withdrawal_channel_settings
Create Date: 2026-09-28 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '042_withdrawal_payout_batches'
down_revision = '041_withdrawal_channel_settings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'withdrawal_payout_batches',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('category', sa.Enum('ALIPAY', 'WECHAT', name='withdrawal_payout_category_enum'), nullable=False),
        sa.Column('remark', sa.String(length=120), nullable=False),
        sa.Column('item_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False, server_default='0'),
        sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='withdrawal_payout_batch_status_enum'), nullable=False, server_default='OPEN'),
        sa.Column('reference_no', sa.String(length=64), nullable=True),
        sa.Column('success_count', sa.Integer(), nullable=True),
        sa.Column('success_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('fail_count', sa.Integer(), nullable=True),
        sa.Column('fail_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('imported_by', sa.Integer(), nullable=True),
        sa.Column('imported_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_withdrawal_payout_batches_id', 'withdrawal_payout_batches', ['id'])
    op.create_index('ix_withdrawal_payout_batches_status', 'withdrawal_payout_batches', ['status'])

    # 提现申请 -> 打款批次（可空：不在任何批次里的申请为 NULL）。
    op.add_column('withdrawal_requests', sa.Column('payout_batch_id', sa.Integer(), nullable=True))
    op.create_index('ix_withdrawal_requests_payout_batch_id', 'withdrawal_requests', ['payout_batch_id'])


def downgrade():
    op.drop_index('ix_withdrawal_requests_payout_batch_id', table_name='withdrawal_requests')
    op.drop_column('withdrawal_requests', 'payout_batch_id')
    op.drop_index('ix_withdrawal_payout_batches_status', table_name='withdrawal_payout_batches')
    op.drop_index('ix_withdrawal_payout_batches_id', table_name='withdrawal_payout_batches')
    op.drop_table('withdrawal_payout_batches')
