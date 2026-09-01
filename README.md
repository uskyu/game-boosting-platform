# 三角洲派单平台 · Delta Dispatch

面向个人运营者的**三角洲行动派单系统**：老板（管理员）在后台派单，打手注册账号后进入大厅抢单，订单完成后自动结算到打手钱包余额，打手随时申请提现，管理员在后台逐笔人工审核并标记打款。

围绕"一个人管、几个人跑单"的真实使用场景设计——没有复杂的支付网关，资金闭环由人工转账 + 平台账本完成，每一分钱都有流水可查。

## 核心流程

```
管理员派单 ──► 订单进入大厅 ──► 打手抢单（或被指派）
     │                                   │
     │                            完成并提交交付
     │                                   │
     ├── 客户确认 ──► 自动结算 ◄─────────┘
     │              打手余额 + 订单收入
     │
     └── 打手申请提现 ──► 金额冻结 ──► 管理员逐笔处理
                          ├─ 通过 ──► 线下打款 ──► 标记已打款（填写流水号）
                          └─ 驳回 ──► 冻结金额自动退回可用余额
```

## 功能特性

### 派单与抢单
- 管理员创建订单或直接在后台把订单**指派给指定打手**（校验角色、状态与接单额度）
- 打手在订单大厅**抢单**，数据库行锁 + 接单额度并发控制，同一订单绝不会被两人抢到
- 完整订单状态机：待接单 → 进行中 → 待确认 → 已完成 / 争议中 / 已取消
- 订单争议人工介入处理

### 钱包与结算
- 每个用户独立钱包：**可用余额 / 冻结金额 / 累计收入 / 累计提现**
- 订单确认完成时**同一事务内自动结算**给接单打手，支持平台佣金比例（`COMMISSION_RATE`）
- `(order_id, type)` 唯一约束 + SAVEPOINT 保证**订单只入账一次**，并发确认也不会重复结算
- 金额全程 `Decimal` 运算，每次余额变动都会写入**不可变流水**（变动前后余额快照、操作人、备注）

### 提现
- 打手提交提现申请（支付宝 / 微信 / 银行卡），金额立即从可用余额转入冻结
- 管理员后台逐笔处理：**通过 → 线下打款 → 标记已打款（记录打款流水号）**，或**驳回（自动解冻）**
- 已打款记录永久可查，包含收款人、收款账号、打款流水号、处理时间

### 运营管理台
- 数据看板：用户 / 订单 / 收入趋势、游戏分布、打手排行
- 打手资质审核（带证明截图上传、接单额度设置）
- 订单管理（状态干预、派单按钮）、提现处理、钱包调账（手动充值/扣减）、游戏管理

### 平台能力
- JWT 双令牌认证 + 自动刷新，三角色权限体系（用户 / 打手 / 管理员）
- 实时聊天（WebSocket）、站内通知、双向评价与信誉分
- AI 需求解析（可选，DeepSeek 接口）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+ · FastAPI · SQLAlchemy 2（async）· Alembic · aiomysql |
| 前端 | Vue 3 · Vite 5 · Pinia · Vue Router · Tailwind CSS · ECharts |
| 数据库 | MySQL 8（utf8mb4）|
| 部署 | Docker Compose（一键起 MySQL + 后端 + 前端）|

## 快速开始

### Docker Compose（推荐）

```bash
cp backend/.env.example backend/.env   # 编辑：数据库、SECRET_KEY、管理员密码
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
| `SECRET_KEY` | JWT 签名密钥，务必替换为随机值 |
| `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` | 首次启动自动创建的管理员账号（密码 ≥ 8 位才创建）|
| `COMMISSION_RATE` | 平台佣金比例，0–1 之间，默认 `0`（全额结算给打手）|
| `DEEPSEEK_API_KEY` | 可选，AI 需求解析用，测试环境可填占位值 |

## 资金安全设计

- **幂等结算**：订单入账依赖数据库唯一约束，重复确认不会二次入账
- **行级锁**：所有余额变动 `SELECT ... FOR UPDATE`，并发提现不可能透支
- **冻结模型**：提现申请即冻结，驳回自动解冻，打款扣减冻结——任何时刻账实相符
- **完整流水**：`wallet_transactions` 记录每一笔变动的类型、金额、前后余额、关联订单/提现单与操作人
- **权限隔离**：资金接口全部要求登录，管理员接口独立鉴权

## 测试

```bash
cd backend
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest tests/ -v        # 42 用例（含资金域 14 个）
cd ../frontend
npm test                                         # 27 用例
```

> 测试会创建并重建独立的 `game_boosting_test` 数据库，请勿指向生产库。

## 许可证

[MIT](LICENSE)
