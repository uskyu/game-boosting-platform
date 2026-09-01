"""
Seed script: generates test boosters, services, orders, and reviews.
Run from backend directory:  python -m scripts.seed_data
"""

import asyncio
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.booster_service import BoosterService
from app.models.game import Game
from app.models.order import Order, OrderStatus, PaymentStatus
from app.models.review import Review
from app.models.user import BoosterApplicationStatus, User, UserRole
from app.services.credit_service import get_credit_service

# ---------------------------------------------------------------------------
# Booster profiles – each with personality and speciality
# ---------------------------------------------------------------------------
BOOSTER_PROFILES = [
    {"username": "荣耀战神", "email": "booster01@test.com", "bio": "王者荣耀五年代练，巅峰赛全国前100", "games": ["王者荣耀"], "rank": "荣耀王者"},
    {"username": "峡谷闪电", "email": "booster02@test.com", "bio": "专注王者荣耀打野，胜率92%", "games": ["王者荣耀"], "rank": "星耀"},
    {"username": "LOL大师兄", "email": "booster03@test.com", "bio": "英雄联盟电一钻石代练，主打中单", "games": ["英雄联盟"], "rank": "大师"},
    {"username": "峡谷猎人", "email": "booster04@test.com", "bio": "LOL+王者双修，擅长AD和射手", "games": ["英雄联盟", "王者荣耀"], "rank": "钻石"},
    {"username": "刀塔老司机", "email": "booster05@test.com", "bio": "DOTA2 5500分，冠绝天涯", "games": ["DOTA2"], "rank": "冠绝天涯"},
    {"username": "无畏先锋", "email": "booster06@test.com", "bio": "VALORANT白金代练，狙击手", "games": ["无畏契约 (VALORANT)"], "rank": "白金"},
    {"username": "手游小王子", "email": "booster07@test.com", "bio": "英雄联盟手游、王者荣耀双排代练", "games": ["英雄联盟手游", "王者荣耀"], "rank": "王者"},
    {"username": "原神代肝哥", "email": "booster08@test.com", "bio": "原神深渊满星清理，每日委托代肝", "games": ["原神"], "rank": "深渊满星"},
    {"username": "铁道快车", "email": "booster09@test.com", "bio": "崩铁混沌回忆满星，角色养成规划", "games": ["崩坏：星穹铁道"], "rank": "混沌10层"},
    {"username": "和平精英ACE", "email": "booster10@test.com", "bio": "和平精英ACE段位，吃鸡率35%", "games": ["和平精英"], "rank": "ACE"},
    {"username": "CS狂飙", "email": "booster11@test.com", "bio": "CS2全球精英，4000小时老兵", "games": ["CS2"], "rank": "全球精英"},
    {"username": "曙光勇者", "email": "booster12@test.com", "bio": "曙光英雄王者段代练，擅长法师", "games": ["曙光英雄"], "rank": "王者"},
    {"username": "决战阴阳师", "email": "booster13@test.com", "bio": "阴阳师斗技2200+，御魂效率代刷", "games": ["阴阳师"], "rank": "鬼王"},
    {"username": "金铲铲棋圣", "email": "booster14@test.com", "bio": "金铲铲之战宗师段，自走棋老玩家", "games": ["金铲铲之战"], "rank": "宗师"},
    {"username": "鸣潮探索者", "email": "booster15@test.com", "bio": "鸣潮全图探索100%，深渊满星", "games": ["鸣潮"], "rank": "满星"},
    {"username": "火线冲锋", "email": "booster16@test.com", "bio": "穿越火线十年老兵，枪法精准", "games": ["穿越火线"], "rank": "元帅"},
    {"username": "飞车达人", "email": "booster17@test.com", "bio": "QQ飞车手游车神段位，赛道熟练", "games": ["QQ飞车手游"], "rank": "车神"},
    {"username": "梦幻西游大佬", "email": "booster18@test.com", "bio": "梦幻西游全服前50，高端副本带队", "games": ["梦幻西游"], "rank": "175级"},
    {"username": "三角洲特工", "email": "booster19@test.com", "bio": "三角洲行动白金代练，战术执行力强", "games": ["三角洲行动"], "rank": "白金"},
    {"username": "暗区猎手", "email": "booster20@test.com", "bio": "暗区突围高端局，生还率60%", "games": ["暗区突围"], "rank": "传奇"},
    {"username": "绝区零酱", "email": "booster21@test.com", "bio": "绝区零深渊满星、委托代肝高效", "games": ["绝区零"], "rank": "满星"},
    {"username": "逆水寒剑客", "email": "booster22@test.com", "bio": "逆水寒PVP高端玩家，擅长竞技场", "games": ["逆水寒"], "rank": "宗师"},
    {"username": "燕云侠士", "email": "booster23@test.com", "bio": "燕云十六声PVE PVP双修", "games": ["燕云十六声"], "rank": "大师"},
    {"username": "全能代练王", "email": "booster24@test.com", "bio": "多游戏代练5年经验，好评如潮", "games": ["王者荣耀", "英雄联盟", "和平精英"], "rank": "各游戏高段位"},
    {"username": "极速车手", "email": "booster25@test.com", "bio": "巅峰极速和极品飞车双修", "games": ["巅峰极速"], "rank": "传奇"},
]

# Test customers
CUSTOMER_PROFILES = [
    {"username": "小明爱打游戏", "email": "customer01@test.com"},
    {"username": "游戏少女Luna", "email": "customer02@test.com"},
    {"username": "上班摸鱼王", "email": "customer03@test.com"},
    {"username": "佛系玩家", "email": "customer04@test.com"},
    {"username": "氪金小能手", "email": "customer05@test.com"},
    {"username": "大学生小张", "email": "customer06@test.com"},
    {"username": "夜猫子选手", "email": "customer07@test.com"},
    {"username": "只想上分", "email": "customer08@test.com"},
    {"username": "咸鱼翻身中", "email": "customer09@test.com"},
    {"username": "游戏萌新", "email": "customer10@test.com"},
]

SERVICE_TYPES = ["代练", "陪玩", "教学"]

REVIEW_CONTENTS_POSITIVE = [
    "上分很快，非常靠谱！",
    "效率很高，超出预期",
    "态度很好，随时沟通",
    "技术很强，看着直播学到不少",
    "准时完成，账号安全",
    "性价比超高，下次还来",
    "老板很好说话，合作愉快",
    "代练过程很顺利，推荐",
    "真的强，一天就搞定了",
    "服务态度满分，结果也满意",
]

REVIEW_CONTENTS_NEUTRAL = [
    "还行吧，能用",
    "一般般，不过完成了",
    "速度还可以，就是沟通少了点",
]

REVIEW_CONTENTS_NEGATIVE = [
    "速度有点慢",
    "沟通不太及时",
]

PASSWORD = hash_password("Test123456")


async def seed() -> None:
    async with async_session_factory() as db:
        # Check if already seeded
        existing = await db.execute(
            select(User).where(User.email == "booster01@test.com")
        )
        if existing.scalar_one_or_none() is not None:
            print("Seed data already exists, skipping.")
            return

        # Load games
        games_result = await db.execute(select(Game).where(Game.is_active.is_(True)))
        games = list(games_result.scalars().all())
        game_by_name = {g.name: g for g in games}

        if not games:
            print("No games found. Please ensure games are seeded first.")
            return

        print(f"Found {len(games)} games")

        # Create booster users
        boosters: list[User] = []
        for profile in BOOSTER_PROFILES:
            user = User(
                email=profile["email"],
                hashed_password=PASSWORD,
                username=profile["username"],
                role=UserRole.BOOSTER,
                is_active=True,
                is_verified=True,
                bio=profile["bio"],
                booster_application_status=BoosterApplicationStatus.APPROVED,
                booster_application_game=profile["games"][0],
                booster_application_current_rank=profile["rank"],
                booster_application_target_rank=profile["rank"],
                booster_quota=random.randint(5, 20),
                reviewed_by_admin_id=1,
                reviewed_at=datetime.now(timezone.utc) - timedelta(days=random.randint(10, 90)),
            )
            db.add(user)
            boosters.append(user)

        # Create customer users
        customers: list[User] = []
        for profile in CUSTOMER_PROFILES:
            user = User(
                email=profile["email"],
                hashed_password=PASSWORD,
                username=profile["username"],
                role=UserRole.USER,
                is_active=True,
                is_verified=True,
            )
            db.add(user)
            customers.append(user)

        await db.flush()
        print(f"Created {len(boosters)} boosters and {len(customers)} customers")

        # Create services for each booster
        all_services: list[BoosterService] = []
        for booster in boosters:
            profile = next(p for p in BOOSTER_PROFILES if p["email"] == booster.email)
            for game_name in profile["games"]:
                game = game_by_name.get(game_name)
                if game is None:
                    continue

                for stype in random.sample(SERVICE_TYPES, k=random.randint(1, 2)):
                    service = BoosterService(
                        booster_id=booster.id,
                        game_id=game.id,
                        title=f"{game.name} {stype} - {booster.username}",
                        description=f"{booster.bio}，提供专业{stype}服务",
                        service_type=stype,
                        price_per_hour=Decimal(str(random.choice([30, 40, 50, 60, 80, 100]))),
                        tags=[stype, game.name, profile["rank"]],
                        is_available=True,
                        order_count=0,
                    )
                    db.add(service)
                    all_services.append(service)

        await db.flush()
        print(f"Created {len(all_services)} services")

        # Create orders with various statuses
        all_orders: list[Order] = []
        now = datetime.now(timezone.utc)

        for _ in range(80):
            customer = random.choice(customers)
            service = random.choice(all_services)
            booster_user = next(b for b in boosters if b.id == service.booster_id)
            game = game_by_name.get(service.title.split(" ")[0]) or random.choice(games)

            created_at = now - timedelta(
                days=random.randint(1, 60),
                hours=random.randint(0, 23),
            )

            # Weighted status distribution
            status_roll = random.random()
            if status_roll < 0.15:
                order_status = OrderStatus.PENDING
            elif status_roll < 0.25:
                order_status = OrderStatus.LOCKED
            elif status_roll < 0.35:
                order_status = OrderStatus.DELIVERED
            elif status_roll < 0.85:
                order_status = OrderStatus.COMPLETED
            elif status_roll < 0.93:
                order_status = OrderStatus.DISPUTED
            else:
                order_status = OrderStatus.CANCELLED

            locked_at = None
            delivered_at = None
            completed_at = None
            booster_id = None
            payment_status = PaymentStatus.UNPAID
            paid_at = None

            if order_status != OrderStatus.PENDING:
                booster_id = booster_user.id
                locked_at = created_at + timedelta(hours=random.randint(1, 12))

            if order_status in (OrderStatus.DELIVERED, OrderStatus.COMPLETED):
                delivered_at = locked_at + timedelta(hours=random.randint(2, 48)) if locked_at else None

            if order_status == OrderStatus.COMPLETED:
                completed_at = (delivered_at or locked_at) + timedelta(hours=random.randint(1, 72)) if (delivered_at or locked_at) else None

            # Most completed/delivered orders are paid
            if order_status in (OrderStatus.LOCKED, OrderStatus.DELIVERED, OrderStatus.COMPLETED) and random.random() < 0.85:
                    payment_status = PaymentStatus.PAID
                    paid_at = locked_at

            if order_status == OrderStatus.CANCELLED and random.random() < 0.3:
                payment_status = PaymentStatus.REFUNDED

            ranks = ["青铜", "白银", "黄金", "铂金", "钻石", "星耀", "王者"]
            current_idx = random.randint(0, 4)
            target_idx = random.randint(current_idx + 1, 6)

            price = Decimal(str(random.choice([50, 80, 100, 150, 200, 300, 500])))

            order = Order(
                user_id=customer.id,
                booster_id=booster_id,
                game_id=game.id,
                service_id=service.id,
                game_name=game.name,
                current_rank=ranks[current_idx],
                target_rank=ranks[target_idx],
                price=price,
                status=order_status,
                service_type=service.service_type,
                description_raw=f"从{ranks[current_idx]}上{ranks[target_idx]}，麻烦快一点",
                created_at=created_at,
                locked_at=locked_at,
                delivered_at=delivered_at,
                completed_at=completed_at,
                payment_status=payment_status,
                paid_at=paid_at,
            )
            db.add(order)
            all_orders.append(order)

        await db.flush()
        print(f"Created {len(all_orders)} orders")

        # Update service order_count
        for service in all_services:
            count = sum(
                1 for o in all_orders
                if o.service_id == service.id and o.status == OrderStatus.COMPLETED
            )
            service.order_count = count

        await db.flush()

        # Create reviews for completed orders
        review_count = 0
        completed_orders = [o for o in all_orders if o.status == OrderStatus.COMPLETED]

        for order in completed_orders:
            # Customer reviews booster
            if random.random() < 0.8:
                rating = random.choices([5, 4, 3, 2, 1], weights=[50, 25, 15, 7, 3])[0]
                if rating >= 4:
                    content = random.choice(REVIEW_CONTENTS_POSITIVE)
                elif rating == 3:
                    content = random.choice(REVIEW_CONTENTS_NEUTRAL)
                else:
                    content = random.choice(REVIEW_CONTENTS_NEGATIVE)

                review = Review(
                    order_id=order.id,
                    reviewer_id=order.user_id,
                    target_id=order.booster_id,
                    rating=rating,
                    content=content,
                    created_at=order.completed_at + timedelta(hours=random.randint(1, 24)) if order.completed_at else now,
                )
                db.add(review)
                review_count += 1

            # Booster reviews customer (less frequent)
            if random.random() < 0.4 and order.booster_id:
                rating = random.choices([5, 4, 3], weights=[60, 30, 10])[0]
                review = Review(
                    order_id=order.id,
                    reviewer_id=order.booster_id,
                    target_id=order.user_id,
                    rating=rating,
                    content=random.choice(REVIEW_CONTENTS_POSITIVE[:4]),
                    created_at=order.completed_at + timedelta(hours=random.randint(1, 48)) if order.completed_at else now,
                )
                db.add(review)
                review_count += 1

        await db.flush()
        await db.commit()
        print(f"Created {review_count} reviews")

        # Recalculate credit for all boosters
        credit_service = get_credit_service(db)
        recalc_count = await credit_service.recalculate_all()
        await db.commit()
        print(f"Recalculated credit for {recalc_count} boosters")
        print("Seed complete!")


if __name__ == "__main__":
    asyncio.run(seed())
