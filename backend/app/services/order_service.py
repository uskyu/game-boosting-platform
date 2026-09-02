"""
Order service module.
Business logic for order management operations.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import encrypt_text, escape_like
from app.models.booster_service import BoosterService
from app.models.game import Game
from app.models.order import Order, OrderClaim, OrderStatus, PaymentStatus, ClaimStatus
from app.models.user import User, UserRole
from app.schemas.booster_service import BoosterServiceOrderCreate
from app.schemas.order import OrderCreate, OrderUpdate
from app.services.ai_service import LLMService
from app.services.wallet_service import get_wallet_service

logger = logging.getLogger(__name__)


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
            HTTPException: If user is not allowed to create orders.

        Notes:
            - ADMIN（老板）发布订单：user_id=管理员自己 id、status=PENDING、
              booster_id=None，订单进入公共大厅供非管理员注册用户抢单。
            - USER/BOOSTER 均作为接单方使用，不能发单。
        """
        if user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有管理员可以发布订单，普通用户和打手不能发单",
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
            status=OrderStatus.PENDING,
        )

        self._db.add(order)
        await self._db.flush()
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
            locked_at=datetime.now(timezone.utc),
        )

        self._db.add(order)
        await self._db.flush()
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
            can_view = (
                order.user_id == user.id
                or order.booster_id == user.id
                or (
                    order.status == OrderStatus.PENDING
                    and order.claim_status == ClaimStatus.OPEN
                    and not order.is_archived
                )
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
    ) -> tuple[list[Order], int]:
        """
        List orders with filtering and pagination.

        Args:
            user: Optional user for filtering (customers see own, boosters see available).
            game_name: Optional game name filter.
            status_filter: Optional status filter.
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
        if user is not None:
            if user.role != UserRole.ADMIN:
                # Every non-admin account can act as a booster. Keep own orders
                # visible while adding the public claimable hall.
                now = datetime.now(timezone.utc)
                claimable = (
                    (Order.status == OrderStatus.PENDING)
                    & (Order.claim_status == ClaimStatus.OPEN)
                    & (Order.is_archived.is_(False))
                    & (or_(Order.deadline.is_(None), Order.deadline > now))
                    & (Order.claimed_count < Order.max_claims)
                )
                booster_scope = claimable | (Order.user_id == user.id) | (Order.booster_id == user.id)
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
        order.claimed_count += 1
        if order.booster_id is None:
            order.booster_id = booster.id
            order.status = OrderStatus.LOCKED
            order.locked_at = now
        if order.claimed_count >= order.max_claims:
            order.claim_status = ClaimStatus.FULL

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

    async def list_order_claims(self, order_id: int) -> list[dict[str, Any]]:
        """
        List the booster claim (报名) records of an order, oldest first.

        Args:
            order_id: Order ID whose claim list should be returned.

        Returns:
            List of claim dicts enriched with the booster's username/email
            and an ``is_first`` flag marking the claim whose booster matches
            the order's current booster (i.e. the first successful grab).

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
                {
                    "id": claim.id,
                    "order_id": claim.order_id,
                    "booster_id": claim.booster_id,
                    "booster_nickname": username,
                    "booster_email": email,
                    "created_at": claim.created_at,
                    "is_first": order_booster_id == claim.booster_id,
                }
            )
        return claims

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

        active_orders_count_result = await self._db.execute(
            select(func.count(Order.id)).where(
                Order.booster_id == locked_booster.id,
                Order.status == OrderStatus.LOCKED,
            )
        )
        active_orders_count = int(active_orders_count_result.scalar() or 0)
        if locked_booster.booster_quota <= active_orders_count:
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
    ) -> Order:
        """
        Booster ends the order with an optional report note,
        awaiting the boss's confirmation.

        Only the assigned booster may call this. Admin-driven state changes
        must go through /admin/orders/{id}/intervene.
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

        if order.status != OrderStatus.LOCKED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有进行中的订单才能结束",
            )

        if user.role == UserRole.ADMIN or order.booster_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有接单打手才能结束订单",
            )

        order.status = OrderStatus.DELIVERED
        order.delivered_at = datetime.now(timezone.utc)
        if delivery_note is not None:
            order.delivery_note = delivery_note.strip() or None

        await self._db.flush()
        await self._db.refresh(order)

        logger.info(f"Order {order_id} delivered by user {user.id}")

        return order

    async def settle_order_income(
        self,
        order: Order,
        payout_amount: Decimal | None = None,
        note: str | None = None,
    ) -> None:
        """Settle an order's booster income when business rules allow it.

        Settlement is deliberately kept on the order service so every order
        completion path uses the same transaction and idempotent wallet logic.
        Existing completion rules permit settlement without requiring a paid
        status, while orders without an assigned booster are a safe no-op.
        payout_amount 覆盖默认全额结算（审核部分到账时传入）。
        """
        if order.booster_id is None:
            return
        wallet_service = get_wallet_service(self._db)
        await wallet_service.settle_order_income(order, payout_amount=payout_amount, note=note)

    async def confirm_order(
        self,
        order_id: int,
        user: User,
        payout_amount: Decimal | None = None,
        note: str | None = None,
    ) -> Order:
        """
        Boss confirms order completion. Increments service order_count.

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

        if order.status != OrderStatus.DELIVERED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有待确认的订单才能确认完成",
            )

        if user.role != UserRole.ADMIN and order.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有下单用户才能确认完成",
            )

        if payout_amount is not None:
            # 区间价订单按最高价作为上限
            price = Decimal(str(order.price))
            if order.price_max is not None:
                price = max(price, Decimal(str(order.price_max)))
            if payout_amount > price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"到账金额不能超过订单报酬 {price}",
                )

        order.status = OrderStatus.COMPLETED
        order.completed_at = datetime.now(timezone.utc)

        if order.service_id is not None:
            await self._db.execute(
                update(BoosterService)
                .where(BoosterService.id == order.service_id)
                .values(order_count=BoosterService.order_count + 1)
            )

        await self._db.flush()

        # Settle booster income in the same transaction (DELIVERED -> COMPLETED).
        # Idempotent via the (order_id, ORDER_INCOME) unique constraint, so a
        # duplicate settle call is a no-op instead of double-crediting.
        await self.settle_order_income(order, payout_amount=payout_amount, note=note)

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
