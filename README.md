# Game Boosting Platform · 游戏派单平台

> Multi-slot order dispatch, first-come-first-served claiming, publisher self-review + settlement, wallet & withdrawals, dispute handling, real-time chat.

Game Boosting Platform is a self-hosted **game boosting order dispatch system** for solo operators and small teams: publishers post orders with escrowed funds, boosters claim slots first-come-first-served, deliver the work, get reviewed, and settle earnings into their wallet — then withdraw, with every cent traceable in an immutable ledger.

游戏派单中心：老板发单并托管资金，打手抢单交付，老板审核后自动结算到钱包，打手提现，管理员逐笔人工打款——每一分钱都有流水可查。

## Keywords / SEO

`game boosting` · `order dispatch` · 派单 · 代练 · `dantai` / 陪玩 · `Delta Force` · `claim & settle` · `wallet ledger` · escrow · withdrawals · dispute resolution

## Core Flow（核心流程）

```
publish ──► escrow (price × slots frozen from publisher wallet)
                │
                ▼
        claim (first-come-first-served, row lock + quota control)
                │
                ▼
        deliver (claim: CLAIMED ──► DELIVERED)
                │
                ▼
        review (publisher approves per claim, or payout-delay auto-settles)
                │
                ▼
        settle (claim: DELIVERED ──► SETTLED, earnings credited in same transaction)
                │
                ▼
        withdraw (booster requests → amount frozen → admin processes each case)
                │
                ▼
        manual payout (offline transfer → admin marks paid with transaction no.;
                       reject → auto-unfreeze back to available balance)
```

Order-level states: `PENDING → LOCKED → DELIVERED → COMPLETED / DISPUTED / CANCELLED`.

## Features（功能列表）

### Dispatch & Claiming（派单与抢单）

- Publishers create orders with **multiple slots** (`max_claims`); boosters **claim** slots first-come-first-served, with DB row locks + claim-quota concurrency control — the same slot is never double-claimed
- Full **escrow on publish**: `price × slots` is frozen from the publisher's wallet before the order enters the hall; insufficient balance rejects the order
- Admins can **assign** an order directly to a specific booster (role / status / quota validated)
- Per-claim lifecycle: **CLAIMED → DELIVERED → SETTLED**, tracked independently per slot
- **Dispute + admin intervene**: disputed orders / claims can be acted on by admins (cancel, mark delivered, complete)

### Settlement（结算）

- On review approval, the claim settles **in the same transaction**: booster balance += order income
- **No commission**: `COMMISSION_RATE = 0.0` — boosters receive the full order price
- Idempotent settlement via `(order_id, type)` unique constraint + SAVEPOINT — repeated confirms never double-credit, even concurrently
- All amounts computed with `Decimal`; every balance change writes an **immutable ledger entry** (before/after snapshots, operator, memo)

### Payout Delay（灵活到账时效）

- Each order can set a payout delay: **days part** (`payout_delay_days`, 0–30) + **hours part** (`payout_delay_hours`, 0–23); both null = no delay
- After delivery, a background scheduler **auto-settles** claims once the delay elapses

### Wallet & Withdrawals（钱包与提现）

- Per-user wallet: **available balance / frozen amount / total earned / total withdrawn**
- Withdrawal request (Alipay / WeChat / bank card) moves the amount from available to frozen immediately
- Admin processes each case: **approve → offline payout → mark paid (with payout transaction no.)**, or **reject (auto-unfreeze)**
- Paid records are permanently queryable (payee, account, transaction no., timestamps)

### Platform（平台能力）

- **Captcha on register** (`captcha_service`) to block bots
- JWT dual-token auth + auto refresh; three roles (user / booster / admin)
- **Real-time chat** (WebSocket) between order parties, plus support chat
- **Notifications**: in-site notifications for claims, deliveries, settlements, withdrawals
- **Admin dashboard**: user / order / revenue trends, game distribution, booster ranking, booster qualification review (with proof-image upload + quota setting), order intervention, withdrawal processing, manual wallet adjustments (top-up / deduct), game management
- Mutual reviews with credit scoring; optional AI requirement parsing (DeepSeek API)

## Tech Stack（技术栈）

| Layer | Tech |
|---|---|
| Backend | Python 3.10+ · FastAPI · SQLAlchemy 2 (async) · Alembic · aiomysql |
| Frontend | Vue 3 · Vite 5 · Pinia · Vue Router · Tailwind CSS · ECharts |
| Database | MySQL 8 (utf8mb4) |
| Deploy | Docker Compose (MySQL + backend + frontend in one shot) |

## Quick Start（快速开始）

### Docker Compose (recommended)

```bash
cp backend/.env.example backend/.env   # edit: DB, SECRET_KEY, admin password
docker compose up -d --build           # first boot runs DB migrations automatically
```

| Service | URL |
|---|---|
| Frontend | http://localhost |
| Backend API | http://localhost:8000 |
| Swagger docs | http://localhost:8000/api/v1/docs |
| Health check | http://localhost:8000/health |

### Local dev

```bash
# Backend (Python 3.10–3.12)
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
# Linux/macOS: python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# Frontend
cd frontend
npm ci
npm run dev            # http://localhost:3000, /api proxied to 8000
```

### Env vars (backend/.env)（环境变量）

| Variable | Description |
|---|---|
| `DB_URL` | `mysql+aiomysql://user:pass@host:3306/dbname` |
| `SECRET_KEY` | JWT signing key — replace with a random value |
| `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` | Admin account auto-created on first boot (password must be ≥ 8 chars) |
| `COMMISSION_RATE` | Platform commission ratio, 0–1; default `0.0` (full amount settles to booster, i.e. no commission) |
| `DEEPSEEK_API_KEY` | Optional, for AI requirement parsing; placeholder value is fine in test env |

## Fund Safety（资金安全设计）

- **Idempotent settlement**: ledger credit relies on a DB unique constraint — duplicate confirms never double-credit
- **Row-level locks**: all balance changes use `SELECT ... FOR UPDATE` — concurrent withdrawals can't overdraw
- **Freeze model**: withdrawal request freezes instantly, rejection auto-unfreezes, payout deducts from frozen — books always reconcile
- **Full ledger**: `wallet_transactions` records every change's type, amount, before/after balances, linked order/withdrawal, and operator
- **Permission isolation**: all fund endpoints require login; admin endpoints have separate authorization

## Tests（测试）

```bash
cd backend
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest tests/ -v        # ~100 cases (funds domain: escrow, settlement, wallet, withdrawals, multi-claim)
cd ../frontend
npm test                                         # ~27 cases
```

> Tests create and rebuild an isolated `game_boosting_test` database — never point them at production.

## Commercial License（商用授权）

Game Boosting Platform is open source under the **Apache License 2.0** — see [LICENSE](LICENSE).

- ✅ Free for personal use, learning, and research
- ✅ Commercial **deployment / SaaS operation / redistribution** requires a commercial license

📧 Contact（联系方式）:

- Email: **uskybox@outlook.com**
- WeChat（微信）: **togetUSD**
