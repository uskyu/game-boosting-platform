"""site_settings 增加 notification_sounds：可自定义的通知提示音

Revision ID: 078_notification_sounds
Revises: 077_signed_url_cache
Create Date: 2026-11-22 00:00:00.000000

背景：前端原有的来单/被接手/取消申请提示音是写死的静态 WAV，聊天提示音是
WebAudio 合成音。管理员需要能按槽位上传 MP3/WAV、调音量、静音，并在前台
即时生效。

本迁移是纯加法迁移，不改变任何现有行为：``notification_sounds`` 为 JSON
可空列，NULL 表示「四个槽位全部使用内置默认音频」。只有管理员在后台保存
过配置后才会写入对象，因此无需回填历史行。

JSON 形状::

    {
      "new_order":        {"url": null, "volume": 80, "enabled": true},
      "claimed":          {"url": null, "volume": 80, "enabled": true},
      "cancel_requested": {"url": null, "volume": 80, "enabled": true},
      "chat_message":     {"url": null, "volume": 80, "enabled": true}
    }
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "078_notification_sounds"
down_revision: str | None = "077_signed_url_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "notification_sounds",
            sa.JSON(),
            nullable=True,
            comment="通知提示音配置：每槽位 {url, volume, enabled}；NULL=全部使用内置默认",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_settings", "notification_sounds")
