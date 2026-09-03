# 游戏派单平台 · Game Boosting Platform

> 多名额派单，先到先得抢单，老板自己审核结算，钱包提现，争议处理，实时聊天。

游戏派单平台是一套可自己部署的**游戏代练派单系统**，给个人老板和小团队用：老板发单并托管资金，打手抢名额、干活、提交交付，老板审核后自动结算到打手钱包，打手提现，管理员逐笔人工打款——每一分钱都有流水可查。

## 关键词 / SEO

`game boosting` · `order dispatch` · 派单 · 代练 · 陪玩 · `Delta Force` 三角洲行动 · 抢单结算 · 钱包账本 · 资金托管 · 提现 · 争议处理

## 核心流程

```
发单 ──► 托管（单价 × 名额，从老板钱包冻结）
              │
              ▼
        抢单（先到先得，数据库行锁 + 名额并发控制）
              │
              ▼
        交付（名额：CLAIMED ──► DELIVERED）
              │
              ▼
        审核（老板按名额审核，或到账时效到了自动结算）
              │
              ▼
        结算（名额：DELIVERED ──► SETTLED，同一事务入账）
              │
              ▼
        提现（打手申请 → 金额冻结 → 管理员逐笔处理）
              │
              ▼
        人工打款（线下转账 → 管理员标记已打款并填流水号；
                   驳回 → 自动解冻退回可用余额）
```

订单状态：`待接单 PENDING → 进行中 LOCKED → 待确认 DELIVERED → 已完成 COMPLETED / 争议中 DISPUTED / 已取消 CANCELLED`。

## 功能列表

### 派单与抢单

- 老板发单可设**多个名额**（`max_claims`）；打手**先到先得**抢名额，数据库行锁 + 名额并发控制——同一个名额绝不会被两个人抢到
- 发单即**全额托管**：`单价 × 名额` 先从老板钱包冻结，余额不足发不了单
- 管理员可把订单**直接指派**给指定打手（校验角色、状态、接单额度）
- 每个名额独立生命周期：**已接单 CLAIMED → 已交付 DELIVERED → 已结算 SETTLED**
- **争议 + 管理员介入**：争议订单可取消、标记交付、完结

### 结算

- 审核通过后**同一事务内结算**：打手余额 += 订单收入
- **不抽佣**：`COMMISSION_RATE = 0.0`——打手拿全额
- `(order_id, type)` 唯一约束 + SAVEPOINT 保证**订单只入账一次**，并发确认也不会重复结算
- 金额全程 `Decimal` 运算，每次余额变动都写**不可变流水**（变动前后余额快照、操作人、备注）

### 灵活到账时效

- 每张订单可设到账时效：**天数**（`payout_delay_days`，0–30）+ **小时数**（`payout_delay_hours`，0–23）；都不填 = 不设置
- 交付后，后台调度器**到时自动结算**

### 钱包与提现

- 每人独立钱包：**可用余额 / 冻结金额 / 累计收入 / 累计提现**
- 提现申请（支付宝 / 微信 / 银行卡），金额立即从可用余额转入冻结
- 管理员逐笔处理：**通过 → 线下打款 → 标记已打款（填打款流水号）**，或**驳回（自动解冻）**
- 已打款记录永久可查（收款人、收款账号、打款流水号、处理时间）

### 平台能力

- **注册图形验证码**，防脚本批量刷号
- JWT 双令牌认证 + 自动刷新；三角色（用户 / 打手 / 管理员）
- **实时聊天**（WebSocket），订单双方 + 客服都可聊
- **站内通知**：接单、交付、结算、提现全有通知
- **管理后台**：用户 / 订单 / 收入趋势、游戏分布、打手排行，打手资质审核（证明截图上传 + 接单额度设置），订单干预，提现处理，钱包手动调账（充值 / 扣减），游戏管理
- 双向评价 + 信誉分；可选 AI 需求解析（DeepSeek 接口）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+ · FastAPI · SQLAlchemy 2（async）· Alembic · aiomysql |
| 前端 | Vue 3 · Vite 5 · Pinia · Vue Router · Tailwind CSS · ECharts |
| 数据库 | MySQL 8（utf8mb4）|
| 部署 | Docker Compose（MySQL + 后端 + 前端，一键起）|

## 快速开始

### Docker Compose（推荐）

```bash
cp backend/.env.example backend/.env   # 改：数据库、SECRET_KEY、管理员密码
docker compose up -d --build           # 首次启动自动执行数据库迁移
```

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost |
| 后端 API | http://localhost:8000 |
| Swagger 文档 | http://localhost:8000/api/v1/docs |
| 健康检查 | http://localhost:8000/health |

### 本地开发

```bash
# 后端（Python 3.10–3.12）
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
# Linux/macOS: python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 前端
cd frontend
npm ci
npm run dev            # http://localhost:3000，/api 代理到 8000
```

### 环境变量（backend/.env）

| 变量 | 说明 |
|---|---|
| `DB_URL` | `mysql+aiomysql://user:pass@host:3306/dbname` |
| `SECRET_KEY` | JWT 签名密钥，务必换成随机值 |
| `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` | 首次启动自动创建的管理员账号（密码至少 8 位）|
| `COMMISSION_RATE` | 平台抽佣比例，0–1，默认 `0.0`（全额结算给打手，即不抽佣）|
| `DEEPSEEK_API_KEY` | 可选，AI 需求解析用，测试环境填占位值就行 |

## 资金安全设计

- **幂等结算**：入账靠数据库唯一约束，重复确认不会二次入账
- **行级锁**：所有余额变动 `SELECT ... FOR UPDATE`，并发提现不可能透支
- **冻结模型**：提现申请即冻结，驳回自动解冻，打款扣减冻结——任何时刻账实相符
- **完整流水**：`wallet_transactions` 记录每笔变动的类型、金额、前后余额、关联订单 / 提现单、操作人
- **权限隔离**：资金接口全部要登录，管理员接口独立鉴权

## 测试

```bash
cd backend
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest tests/ -v        # 约 100 用例（含资金域：托管、结算、钱包、提现、多名额）
cd ../frontend
npm test                                         # 约 27 用例
```

> 测试会创建并重建独立的 `game_boosting_test` 数据库，千万别指向生产库。

## 商用授权

本项目以 **Apache License 2.0** 开源——见 [LICENSE](LICENSE)。

- ✅ 个人使用、学习、研究，免费
- ✅ 商用**部署 / 二开上线 / 对外运营**，需要商业授权

📧 联系方式：

- 邮箱：**uskybox@outlook.com**
- 微信：**togetUSD**
