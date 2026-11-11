"""
Publisher blacklist model.

发单人维度的账号级黑名单（平表，不做等级/处罚阶梯）：
- 发单人拉黑某用户后，该用户不能再接这个发单人的订单，但仍可查看；
- 只影响后续接单，不追溯已接订单/已报名名额；
- 解除即物理删除，无软删/状态机。
"""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class PublisherBlacklist(TimestampMixin, Base):
    """发单人黑名单：一条记录 = 某发单人拉黑了某用户。"""

    __tablename__ = "publisher_blacklists"
    __table_args__ = (
        # 同一发单人对同一用户只能有一条（重复拉黑幂等）
        UniqueConstraint(
            "publisher_id",
            "blocked_user_id",
            name="uq_publisher_blacklists_publisher_blocked",
        ),
        # 「当前查看者被哪些发单人拉黑」的反查索引
        Index(
            "ix_publisher_blacklists_blocked_user_id_publisher_id",
            "blocked_user_id",
            "publisher_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    publisher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    blocked_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 可选备注，仅发单人/管理员可见，不对被拉黑用户展示
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 关系默认不加载，避免认证/列表路径被隐式 JOIN 放大。
    publisher: Mapped["User"] = relationship(
        "User", foreign_keys=[publisher_id], lazy="noload"
    )
    blocked_user: Mapped["User"] = relationship(
        "User", foreign_keys=[blocked_user_id], lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<PublisherBlacklist(publisher_id={self.publisher_id}, "
            f"blocked_user_id={self.blocked_user_id})>"
        )
