# 五大联赛量化研究台

一个面向足球量化研究者的可审计决策系统。项目以 `deep-research-report.md` 为唯一需求来源，当前首版聚焦：

- 五大联赛比赛、阵容、球员表现、伤停与赔率数据的统一沉淀。
- 球员位置评分与球队攻防强度聚合。
- 赛前胜平负概率、市场隐含概率与价值差对比。
- 时间序列回测、模拟下注、CLV 与回撤监控。
- 数据质量、模型版本和每次预测的完整审计链路。

## 项目结构

```text
backend/            FastAPI 服务、真实赛程聚合、历史查询与概率回测
frontend/           React + Vite + ECharts 数据驾驶舱
docs/               架构、数据库、页面与 API 设计
data/historical/    供应商官网历史 CSV 缓存与机器审计清单（原文件不改写）
deep-research-report.md
```

## 本地运行

数据库（首次运行会自动创建 PostgreSQL 16 数据卷并执行 `docs/schema.sql`）：

```powershell
docker compose up -d postgres
```

前端：

```powershell
cd frontend
npm install
npm run dev -- --port 5174
```

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 在 .env 中填写 API_FOOTBALL_API_KEY 与 FOOTBALL_DATA_API_KEY
uvicorn app.main:app --reload --port 8000
```

比赛中心通过 `/api/fixtures` 展示 API-Football 与 football-data.org 的真实赛程，
并可通过 `/api/historical/matches` 浏览已审计的真实历史比赛。球队页已切换为
真实赛果滚动基线，回测页已切换为真实 walk-forward 概率评估；球员页因缺少
足够的历史球员数据，仍保留明确隔离的示例内容。密钥仅保存在 `backend/.env`，
该文件已被 `.gitignore` 排除。

真实赛程的业务范围硬限制为：英超、西甲、德甲、意甲、法甲、瑞典超级联赛、挪威超级联赛和芬兰超级联赛。页面查询未来 7 天；受 API-Football 免费套餐限制，该来源当前只补充当天比赛，未来比赛由 football-data.org 的免费覆盖范围补充。

## 采集与持久化

- 后端每 3 小时自动执行一次配额感知采集。
- 赛程原始响应、伤停、赔率以及赛中/赛后详情先写入 `raw`，再标准化到 `core`。
- 伤停最短 12 小时刷新一次，赔率最短 6 小时刷新一次。
- 距离开赛不足 90 分钟、比赛进行中或已结束时，补拉阵容、事件和球员比赛统计。
- 比赛页面只读取 PostgreSQL，不因刷新页面额外消耗第三方 API 次数。
- 数据运维页可查看真实表计数、批次状态并手动触发采集。

五大联赛历史底座（默认 2021/22–2025/26）：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\import_historical.py
```

回填已验证可下载范围 1993/94–2025/26（五大联赛共 165 个源文件）：

```powershell
.\.venv\Scripts\python.exe scripts\import_historical.py --all-history
```

该任务从 football-data.co.uk 官网的公开 CSV 直链下载真实赛果与历史赔率。
每份文件保存来源 URL、HTTP Last-Modified、SHA-256、原始行和校验报告；
比分与赛果冲突、重复比赛以及不合法赔率不会进入标准化表。可通过
`GET /api/historical/status` 查看已入库覆盖。

对 2024/25 五大联赛执行哈希固定的跨来源核验，并导入已验证球队别名：

```powershell
.\.venv\Scripts\python.exe scripts\apply_cross_source_validation.py
.\.venv\Scripts\python.exe scripts\import_team_aliases.py
```

跨来源报告逐场对照 football-data.org；OpenFootball 和俱乐部/联赛官方页面
仅作为附加证据。程序不会用“多数票”覆盖主源，更不会补造缺失比分、半场或
统计。原始证据、来源 URL 和文件哈希保存在 `data/source_samples`，认证密钥
不会写入这些文件。

当前本地历史快照已经完成全范围回填：

- 1993/94–2025/26，五大联赛 33 个赛季、165 个哈希固定源文件。
- 59,079 场比赛、818,368 条规范化赔率；18 个非法源赔率单元已隔离。
- 2024/25 共 1,752 场完成跨来源全场比分核验；其余赛季诚实保留
  `unverified`，不会冒充双源验证。
- 独立审计结论 `PASS_WITH_WARNINGS`、P0 为 0，详见
  `docs/historical-database-final-audit.md`。

次级 Nordic 底座也已按独立审查结论入库：

- 芬超、挪超、瑞超 2012–2026，共 9,618 场和 30,022 条 closing 1X2 赔率。
- 官网未说明 `Time` 的时区，数据库明确保存为 `unknown`，不转换为 UTC。
- 2025-07-23 后 252 组 Pinnacle 赔率及 1 场结构冲突中的 2 组市场聚合赔率
  已隔离；原始值仍可通过 `raw_row` 追溯。
- 当前历史库合计 168 个源文件、68,697 场、848,390 条赔率。

## 真实概率回测

运行固定配置、可幂等复现的五大联赛历史回测：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\run_historical_backtest.py
```

当前 `rolling-poisson-dc-v1` 使用 2019-07-01 起的历史作为滚动训练底座，
对 2024/25 五大联赛 1,750 场跨来源核验的正常完赛逐日样本外预测。每个比赛日
先统一预测、后统一更新结果，因此同日比赛互相不可见；2 场判定比赛被排除，
历史赔率也不进入模型特征。

首轮真实结果：

- 模型 Brier：0.196524；封盘市场参考 Brier：0.190117。
- 模型 Log Loss：0.989187；封盘市场参考：0.960222。
- ECE：0.015424。
- 1,750 条预测已持久化，全部赛果均经跨来源核验，日期截止约束违规数为 0。
- 输入比赛、赔率、实现代码和预测内容分别保存 SHA-256 指纹；重复运行幂等。

当前基线略逊于市场，系统会原样展示。由于历史 opening/closing 赔率没有逐条
采集时间证明，ROI、CLV 和最大回撤保持 `null`，不会生成虚假盈利曲线。详见
`docs/backtest-report.md`。

复现最终数据库审计：

```powershell
.\backend\.venv\Scripts\python.exe scripts\audit_historical_database.py `
  --output output\historical-database-audit-final.json
```

Nordic 独立来源报告位于
`data/source_samples/nordic_source_validation.json`；重复导入使用：

```powershell
.\backend\.venv\Scripts\python.exe scripts\import_nordic_historical.py --offline
```

手动执行：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/ingestion/run?days=7
```

停止容器不会删除数据：

```powershell
docker compose stop postgres
```

不要使用 `docker compose down -v`，该命令会删除 PostgreSQL 数据卷。

## 设计与安全边界

- 赛季初仅采集，不输出真实投注建议。
- 首版只提供模拟观察与研究判断，不提供真实资金下单。
- 网页型数据源只用于低频补充和人工校验，生产主链路以授权 API 为准。
- GPT 仅用于解释已经由数值服务计算出的结构化结果，不参与核心概率计算和风控决策。
