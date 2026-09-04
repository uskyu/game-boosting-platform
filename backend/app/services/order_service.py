"""
Order service module.
Business logic for order management operations.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import encrypt_text, escape_like
from app.models.booster_service import BoosterService
from app.models.game import Game
from app.models.order import (
    ClaimLifecycleStatus,
    ClaimStatus,
    Order,
    OrderClaim,
    OrderStatus,
    PaymentStatus,
)
from app.models.user import User, UserRole
from app.models.wallet import WalletTransaction, WalletTransactionType
from app.schemas.booster_service import BoosterServiceOrderCreate
from app.schemas.order import OrderCreate, OrderUpdate
from app.services.ai_service import LLMService
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")


def _to_decimal(value: Decimal | int | str | None) -> Decimal:
    """Coerce amounts to Decimal (never float); None -> 0."""
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class OrderService:
    """
    Service class for order-related business logic.
    Handles CRUD operations and business rules for orders.
    """

    def __init__(self, db: AsyncSession) -> None:
        """
        Initialize order service with database session.

        Args:
            db: Async database session.
        """
        self._db = db

    async def analyze_requirement(
        self,
        text: str,
        llm_service: LLMService,
    ) -> dict[str, Any]:
        """
        Analyze user requirement and build ai_tags-compatible result.
        """
        result = await llm_service.analyze_requirement(text)
        if result.get("is_risky", False):
            return result

        game = await self._resolve_game(
            game_name=result.get("game_name"),
            text=text,
        )
        service_type = self._infer_service_type(
            description=text,
            game=game,
            extracted_service_type=result.get("service_type"),
        )
        server = self._normalize_server_for_game(
            server=result.get("server"),
            game=game,
        )
        ai_tags = self._build_ai_tags(
            existing=result.get("ai_tags"),
            game=game,
            server=server,
            service_type=service_type,
            current_rank=result.get("current_rank"),
            target_rank=result.get("target_rank"),
            role=result.get("role"),
            requirements=self._normalize_requirements(result.get("requirements")),
        )

        return {
            "game_id": game.id if game is not None else None,
            "game_name": game.name if game is not None else result.get("game_name"),
            "current_rank": result.get("current_rank"),
            "target_rank": result.get("target_rank"),
            "price": result.get("price"),
            "role": result.get("role"),
            "server": server,
            "service_type": service_type,
            "ai_tags": ai_tags,
            "is_risky": result.get("is_risky", False),
        }

    async def create_order(
        self,
        order_data: OrderCreate,
        user: User,
    ) -> Order:
        """
        Create a new order for a user.

        Args:
            order_data: Validated order creation data.
            user: User creating the order.

        Returns:
            Created Order instance.

        Raises:
            HTTPException: If the user cannot escrow the order amount.

        Notes:
            - 任何活跃用户均可发单。ADMIN（平台单）不冻结资金；
            - 非管理员发布时托管 price × max_claims：发布人可用余额
              减少、冻结余额增加（ESCROW_HOLD），余额不足返回 400。
            - 被禁止发单（can_publish=False）的非管理员返回 403；
              ADMIN 发单不受 can_publish 限。
        """
        if user.role != UserRole.ADMIN and not user.can_publish:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="您已被禁止发布订单",
            )
        game = await self._resolve_game(
            game_id=order_data.game_id,
            game_name=order_data.game_name,
        )
        current_rank = (
            order_data.current_rank
            or self._extract_ai_detail_value(order_data.ai_tags, "current_rank")
            or "未指定"
        )
        target_rank = (
            order_data.target_rank
            or self._extract_ai_detail_value(order_data.ai_tags, "target_rank")
            or "未指定"
        )
        game_name = order_data.game_name or (game.name if game is not None else None)
        if not game_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="游戏名称不能为空",
            )

        service_type = order_data.service_type or self._extract_ai_root_value(order_data.ai_tags, "service_type")
        server = order_data.server or self._extract_ai_root_value(order_data.ai_tags, "server")
        if game is not None and service_type:
            self._validate_service_type(game, service_type)

        ai_tags = self._build_ai_tags(
            existing=order_data.ai_tags,
            game=game,
            server=server,
            service_type=service_type,
            current_rank=current_rank,
            target_rank=target_rank,
            role=order_data.role,
            requirements=self._extract_ai_detail_requirements(order_data.ai_tags),
        )

        # 非管理员发单需托管 price × max_claims；先校验余额再落单，
        # 不足直接 400（不产生订单残留）。
        escrow_required: Decimal | None = None
        publisher_wallet = None
        if user.role != UserRole.ADMIN:
            escrow_required = _to_decimal(order_data.price) * int(order_data.max_claims)
            wallet_service = get_wallet_service(self._db)
            publisher_wallet = await wallet_service.get_or_create_wallet(user.id)
            if _to_decimal(publisher_wallet.available_balance) < escrow_required:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"余额不足以托管该订单（需 ¥{escrow_required}）",
                )

        order = Order(
            user_id=user.id,
            game_id=game.id if game is not None else order_data.game_id,
            game_name=game.name if game is not None else game_name,
            current_rank=current_rank,
            target_rank=target_rank,
            price=order_data.price,
            price_min=order_data.price_min or order_data.price,
            price_max=order_data.price_max or order_data.price,
            title=order_data.title,
            intro=order_data.intro or order_data.description,
            description=order_data.description or order_data.intro,
            max_claims=order_data.max_claims,
            claim_status=ClaimStatus.OPEN,
            deadline=order_data.deadline,
            attachments=order_data.attachments,
            description_raw=order_data.description_raw,
            description_ai=order_data.description_ai,
            ai_tags=ai_tags,
            game_account=order_data.game_account,
            game_password=encrypt_text(order_data.game_password),
            service_type=service_type,
            server=server,
            priority=order_data.priority,
            notes=order_data.notes,
            boss_contact=order_data.boss_contact,
            compensation_amount=order_data.compensation_amount,
            payout_delay_days=order_data.payout_delay_days,
            payout_delay_hours=order_data.payout_delay_hours,
            status=OrderStatus.PENDING,
        )

        self._db.add(order)
        await self._db.flush()

        # 托管冻结（非管理员发布人）；hold_escrow 内部带行锁二次校验
        if escrow_required is not None:
            await wallet_service.hold_escrow(
                publisher_wallet, amount=escrow_required, order_id=order.id
            )

        await self._db.refresh(order)

        logger.info(f"Created order {order.id} for user {user.id}")

        return order

    async def create_service_order(
        self,
        service: BoosterService,
        payload: BoosterServiceOrderCreate,
        current_user: User,
    ) -> Order:
        """
        Create a locked order directly from a service card.
        """
        if current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有管理员可以发布订单，普通用户和打手不能发单",
            )

        if current_user.id == service.booster_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能下单自己的服务",
            )

        if not service.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该服务已下架",
            )

        # Lock booster row and check quota to prevent concurrent overselling
        booster_result = await self._db.execute(
            select(User).where(User.id == service.booster_id).with_for_update()
        )
        booster = booster_result.scalar_one_or_none()
        if booster is None or not booster.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该服务的代练已不可用",
            )

        active_count_result = await self._db.execute(
            select(func.count(Order.id)).where(
                Order.booster_id == booster.id,
                Order.status == OrderStatus.LOCKED,
            )
        )
        active_count = int(active_count_result.scalar() or 0)
        if booster.booster_quota <= active_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该代练当前接单额度已满，请稍后再试",
            )

        current_rank = (
            payload.current_rank
            or self._extract_ai_detail_value(payload.ai_tags, "current_rank")
            or "未指定"
        )
        target_rank = (
            payload.target_rank
            or self._extract_ai_detail_value(payload.ai_tags, "target_rank")
            or "未指定"
        )
        server = payload.server or self._extract_ai_root_value(payload.ai_tags, "server")
        ai_tags = self._build_ai_tags(
            existing=payload.ai_tags,
            game=service.game,
            server=server,
            service_type=service.service_type,
            current_rank=current_rank,
            target_rank=target_rank,
            role=self._extract_ai_detail_value(payload.ai_tags, "role"),
            requirements=self._extract_ai_detail_requirements(payload.ai_tags),
        )

        price = payload.price
        if price is None:
            estimated_hours = payload.estimated_hours or Decimal("1")
            price = (service.price_per_hour * estimated_hours).quantize(Decimal("0.01"))

        order = Order(
            user_id=current_user.id,
            booster_id=service.booster_id,
            game_id=service.game_id,
            service_id=service.id,
            game_name=service.game.name,
            current_rank=current_rank,
            target_rank=target_rank,
            price=price,
            status=OrderStatus.LOCKED,
            description_raw=payload.description_raw,
            description_ai=payload.description_ai,
            ai_tags=ai_tags,
            game_account=payload.game_account,
            game_password=encrypt_text(payload.game_password),
            service_type=service.service_type,
            server=server,
            priority=0,
            notes=payload.notes,
            max_claims=1,
            claimed_count=1,
            locked_at=datetime.now(timezone.utc),
        )

        self._db.add(order)
        await self._db.flush()

        # Claim-level delivery requires every assigned booster to own a
        # claim row; service-card orders are single-claim by construction.
        self._db.add(
            OrderClaim(
                order_id=order.id,
                booster_id=service.booster_id,
                status=ClaimLifecycleStatus.CLAIMED,
            )
        )
        await self._db.refresh(order)

        logger.info(
            "Created locked service order %s from service %s for user %s",
            order.id,
            service.id,
            current_user.id,
        )

        return order

    async def get_order_by_id(
        self,
        order_id: int,
        user: User | None = None,
    ) -> Order:
        """
        Get order by ID with access control.

        Args:
            order_id: Order ID to fetch.
            user: Optional user for access control.

        Returns:
            Order instance.

        Raises:
            HTTPException: If order not found or access denied.
        """
        result = await self._db.execute(
            select(Order)
            .options(
                selectinload(Order.user),
                selectinload(Order.booster),
            )
            .where(Order.id == order_id)
        )
        order = result.scalar_one_or_none()

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )

        # Access control
        if user is not None and user.role != UserRole.ADMIN:
            my_claim_exists = (
                await self._db.execute(
                    select(OrderClaim.id)
                    .where(
                        OrderClaim.order_id == order.id,
                        OrderClaim.booster_id == user.id,
                    )
                    .limit(1)
                )
            ).scalar() is not None
            hall_visible = (
                order.status in (OrderStatus.PENDING, OrderStatus.LOCKED)
                and order.claim_status == ClaimStatus.OPEN
                and not order.is_archived
            )
            can_view = (
                order.user_id == user.id
                or (order.booster_id is not None and order.booster_id == user.id)
                or hall_visible
                or my_claim_exists
            )
            if not can_view:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权访问此订单",
                )

        return order

    async def list_orders(
        self,
        user: User | None = None,
        game_name: str | None = None,
        status_filter: OrderStatus | None = None,
        page: int = 1,
        page_size: int = 20,
        mine_published: bool = False,
        boss_contact: str | None = None,
    ) -> tuple[list[Order], int]:
        """
        List orders with filtering and pagination.

        Args:
            user: Optional user for filtering (customers see own, boosters see available).
            mine_published: When true, return only orders published by ``user``;
                this scope takes precedence over the hall/assigned-order scope.
            game_name: Optional game name filter.
            status_filter: Optional status filter.
            boss_contact: Optional boss-contact fuzzy filter (ilike); typically
                combined with mine_published=true so publishers can find their
                own orders by the boss ID they filled in.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Tuple of (orders list, total count).
        """
        query = select(Order).options(
            selectinload(Order.user),
            selectinload(Order.booster),
        )
        count_query = select(func.count(Order.id))

        # Apply user-based filtering
        if mine_published and user is not None:
            publisher_scope = Order.user_id == user.id
            query = query.where(publisher_scope)
            count_query = count_query.where(publisher_scope)
        elif user is not None:
            if user.role != UserRole.ADMIN:
                # Every non-admin account can act as a booster. The hall keeps
                # listing an order while it still has free claim slots
                # (PENDING or multi-claim LOCKED), and boosters always see the
                # orders they have claimed so LOCKED entries do not vanish
                # from "my orders".
                now = datetime.now(timezone.utc)
                claimable = (
                    Order.status.in_((OrderStatus.PENDING, OrderStatus.LOCKED))
                    & (Order.claim_status == ClaimStatus.OPEN)
                    & (Order.is_archived.is_(False))
                    & (or_(Order.deadline.is_(None), Order.deadline > now))
                    & (Order.claimed_count < Order.max_claims)
                )
                my_claim_exists = exists(
                    select(OrderClaim.id).where(
                        OrderClaim.order_id == Order.id,
                        OrderClaim.booster_id == user.id,
                    )
                )
                booster_scope = (
                    claimable
                    | (Order.user_id == user.id)
                    | (Order.booster_id == user.id)
                    | my_claim_exists
                )
                query = query.where(booster_scope)
                count_query = count_query.where(booster_scope)
            # Admins see all orders (no filter)

        # Apply game name filter
        if game_name:
            pattern = f"%{escape_like(game_name)}%"
            query = query.where(Order.game_name.ilike(pattern))
            count_query = count_query.where(Order.game_name.ilike(pattern))

        # Apply status filter
        if status_filter:
            query = query.where(Order.status == status_filter)
            count_query = count_query.where(Order.status == status_filter)

        # Apply boss-contact fuzzy filter
        if boss_contact:
            pattern = f"%{escape_like(boss_contact)}%"
            query = query.where(Order.boss_contact.ilike(pattern))
            count_query = count_query.where(Order.boss_contact.ilike(pattern))

        # Get total count
        total_result = await self._db.execute(count_query)
        total = total_result.scalar() or 0

        # Apply pagination and ordering
        offset = (page - 1) * page_size
        query = query.order_by(Order.created_at.desc()).offset(offset).limit(page_size)

        # Execute query
        result = await self._db.execute(query)
        orders = list(result.scalars().all())

        return orders, total

    async def accept_order(
        self,
        order_id: int,
        booster: User,
    ) -> Order:
        """
        Accept an order as a booster.

        Any active non-admin account may accept orders. Admins manage orders
        via the dedicated /admin/orders/{id}/intervene endpoint.
        """
        if booster.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="管理员请通过管理端处理订单，不能作为打手接单",
            )

        # Lock booster row first to serialize quota checks.
        # Concurrent accepts without this lock can both pass the count check
        # and exceed the quota.
        locked_booster_result = await self._db.execute(
            select(User).where(User.id == booster.id).with_for_update()
        )
        locked_booster = locked_booster_result.scalar_one_or_none()
        if locked_booster is None or not locked_booster.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="代练账号不可用",
            )
        if not locked_booster.can_accept:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="您已被禁止接单",
            )

        active_orders_count_result = await self._db.execute(
            select(func.count(Order.id)).where(
                Order.booster_id == booster.id,
                Order.status == OrderStatus.LOCKED,
            )
        )
        active_orders_count = int(active_orders_count_result.scalar() or 0)
        if locked_booster.role == UserRole.BOOSTER and locked_booster.booster_quota <= active_orders_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前接单额度已满，请先完成现有订单",
            )

        result = await self._db.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
        )
        order = result.scalar_one_or_none()

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )

        now = datetime.now(timezone.utc)
        if order.is_archived:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单已归档，不能接单")
        if order.claim_status != ClaimStatus.OPEN:
            claim_status_messages = {
                ClaimStatus.PAUSED: "订单已暂停抢单",
                ClaimStatus.CLOSED: "订单已截止，不能接单",
                ClaimStatus.FULL: "订单接单人数已满",
            }
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=claim_status_messages.get(order.claim_status, "订单当前不允许接单"),
            )
        if order.status not in (OrderStatus.PENDING, OrderStatus.LOCKED):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单当前状态不允许接单")
        # LOCKED remains claimable only for orders configured for multiple boosters.
        if order.status == OrderStatus.LOCKED and order.max_claims <= 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单已被其他代练接单")
        if order.deadline is not None:
            # MySQL DATETIME 返回 naive 时间，须补 UTC 再与 aware now 比较
            deadline = order.deadline if order.deadline.tzinfo else order.deadline.replace(tzinfo=timezone.utc)
            if deadline <= now:
                order.claim_status = ClaimStatus.CLOSED
                await self._db.flush()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单已截止，不能接单")
        if order.claimed_count >= order.max_claims:
            order.claim_status = ClaimStatus.FULL
            await self._db.flush()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单接单人数已满")
        if order.user_id == booster.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能接取自己的订单")
        existing = await self._db.execute(select(OrderClaim).where(OrderClaim.order_id == order.id, OrderClaim.booster_id == booster.id))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="您已报名过该订单，无需重复报名")
        self._db.add(OrderClaim(order_id=order.id, booster_id=booster.id))
        # 行锁串行化配额检查，唯一约束兜底并发重复报名转409
        order.claimed_count += 1
        if order.booster_id is None:
            order.booster_id = booster.id
            order.status = OrderStatus.LOCKED
            order.locked_at = now
        if order.claimed_count >= order.max_claims:
            order.claim_status = ClaimStatus.FULL

        # 炸单赔偿金：接单即从打手可用余额冻结（同一事务，行锁保护）。
        # 余额不足时 hold_deposit 抛 400，整个接单（含名额）一并回滚。
        compensation = _to_decimal(order.compensation_amount)
        if compensation > _ZERO:
            wallet_service = get_wallet_service(self._db)
            booster_wallet = await wallet_service.get_or_create_wallet(booster.id)
            await wallet_service.hold_deposit(
                booster_wallet,
                amount=compensation,
                order_id=order.id,
                booster_id=booster.id,
            )

        try:
            await self._db.flush()
            await self._db.refresh(order)
        except IntegrityError:
            # Concurrency fallback for UniqueConstraint uq_order_claim_booster:
            # two requests can both pass the row lock + pre-check window, and
            # only one INSERT survives. Translate the violation into the same
            # 409 instead of an unhandled 500.
            await self._db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="您已报名过该订单，无需重复报名",
            )

        logger.info(f"Order {order_id} accepted by booster {booster.id}")

        return order

    @staticmethod
    def _enum_value(value: Any) -> Any:
        """Return the raw value for enum-ish column data."""
        return value.value if hasattr(value, "value") else value

    def _serialize_claim(
        self,
        claim: OrderClaim,
        *,
        order_booster_id: int | None,
        booster_nickname: str | None = None,
        booster_email: str | None = None,
    ) -> dict[str, Any]:
        """Serialize a claim into the public API contract shape."""
        return {
            "id": claim.id,
            "order_id": claim.order_id,
            "booster_id": claim.booster_id,
            "booster_nickname": booster_nickname,
            "booster_email": booster_email,
            "status": self._enum_value(claim.status),
            "delivery_note": claim.delivery_note,
            "delivery_attachments": claim.delivery_attachments or None,
            "created_at": claim.created_at,
            "delivered_at": claim.delivered_at,
            "settled_at": claim.settled_at,
            "is_first": order_booster_id == claim.booster_id,
        }

    async def _claim_view_with_user(
        self, claim: OrderClaim, order: Order
    ) -> dict[str, Any]:
        """Claim contract dict enriched with the booster's nickname/email."""
        user_result = await self._db.execute(
            select(User.username, User.email).where(User.id == claim.booster_id)
        )
        row = user_result.one_or_none()
        username = row.username if row is not None else None
        email = row.email if row is not None else None
        return self._serialize_claim(
            claim,
            order_booster_id=order.booster_id,
            booster_nickname=username,
            booster_email=email,
        )

    async def get_order_claim_view(
        self, order: Order, booster: User
    ) -> dict[str, Any] | None:
        """The booster's claim contract dict on this order, or None."""
        result = await self._db.execute(
            select(OrderClaim).where(
                OrderClaim.order_id == order.id, OrderClaim.booster_id == booster.id
            )
        )
        claim = result.scalar_one_or_none()
        if claim is None:
            return None
        return self._serialize_claim(
            claim,
            order_booster_id=order.booster_id,
            booster_nickname=booster.username,
            booster_email=booster.email,
        )

    async def claims_view_for_booster(
        self, orders: list[Order], booster: User
    ) -> dict[int, dict[str, Any]]:
        """Map order_id -> the booster's claim contract dict (batched)."""
        order_ids = [order.id for order in orders]
        if not order_ids:
            return {}
        result = await self._db.execute(
            select(OrderClaim).where(
                OrderClaim.booster_id == booster.id,
                OrderClaim.order_id.in_(order_ids),
            )
        )
        claims = {claim.order_id: claim for claim in result.scalars().all()}
        views: dict[int, dict[str, Any]] = {}
        for order in orders:
            claim = claims.get(order.id)
            if claim is None:
                continue
            views[order.id] = self._serialize_claim(
                claim,
                order_booster_id=order.booster_id,
                booster_nickname=booster.username,
                booster_email=booster.email,
            )
        return views

    async def claim_status_counts(
        self, order_ids: list[int]
    ) -> dict[int, dict[str, int]]:
        """Map order_id -> {'DELIVERED': n, 'SETTLED': m, 'CLAIMED': k}."""
        if not order_ids:
            return {}
        result = await self._db.execute(
            select(OrderClaim.order_id, OrderClaim.status, func.count(OrderClaim.id))
            .where(OrderClaim.order_id.in_(order_ids))
            .group_by(OrderClaim.order_id, OrderClaim.status)
        )
        counts: dict[int, dict[str, int]] = {}
        for order_id, status_value, count in result.all():
            counts.setdefault(order_id, {})[self._enum_value(status_value)] = int(
                count or 0
            )
        return counts

    async def list_order_claims(self, order_id: int) -> list[dict[str, Any]]:
        """
        List the booster claim (报名) records of an order, oldest first.

        Args:
            order_id: Order ID whose claim list should be returned.

        Returns:
            List of claim dicts enriched with the booster's username/email,
            the per-claim lifecycle fields and an ``is_first`` flag marking
            the claim whose booster matches the order's current booster
            (i.e. the first successful grab).

        Raises:
            HTTPException: 404 if the order does not exist.
        """
        order_result = await self._db.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )
        order_booster_id = order.booster_id

        rows = await self._db.execute(
            select(OrderClaim, User.username, User.email)
            .join(User, OrderClaim.booster_id == User.id)
            .where(OrderClaim.order_id == order_id)
            .order_by(OrderClaim.created_at.asc(), OrderClaim.id.asc())
        )
        claims: list[dict[str, Any]] = []
        for claim, username, email in rows.all():
            claims.append(
                self._serialize_claim(
                    claim,
                    order_booster_id=order_booster_id,
                    booster_nickname=username,
                    booster_email=email,
                )
            )
        return claims

    async def list_my_claims(
        self,
        booster_id: int,
        status_filter: ClaimLifecycleStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Paginated claims of one booster (我的报名), newest first.

        Each item is the claim contract dict plus an ``order`` summary.
        """
        conditions = [OrderClaim.booster_id == booster_id]
        if status_filter is not None:
            conditions.append(OrderClaim.status == status_filter)

        total_result = await self._db.execute(
            select(func.count(OrderClaim.id)).where(*conditions)
        )
        total = int(total_result.scalar() or 0)

        result = await self._db.execute(
            select(OrderClaim, Order)
            .join(Order, OrderClaim.order_id == Order.id)
            .where(*conditions)
            .order_by(OrderClaim.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        items: list[dict[str, Any]] = []
        for claim, order in result.all():
            item = self._serialize_claim(
                claim, order_booster_id=order.booster_id
            )
            item["order"] = {
                "id": order.id,
                "title": order.title,
                "intro": order.intro,
                "game_name": order.game_name,
                "price": order.price,
                "price_min": order.price_min,
                "price_max": order.price_max,
                "status": self._enum_value(order.status),
                "claim_status": self._enum_value(order.claim_status),
                "claimed_count": order.claimed_count,
                "max_claims": order.max_claims,
                # 我的报名必然已接单：老板联系方式对本人可见
                "boss_contact": order.boss_contact,
                "compensation_amount": order.compensation_amount,
                "payout_delay_days": order.payout_delay_days,
                "payout_delay_hours": order.payout_delay_hours,
            }
            items.append(item)
        return items, total

    async def assign_order(
        self,
        order_id: int,
        booster_id: int,
        admin: User,
    ) -> Order:
        """
        Admin assigns a PENDING order to a booster.

        Mirrors accept_order's safety rules: locks the booster row to
        serialize quota checks, locks the order row, and only assigns
        PENDING orders that have no booster yet.
        """
        if admin.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有管理员才能派单",
            )

        # Lock booster row first to serialize quota checks.
        locked_booster_result = await self._db.execute(
            select(User).where(User.id == booster_id).with_for_update()
        )
        locked_booster = locked_booster_result.scalar_one_or_none()
        if locked_booster is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标用户不存在",
            )
        if locked_booster.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="管理员账号不能作为打手接单",
            )
        if not locked_booster.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="代练账号不可用",
            )
        if not locked_booster.can_accept:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该用户已被禁止接单",
            )

        active_orders_count_result = await self._db.execute(
            select(func.count(Order.id)).where(
                Order.booster_id == locked_booster.id,
                Order.status == OrderStatus.LOCKED,
            )
        )
        active_orders_count = int(active_orders_count_result.scalar() or 0)
        # Quota caps only apply to reviewed BOOSTER accounts; any registered
        # USER may be assigned, mirroring accept_order's open-claiming rule.
        if locked_booster.role == UserRole.BOOSTER and locked_booster.booster_quota <= active_orders_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该代练当前接单额度已满，请先完成现有订单",
            )

        result = await self._db.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
        )
        order = result.scalar_one_or_none()

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )

        if order.status != OrderStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有待接单的订单才能派单",
            )

        if order.booster_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="订单已被接取或已派单",
            )

        if order.user_id == locked_booster.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不能派单给下单用户本人",
            )

        order.booster_id = locked_booster.id
        order.status = OrderStatus.LOCKED
        order.locked_at = datetime.now(timezone.utc)

        # Dispatch must materialize a claim row too: claim-level delivery
        # requires the booster to own a CLAIMED record on the order.
        existing_claim = await self._db.execute(
            select(OrderClaim).where(
                OrderClaim.order_id == order.id,
                OrderClaim.booster_id == locked_booster.id,
            )
        )
        if existing_claim.scalar_one_or_none() is None:
            self._db.add(
                OrderClaim(
                    order_id=order.id,
                    booster_id=locked_booster.id,
                    status=ClaimLifecycleStatus.CLAIMED,
                )
            )
            order.claimed_count += 1
        if order.claimed_count >= order.max_claims:
            order.claim_status = ClaimStatus.FULL

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(
            f"Order {order_id} assigned to booster {booster_id} by admin {admin.id}"
        )

        return order

    async def deliver_order(
        self,
        order_id: int,
        user: User,
        delivery_note: str | None = None,
    ) -> tuple[Order, OrderClaim]:
        """
        A claiming booster submits completion for their own slot (名额).

        Marks the caller's claim CLAIMED -> DELIVERED with the optional
        report note; the order itself stays PENDING/LOCKED so the remaining
        slots remain claimable and other boosters are unaffected. Admin-driven
        state changes must go through /admin/orders/{id}/intervene.

        Returns (order, claim) so the response can embed my_claim.
        """
        locked_result = await self._db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        order = locked_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )

        if order.status not in (OrderStatus.PENDING, OrderStatus.LOCKED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有进行中的订单才能结束",
            )

        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有已报名的打手才能交付",
            )

        claim_result = await self._db.execute(
            select(OrderClaim)
            .where(OrderClaim.order_id == order.id, OrderClaim.booster_id == user.id)
            .with_for_update()
        )
        claim = claim_result.scalar_one_or_none()
        if claim is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有已报名的打手才能交付",
            )
        if claim.status == ClaimLifecycleStatus.DELIVERED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="您已提交过交付，请等待审核",
            )
        if claim.status == ClaimLifecycleStatus.SETTLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该报名记录已结算，无需重复交付",
            )

        claim.status = ClaimLifecycleStatus.DELIVERED
        claim.delivered_at = datetime.now(timezone.utc)
        if delivery_note is not None:
            claim.delivery_note = delivery_note.strip() or None

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(
            f"Order {order_id} claim {claim.id} delivered by user {user.id}"
        )

        return order, claim

    async def get_my_claim_for_delivery(
        self,
        order_id: int,
        user: User,
    ) -> tuple[Order, OrderClaim]:
        """Fetch the order plus the caller's claim for delivery attachments.

        The claim must exist and must not be settled yet; attachments are
        stored on the claim, not on the order.
        """
        order = await self.get_order_by_id(order_id, user)

        claim_result = await self._db.execute(
            select(OrderClaim).where(
                OrderClaim.order_id == order.id, OrderClaim.booster_id == user.id
            )
        )
        claim = claim_result.scalar_one_or_none()
        if claim is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有已报名的打手才能上传交付附件",
            )
        if claim.status == ClaimLifecycleStatus.SETTLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该报名记录已结算，不能修改交付附件",
            )
        return order, claim

    async def _publisher_requires_escrow(self, order: Order) -> bool:
        """发布人是否为非管理员（需要从托管冻结中打款）。"""
        result = await self._db.execute(
            select(User.role).where(User.id == order.user_id)
        )
        role = result.scalar_one_or_none()
        return role is not None and role != UserRole.ADMIN

    async def _settle_booster_funds(
        self,
        order: Order,
        booster_id: int,
        *,
        payout_amount: Decimal | None = None,
        note: str | None = None,
        deduction: Decimal | None = None,
    ) -> Decimal | None:
        """结算一个打手在某订单上的全部资金流。

        1) 打手入账 ORDER_INCOME +P（幂等键 (order_id, booster_id, type)）；
        2) 炸单赔偿金：扣除 C 走 COMPENSATION_DEDUCT 不返还，剩余
           compensation_amount - C 走 DEPOSIT_RELEASE 回补打手可用余额；
        3) 非管理员发布人：ORDER_PAYMENT -P 从其托管冻结支出（幂等，
           冻结不足时尽力扣减并 warning，不阻断打手入账——老板兜底）。

        Returns:
            实际入账金额 P；该打手已结算过（幂等命中）时返回 None。
        """
        wallet_service = get_wallet_service(self._db)
        income_tx = await wallet_service.settle_order_income(
            order,
            payout_amount=payout_amount,
            note=note,
            booster_id=booster_id,
        )
        if income_tx is None:
            return None
        payout = _to_decimal(income_tx.amount)

        # 炸单赔偿金：扣除 + 剩余返还
        compensation = _to_decimal(order.compensation_amount)
        if compensation > _ZERO:
            deduct = _to_decimal(deduction)
            deduct = min(max(deduct, _ZERO), compensation)
            booster_wallet = await wallet_service.get_or_create_wallet(booster_id)
            if deduct > _ZERO:
                await wallet_service.deduct_compensation(
                    booster_wallet,
                    amount=deduct,
                    order_id=order.id,
                    booster_id=booster_id,
                    note=note,
                )
            remainder = compensation - deduct
            if remainder > _ZERO:
                await wallet_service.release_deposit(
                    booster_wallet,
                    amount=remainder,
                    order_id=order.id,
                    booster_id=booster_id,
                    note=note,
                )

        # 发布人侧：从托管冻结打款（仅非管理员发布人）
        if await self._publisher_requires_escrow(order):
            await wallet_service.pay_order_from_escrow(
                order, booster_id=booster_id, amount=payout, note=note
            )

        return payout

    async def settle_order_income(
        self,
        order: Order,
        payout_amount: Decimal | None = None,
        note: str | None = None,
        booster_id: int | None = None,
    ) -> None:
        """Settle an order's booster income when business rules allow it.

        Settlement is deliberately kept on the order service so every order
        completion path uses the same transaction and idempotent wallet logic.
        payout_amount 覆盖默认全额结算（审核部分到账时传入）。
        booster_id 指定结算的打手（名额制）；缺省沿用订单首抢打手。

        兼容路径（admin intervene 完结）：除打手入账外，同步处理炸单
        赔偿金全额返还（deduction=0）与发布人托管打款。
        """
        if booster_id is None:
            booster_id = order.booster_id
        if booster_id is None:
            return
        await self._settle_booster_funds(
            order,
            booster_id,
            payout_amount=payout_amount,
            note=note,
            deduction=_ZERO,
        )

    async def _settle_claim(
        self,
        order: Order,
        claim: OrderClaim,
        *,
        payout_amount: Decimal | None = None,
        note: str | None = None,
        deduction: Decimal | None = None,
    ) -> None:
        """Settle one claim's booster payout and mark the claim SETTLED."""
        await self._settle_booster_funds(
            order,
            claim.booster_id,
            payout_amount=payout_amount,
            note=note,
            deduction=deduction,
        )
        claim.status = ClaimLifecycleStatus.SETTLED
        claim.settled_at = datetime.now(timezone.utc)
        # autoflush=False：必须显式刷库，_auto_complete_if_done 的统计查询
        # 才能读到 SETTLED，否则订单永远停在 LOCKED。
        await self._db.flush()

    async def auto_settle_due_claim(self, order: Order, claim: OrderClaim) -> bool:
        """到账时效自动结算一个名额：全额入账、赔偿金全额返还（无扣除）。

        调用方需已锁定 order 行；返回是否执行了结算。
        """
        if claim.status != ClaimLifecycleStatus.DELIVERED:
            return False
        if order.payout_delay_days is None and order.payout_delay_hours is None:
            return False
        await self._settle_booster_funds(
            order,
            claim.booster_id,
            payout_amount=None,
            note="到账时效自动结算",
            deduction=_ZERO,
        )
        claim.status = ClaimLifecycleStatus.SETTLED
        claim.settled_at = datetime.now(timezone.utc)
        await self._db.flush()
        await self._auto_complete_if_done(order)
        return True

    async def _auto_complete_if_done(self, order: Order) -> bool:
        """Auto-complete the order once every claim is settled and the quota
        is exhausted (or claiming was closed).

        Preserves the legacy service order_count increment on the transition
        into COMPLETED. Must be called with the order row locked.
        """
        if order.status == OrderStatus.COMPLETED:
            return False

        counts_result = await self._db.execute(
            select(OrderClaim.status, func.count(OrderClaim.id))
            .where(OrderClaim.order_id == order.id)
            .group_by(OrderClaim.status)
        )
        counts = {
            status_value: int(count or 0)
            for status_value, count in counts_result.all()
        }
        total_claims = sum(counts.values())
        if total_claims == 0:
            return False
        if counts.get(ClaimLifecycleStatus.CLAIMED, 0) or counts.get(
            ClaimLifecycleStatus.DELIVERED, 0
        ):
            return False

        if order.claimed_count >= order.max_claims or order.claim_status == ClaimStatus.CLOSED:
            order.status = OrderStatus.COMPLETED
            order.completed_at = datetime.now(timezone.utc)
            if order.service_id is not None:
                await self._db.execute(
                    update(BoosterService)
                    .where(BoosterService.id == order.service_id)
                    .values(order_count=BoosterService.order_count + 1)
                )
            return True
        return False

    async def _payout_cap(self, order: Order) -> Decimal:
        """Maximum allowed payout for one claim settlement."""
        price = Decimal(str(order.price))
        if order.price_max is not None:
            price = max(price, Decimal(str(order.price_max)))
        return price

    # ------------------------------------------------------------------
    # Publisher escrow release (close / cancel / delete / refund)
    # ------------------------------------------------------------------

    async def _publisher_escrow_held(self, order: Order) -> Decimal:
        """发布人在本订单当前持有的托管金额（按钱包流水动态聚合）。

        持有 = Σ托管（-Σ ESCROW_HOLD）- Σ已退回（Σ ESCROW_RELEASE）
        - Σ已打款（-Σ ORDER_PAYMENT）；管理员发布的平台单没有托管
        流水，自然为 0。
        """
        result = await self._db.execute(
            select(
                WalletTransaction.type,
                func.coalesce(func.sum(WalletTransaction.amount), 0),
            )
            .where(
                WalletTransaction.order_id == order.id,
                WalletTransaction.type.in_((
                    WalletTransactionType.ESCROW_HOLD,
                    WalletTransactionType.ESCROW_RELEASE,
                    WalletTransactionType.ORDER_PAYMENT,
                )),
            )
            .group_by(WalletTransaction.type)
        )
        held = _ZERO
        for tx_type, total in result.all():
            total = _to_decimal(total)
            if tx_type == WalletTransactionType.ESCROW_HOLD:
                held -= total          # HOLD 为负数 → 增加持有
            elif tx_type == WalletTransactionType.ESCROW_RELEASE:
                held -= total          # RELEASE 为正数 → 减少持有
            else:                      # ORDER_PAYMENT 为负数 → 减少持有
                held += total
        return held

    async def release_escrow(
        self, order: Order, requested: Decimal, reason: str
    ) -> Decimal:
        """把发布人托管中未接单名额的资金解冻回可用余额。

        实际释放 min(requested, held)，防超释；已接单未结算名额的钱
        保留到各自结算。返回实际解冻金额。
        """
        held = await self._publisher_escrow_held(order)
        actual = min(_to_decimal(requested), held)
        if actual <= _ZERO:
            return _ZERO
        wallet_service = get_wallet_service(self._db)
        wallet = await wallet_service.get_or_create_wallet(order.user_id)
        transaction = await wallet_service.release_escrow(
            wallet,
            amount=actual,
            order_id=order.id,
            remark=f"订单 #{order.id} {reason}",
        )
        if transaction is None:
            return _ZERO
        return _to_decimal(transaction.amount)

    async def release_all_escrow(self, order: Order, reason: str) -> Decimal:
        """释放发布人在本订单当前持有的全部托管（取消/删除/退款场景）。"""
        held = await self._publisher_escrow_held(order)
        return await self.release_escrow(order, requested=held, reason=reason)

    async def review_claim(
        self,
        order_id: int,
        claim_id: int,
        reviewer: User,
        action: str = "approve",
        payout_amount: Decimal | None = None,
        note: str | None = None,
        deduction: Decimal | None = None,
    ) -> dict[str, Any]:
        """
        Admin approves one booster's delivered claim (名额审核).

        Settles the payout for that booster only; the order auto-completes
        when all claims are settled and the quota is exhausted. Returns the
        updated claim in the API contract shape.

        deduction 为炸单赔偿扣除金额（0 ~ compensation_amount，缺省 0）：
        扣除部分 COMPENSATION_DEDUCT 不返还打手，剩余部分解冻回补。
        """
        # 审核权：发单用户审核自己的单（人人可发单模式），管理员兜底
        if action != "approve":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="不支持的审核操作",
            )

        locked_result = await self._db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        order = locked_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )
        if reviewer.role != UserRole.ADMIN and order.user_id != reviewer.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有订单发布人或管理员才能审核交付记录",
            )

        claim_result = await self._db.execute(
            select(OrderClaim)
            .where(OrderClaim.id == claim_id, OrderClaim.order_id == order.id)
            .with_for_update()
        )
        claim = claim_result.scalar_one_or_none()
        if claim is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="交付记录不存在",
            )
        if claim.status != ClaimLifecycleStatus.DELIVERED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该记录不在待审核状态",
            )

        if payout_amount is not None:
            cap = await self._payout_cap(order)
            if payout_amount > cap:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"到账金额不能超过订单报酬 {cap}",
                )

        # 炸单赔偿扣除校验
        compensation = _to_decimal(order.compensation_amount)
        if deduction is not None:
            deduct = _to_decimal(deduction)
            if compensation <= _ZERO:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="该订单未设置炸单赔偿金，不能扣除",
                )
            if deduct < _ZERO or deduct > compensation:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"炸单赔偿扣除金额需在 0 ~ {compensation} 之间",
                )

        await self._settle_claim(
            order,
            claim,
            payout_amount=payout_amount,
            note=note,
            deduction=deduction,
        )
        await self._auto_complete_if_done(order)

        await self._db.flush()
        await self._db.refresh(order)
        await self._db.refresh(claim)

        logger.info(
            f"Order {order_id} claim {claim_id} reviewed (approve) by user {reviewer.id}"
        )

        return await self._claim_view_with_user(claim, order)

    async def confirm_order(
        self,
        order_id: int,
        user: User,
        payout_amount: Decimal | None = None,
        note: str | None = None,
    ) -> Order:
        """
        Boss confirms order completion (compat endpoint).

        Settles every DELIVERED claim of the order at full price (or the
        single claim with amount/note when provided), then runs the
        auto-completion check. Order-level delivery fields are no longer
        written; delivery lives on each claim.

        Args:
            order_id: int - Order ID to confirm.
            user: User confirming (must be order owner or admin).
            payout_amount: Decimal | None - 部分到账金额；缺省按订单全额结算。
            note: str | None - 打款备注，随钱包流水留存。

        Returns:
            Updated Order instance.
        """
        locked_result = await self._db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        order = locked_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )

        if order.status not in (OrderStatus.PENDING, OrderStatus.LOCKED, OrderStatus.DELIVERED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="订单当前状态不允许确认完成",
            )

        if user.role != UserRole.ADMIN and order.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有下单用户才能确认完成",
            )

        delivered_result = await self._db.execute(
            select(OrderClaim)
            .where(
                OrderClaim.order_id == order.id,
                OrderClaim.status == ClaimLifecycleStatus.DELIVERED,
            )
            .order_by(OrderClaim.id.asc())
            .with_for_update()
        )
        delivered_claims = list(delivered_result.scalars().all())

        if not delivered_claims:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该订单没有待审核的交付记录",
            )

        if payout_amount is not None:
            if len(delivered_claims) > 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="存在多个待审核记录，请逐个审核",
                )
            cap = await self._payout_cap(order)
            if payout_amount > cap:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"到账金额不能超过订单报酬 {cap}",
                )

        for claim in delivered_claims:
            await self._settle_claim(
                order, claim, payout_amount=payout_amount, note=note
            )

        await self._auto_complete_if_done(order)

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(f"Order {order_id} confirmed by user {user.id}")

        return order

    async def cancel_order(
        self,
        order_id: int,
        user: User,
    ) -> Order:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel.
            user: User cancelling the order.

        Returns:
            Updated Order instance.

        Raises:
            HTTPException: If order cannot be cancelled.
        """
        order = await self.get_order_by_id(order_id)

        # Only pending orders can be cancelled by users
        if order.status not in (OrderStatus.PENDING, OrderStatus.LOCKED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="订单状态不允许取消",
            )

        # Access control
        if user.role != UserRole.ADMIN:
            if order.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="只有订单创建者才能取消订单",
                )
            if order.status == OrderStatus.LOCKED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="订单已被接取，请联系客服处理",
                )

        order.status = OrderStatus.CANCELLED
        # 取消订单：发布人当前持有的托管全额退回（已接单未结算名额的
        # 打款在其后结算时按剩余冻结尽力扣减——老板兜底）
        released = await self.release_all_escrow(order, reason="订单取消，托管解冻")
        if released > _ZERO:
            logger.info(
                "Order %s cancelled, released escrow %s back to publisher %s",
                order.id, released, order.user_id,
            )

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(f"Order {order_id} cancelled by user {user.id}")

        return order

    async def dispute_order(
        self,
        order_id: int,
        user: User,
        reason: str | None = None,
    ) -> Order:
        """
        Mark an order as disputed.

        Args:
            order_id: Order ID to dispute.
            user: User raising the dispute.
            reason: Optional dispute reason.

        Returns:
            Updated Order instance.

        Raises:
            HTTPException: If order cannot be disputed.
        """
        order = await self.get_order_by_id(order_id)

        if order.status not in (OrderStatus.LOCKED, OrderStatus.DELIVERED, OrderStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有进行中、待确认或已完成的订单才能发起争议",
            )

        # Only order owner, booster, or admin can dispute
        if user.role != UserRole.ADMIN and order.user_id != user.id and order.booster_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权对此订单发起争议",
            )

        order.status = OrderStatus.DISPUTED
        if reason:
            order.notes = f"争议原因: {reason}" + (f"\n{order.notes}" if order.notes else "")

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(f"Order {order_id} disputed by user {user.id}")

        return order

    async def update_order(
        self,
        order_id: int,
        order_data: OrderUpdate,
        user: User,
    ) -> Order:
        """
        Update an order.

        Args:
            order_id: Order ID to update.
            order_data: Update data.
            user: User making the update.

        Returns:
            Updated Order instance.

        Raises:
            HTTPException: If order cannot be updated.
        """
        order = await self.get_order_by_id(order_id)

        # Only pending orders can be updated
        if order.status != OrderStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有待接单的订单才能修改",
            )

        # Access control
        if user.role != UserRole.ADMIN and order.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有订单创建者才能修改订单",
            )

        # Update fields
        update_data = order_data.model_dump(exclude_unset=True)
        if "game_password" in update_data:
            update_data["game_password"] = encrypt_text(update_data["game_password"])
        if "description" in update_data and "intro" not in update_data:
            update_data["intro"] = update_data["description"]

        game: Game | None = None
        if "game_id" in update_data or "game_name" in update_data:
            game = await self._resolve_game(
                game_id=update_data.get("game_id", order.game_id),
                game_name=update_data.get("game_name", order.game_name),
            )
            if game is not None:
                update_data["game_id"] = game.id
                update_data["game_name"] = game.name

        rebuild_ai_tags = any(
            key in update_data
            for key in ("game_id", "game_name", "current_rank", "target_rank", "service_type", "server", "ai_tags")
        )
        if rebuild_ai_tags:
            current_rank = update_data.get("current_rank", order.current_rank)
            target_rank = update_data.get("target_rank", order.target_rank)
            service_type = update_data.get("service_type", order.service_type)
            server = update_data.get("server", order.server)
            ai_tags_input = update_data.get("ai_tags", order.ai_tags)
            if game is None and (order.game_id is not None or order.game_name):
                game = await self._resolve_game(
                    game_id=order.game_id,
                    game_name=order.game_name,
                )
            if game is not None and service_type:
                self._validate_service_type(game, service_type)
            update_data["ai_tags"] = self._build_ai_tags(
                existing=ai_tags_input,
                game=game,
                server=server,
                service_type=service_type,
                current_rank=current_rank,
                target_rank=target_rank,
                role=self._extract_ai_detail_value(ai_tags_input, "role"),
                requirements=self._extract_ai_detail_requirements(ai_tags_input),
            )

        for field, value in update_data.items():
            if hasattr(order, field):
                setattr(order, field, value)

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(f"Order {order_id} updated by user {user.id}")

        return order

    async def claim_control(self, order_id: int, action: str, admin: User) -> Order:
        if admin.role != UserRole.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有管理员才能操作抢单控制")
        result = await self._db.execute(select(Order).where(Order.id == order_id).with_for_update())
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        actions = {"pause": ClaimStatus.PAUSED, "resume": ClaimStatus.OPEN, "close": ClaimStatus.CLOSED}
        if action not in actions and action != "archive":
            raise HTTPException(status_code=400, detail="不支持的抢单操作")
        if action == "archive": order.is_archived = True
        else:
            if action == "resume" and order.claimed_count >= order.max_claims:
                order.claim_status = ClaimStatus.FULL
            else: order.claim_status = actions[action]
        # 截止/归档后未接单名额不再可被领取：把这部分托管解冻回发布人
        if action in ("close", "archive"):
            unclaimed = max(int(order.max_claims) - int(order.claimed_count), 0)
            requested = _to_decimal(order.price) * unclaimed
            if requested > _ZERO:
                released = await self.release_escrow(
                    order, requested=requested, reason="抢单截止，未接单名额托管解冻"
                )
                if released > _ZERO:
                    logger.info(
                        "Order %s %s released escrow %s back to publisher %s",
                        order.id, action, released, order.user_id,
                    )
        await self._db.flush(); await self._db.refresh(order)
        return order

    async def delete_order(self, order_id: int, admin: User) -> None:
        if admin.role != UserRole.ADMIN:
            raise HTTPException(status_code=403, detail="只有管理员才能删除订单")
        result = await self._db.execute(select(Order).where(Order.id == order_id).with_for_update())
        order = result.scalar_one_or_none()
        if order is None: raise HTTPException(status_code=404, detail="订单不存在")
        if order.claimed_count or order.booster_id or order.payment_status != PaymentStatus.UNPAID or order.status != OrderStatus.PENDING:
            raise HTTPException(status_code=409, detail="订单已有业务记录，不能物理删除")
        # 删除前把发布人托管全额退回（钱包流水 order_id 随外键置空）
        await self.release_all_escrow(order, reason="订单删除，托管解冻")
        await self._db.delete(order); await self._db.flush()

    async def pay_order(self, order_id: int, user: User) -> Order:
        """Simulate payment: UNPAID -> PAID. Locks the order row to prevent
        double-payment races where two concurrent requests both read UNPAID."""
        # Existence + access check (raises if user cannot view this order)
        await self.get_order_by_id(order_id, user)

        # Lock the order row so concurrent /pay calls serialize.
        locked_result = await self._db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        order = locked_result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在",
            )

        if order.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有下单用户才能支付",
            )

        # Block payment on terminal / closed states. Without this check a
        # user could pay a CANCELLED or DISPUTED order, locking funds that
        # the normal refund flow no longer covers cleanly.
        if order.status in (OrderStatus.CANCELLED, OrderStatus.DISPUTED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"订单当前状态为 {order.status.value}，无法支付",
            )

        if order.payment_status != PaymentStatus.UNPAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前支付状态为 {order.payment_status.value}，无法支付",
            )

        order.payment_status = PaymentStatus.PAID
        order.paid_at = datetime.now(timezone.utc)
        await self._db.flush()
        await self._db.refresh(order)
        return order

    async def refund_order(self, order_id: int, admin: User) -> Order:
        """Admin refund: PAID -> REFUNDED. Order must be CANCELLED or DISPUTED."""
        if admin.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有管理员才能退款",
            )

        order = await self.get_order_by_id(order_id, admin)

        if order.payment_status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有已支付的订单才能退款",
            )

        if order.status not in (OrderStatus.CANCELLED, OrderStatus.DISPUTED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有已取消或争议中的订单才能退款",
            )

        order.payment_status = PaymentStatus.REFUNDED
        # 退款路径：发布人当前持有的托管一并退回
        released = await self.release_all_escrow(order, reason="订单退款，托管解冻")
        if released > _ZERO:
            logger.info(
                "Order %s refunded, released escrow %s back to publisher %s",
                order.id, released, order.user_id,
            )
        await self._db.flush()
        await self._db.refresh(order)
        return order

    async def _resolve_game(
        self,
        game_id: int | None = None,
        game_name: str | None = None,
        text: str | None = None,
    ) -> Game | None:
        if game_id is not None:
            result = await self._db.execute(
                select(Game).where(Game.id == game_id)
            )
            game = result.scalar_one_or_none()
            if game is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="游戏不存在",
                )
            return game

        if game_name:
            result = await self._db.execute(
                select(Game)
                .where(
                    or_(
                        Game.name == game_name,
                        Game.english_name == game_name,
                        Game.name.like(f"%{escape_like(game_name)}%"),
                        Game.english_name.like(f"%{escape_like(game_name)}%"),
                    )
                )
                .order_by(func.length(Game.name).desc(), Game.id.asc())
                .limit(1)
            )
            game = result.scalar_one_or_none()
            if game is not None:
                return game

        if text:
            result = await self._db.execute(
                select(Game).where(Game.is_active.is_(True)).order_by(func.length(Game.name).desc(), Game.id.asc())
            )
            games = list(result.scalars().all())
            lowered_text = text.lower()
            for game in games:
                english_name = (game.english_name or "").lower()
                if game.name in text or (english_name and english_name in lowered_text):
                    return game

        return None

    def _validate_service_type(self, game: Game, service_type: str) -> None:
        service_types = game.service_template.get("service_types", [])
        if service_types and service_type not in service_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="服务类型不属于该游戏模板",
            )

    def _infer_service_type(
        self,
        description: str,
        game: Game | None,
        extracted_service_type: str | None,
    ) -> str | None:
        if game is None:
            return extracted_service_type

        service_types = game.service_template.get("service_types", [])
        if extracted_service_type and extracted_service_type in service_types:
            return extracted_service_type

        for service_type in service_types:
            if service_type in description:
                return service_type

        return service_types[0] if service_types else extracted_service_type

    def _normalize_server_for_game(
        self,
        server: str | None,
        game: Game | None,
    ) -> str | None:
        if server is None or game is None:
            return server

        servers = game.service_template.get("servers", [])
        if not servers:
            return server

        for candidate in servers:
            if server == candidate or server in candidate or candidate in server:
                return candidate

        return server

    def _build_ai_tags(
        self,
        existing: dict[str, Any] | None,
        game: Game | None,
        server: str | None,
        service_type: str | None,
        current_rank: str | None,
        target_rank: str | None,
        role: str | None,
        requirements: list[str],
    ) -> dict[str, Any] | None:
        if (
            existing is None
            and game is None
            and server is None
            and service_type is None
            and current_rank is None
            and target_rank is None
            and role is None
            and not requirements
        ):
            return None

        ai_tags: dict[str, Any] = dict(existing or {})
        if game is not None:
            ai_tags["game_id"] = game.id
        elif "game_id" not in ai_tags:
            ai_tags["game_id"] = None

        if server is not None:
            ai_tags["server"] = server
        if service_type is not None:
            ai_tags["service_type"] = service_type

        detail = dict(ai_tags.get("detail") or {})
        if current_rank is not None:
            detail["current_rank"] = current_rank
        if target_rank is not None:
            detail["target_rank"] = target_rank
        if role is not None:
            detail["role"] = role
        detail["requirements"] = requirements
        ai_tags["detail"] = detail
        return ai_tags

    @staticmethod
    def _extract_ai_root_value(ai_tags: dict[str, Any] | None, key: str) -> str | None:
        if not ai_tags:
            return None
        value = ai_tags.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_ai_detail_value(ai_tags: dict[str, Any] | None, key: str) -> str | None:
        if not ai_tags:
            return None
        detail = ai_tags.get("detail")
        if not isinstance(detail, dict):
            return None
        value = detail.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _extract_ai_detail_requirements(self, ai_tags: dict[str, Any] | None) -> list[str]:
        if not ai_tags:
            return []
        detail = ai_tags.get("detail")
        if not isinstance(detail, dict):
            return []
        return self._normalize_requirements(detail.get("requirements"))

    @staticmethod
    def _normalize_requirements(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if isinstance(value, list):
            requirements: list[str] = []
            for item in value:
                cleaned = str(item).strip()
                if cleaned:
                    requirements.append(cleaned)
            return requirements
        cleaned = str(value).strip()
        return [cleaned] if cleaned else []


def get_order_service(db: AsyncSession) -> OrderService:
    """
    Factory function to create OrderService instance.

    Args:
        db: Async database session.

    Returns:
        OrderService instance.
    """
    return OrderService(db)
