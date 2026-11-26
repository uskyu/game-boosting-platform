"""数据库容灾备份：站点备份设置 + 备份执行记录

Revision ID: 080_database_backup
Revises: 079_login_session_days
Create Date: 2026-11-26 00:00:00.000000

背景：生产库需要定期容灾备份。调度器按 ``site_settings`` 里的开关与间隔
把 MySQL dump 成 gzip 后上传腾讯云 COS；本迁移新增对应配置列与执行记录表。

纯加法迁移：六个配置列都有 server_default，存量 site_settings 行自动获得
默认值（备份默认关闭，不影响升级前行为）；``database_backups`` 只由应用层
写入，不需要回填历史数据。

``started_at`` 的默认值显式写成 ``CURRENT_TIMESTAMP``：MySQL 对 DATETIME
列使用常量文本默认值，避免 ``now()`` 在部分版本上生成不被接受的 DDL。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "080_database_backup"
down_revision: str | None = "079_login_session_days"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- site_settings：六个备份配置列（均带 server_default，存量行无需回填）
    op.add_column(
        "site_settings",
        sa.Column(
            "db_backup_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="是否启用定时数据库备份：0=关闭，1=开启",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "db_backup_interval_minutes",
            sa.Integer(),
            nullable=False,
            server_default="60",
            comment="自动备份间隔分钟数（业务范围 5-1440，由 API 层校验）",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "db_backup_retention_count",
            sa.Integer(),
            nullable=False,
            server_default="48",
            comment="保留最近多少份成功备份（更早的连同 COS 对象一起清理）",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "db_backup_prefix",
            sa.String(length=120),
            nullable=False,
            server_default=sa.text("'db-backups'"),
            comment="COS 对象 key 前缀，例如 db-backups 或 db-backups/cn1",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "db_backup_include_notifications",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="备份是否包含 notifications 表数据（默认只导出该表结构）",
        ),
    )
    op.add_column(
        "site_settings",
        sa.Column(
            "db_backup_requested_at",
            sa.DateTime(),
            nullable=True,
            comment="管理员手动备份请求时间（naive UTC）；调度器消费后清空",
        ),
    )

    # --- 备份执行记录表
    op.create_table(
        "database_backups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="RUNNING",
            comment="RUNNING / SUCCESS / FAILED",
        ),
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            server_default="SCHEDULED",
            comment="SCHEDULED=定时触发；MANUAL=管理员手动触发",
        ),
        sa.Column(
            "object_key",
            sa.String(length=500),
            nullable=True,
            comment="成功上传后的 COS 对象 key",
        ),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            nullable=True,
            comment="备份文件字节数（上传后 HEAD 核对的值）",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="开始时间（naive UTC）",
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="结束时间（naive UTC）"),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=True,
            comment="本次备份耗时（毫秒）",
        ),
        sa.Column("error_message", sa.Text(), nullable=True, comment="失败原因（中文，已截断）"),
        sa.Column(
            "include_notifications",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="本次是否包含 notifications 表数据",
        ),
        sa.Column(
            "triggered_by",
            sa.Integer(),
            nullable=True,
            comment="手动触发人 users.id；定时任务为 NULL",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by"],
            ["users.id"],
            name="fk_database_backups_triggered_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 状态索引：调度器查最近一次运行、清理 FAILED 旧行都要走它。
    op.create_index(
        "ix_database_backups_status", "database_backups", ["status"], unique=False
    )
    # 开始时间索引：保留清理按 started_at desc 排序。
    op.create_index(
        "ix_database_backups_started_at", "database_backups", ["started_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_database_backups_started_at", table_name="database_backups")
    op.drop_index("ix_database_backups_status", table_name="database_backups")
    op.drop_table("database_backups")

    # 配置列按 upgrade 的相反顺序删除。
    for column in (
        "db_backup_requested_at",
        "db_backup_include_notifications",
        "db_backup_prefix",
        "db_backup_retention_count",
        "db_backup_interval_minutes",
        "db_backup_enabled",
    ):
        op.drop_column("site_settings", column)
