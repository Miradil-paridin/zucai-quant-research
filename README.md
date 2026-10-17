# 足彩量化研究台 · Football Quant Research Desk

**中文** · [English](README.en.md)

> **项目已封存（2026-10-18）。** 结论：在公开数据和竞彩约 88% 的返奖率下，**没有找到可实盘的正期望边际**。
> 本仓库是一份可审计的研究记录，**不是投注工具，也不构成任何投注建议**。完整结论见 [docs/final-report.md](docs/final-report.md)。

## 这是什么

一个面向足球量化研究的本地全栈系统，用来回答一个问题：**能不能用数据在竞彩足球上长期赚钱？**

它做了三件事：

1. **自建概率模型**：滚动 Poisson / Dixon-Coles + 射门特征 + 温度校准，在严格防泄漏的 walk-forward 回测里与博彩市场比较。
2. **历史价差回测**：把 2015–2026 年竞彩官方停售赔率与国际封盘共识逐场配对（7,051 场），检验跨市场价差。
3. **实时调价滞后检验**：每 10 分钟采集竞彩赔率、按需采集国际锐利庄家（Pinnacle 等）赔率，全部带时间戳，检验“竞彩调价慢于国际市场”是否会产生可下注的错价。

## 结论速览

| 路线 | 结果 |
|---|---|
| 自建模型 v1–v3 | 校准良好（ECE≈0.014），但 Brier/LogLoss 均逊于市场；允许与开盘赔率任意混合时，最优权重为 **100% 市场 / 0% 模型** |
| 竞彩 vs 国际封盘（历史 7,051 场） | 竞彩抽水 13.5%；EV>2% 的 ROI +6% 但 t<1，EV>5% 反而 -8.3% |
| 实时滞后检验（2.5 个月，1,593 组对齐价格） | 全部方向 EV 均值 -10.7%；12 个信号账面盈利但标准误 ±81%；表观边际在封盘前平均回吐 ~3.7 个百分点；多数信号无单关 |

**一句话：预测能做到“和市场一样准”，但抽水约 12%，要赚钱必须比市场准出 12% 以上——这是结构性的，不是再多研究几个月能补上的。**

## 技术栈

- **后端**：Python 3.14 · FastAPI · psycopg 3 · APScheduler · curl_cffi
- **数据库**：PostgreSQL 16（Docker），`raw` → `core` 分层，原始响应与 SHA-256 指纹全部留档
- **前端**：React 19 · Vite · ECharts（比赛中心、回测、赛前参考、数据运维等页面）
- **测试**：pytest（141 通过）、前端 smoke 测试

## 数据来源

| 来源 | 用途 | 是否需要密钥 |
|---|---|---|
| football-data.co.uk | 1993/94–2025/26 五大联赛及北欧联赛历史赛果与赔率 | 否 |
| football-data.org | 赛程、比分、跨来源核验 | 是 |
| API-Football | 赛程、阵容、伤停、事件 | 是 |
| The Odds API | 国际庄家实时 1X2 赔率 | 是 |
| 中国体育彩票竞彩官网 | 竞彩在售赔率与开奖结果 | 否（需中国大陆出口 IP） |

历史库合计 68,697 场比赛、848,390 条赔率；2024/25 五大联赛 1,752 场完成双源比分核验。

## 项目结构

```text
backend/            FastAPI 服务、采集器、历史库、回测与竞彩分析
  app/historical/   football-data.co.uk 与北欧历史数据导入
  app/research/     滚动 Poisson/DC 模型与 walk-forward 回测
  app/jingcai/      竞彩采集、国际共识采集、价差与调价滞后分析
  app/reference/    赛前参考页（市场去水概率 + 赛前/赛后留档）
  scripts/          一次性采集、导入、回测脚本
  tests/            pytest
frontend/           React + Vite + ECharts 数据驾驶舱
docs/               架构、数据库 schema、回测报告、终结报告
scripts/            历史数据审计脚本与终结审计 final_live_audit.py
data/source_samples 跨来源核验的哈希固定样本（不含任何密钥）
```

## 本地运行

### 1. 数据库

```powershell
docker compose up -d postgres
```

首次启动会自动执行 `docs/schema.sql`。`docker-compose.yml` 中的数据库口令仅供本机开发，端口只绑定 `127.0.0.1`；如需对外暴露请自行修改口令。

### 2. 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 在 .env 中填写你自己的 API_FOOTBALL_API_KEY、FOOTBALL_DATA_API_KEY、ODDS_API_KEY
uvicorn app.main:app --reload --port 8000
```

**密钥只放在 `backend/.env`，该文件已被 `.gitignore` 排除，仓库中不包含任何真实密钥。**

### 3. 前端

```powershell
cd frontend
npm install
npm run dev -- --port 5174
```

### 4. 常用脚本

```powershell
cd backend
# 导入五大联赛历史（默认 2021/22–2025/26；加 --all-history 回填 1993/94 起）
.\.venv\Scripts\python.exe scripts\import_historical.py
# 运行可复现的历史概率回测
.\.venv\Scripts\python.exe scripts\run_historical_backtest.py
# 竞彩 vs 国际封盘价差回测
.\.venv\Scripts\python.exe scripts\run_jc_spread_backtest.py
# 实时调价滞后终结审计
.\.venv\Scripts\python.exe ..\scripts\final_live_audit.py
# 测试
.\.venv\Scripts\python.exe -m pytest -q
```

不要使用 `docker compose down -v`，它会删除数据库数据卷。

## 研究原则

- **防泄漏**：每场预测只用更早日期的数据；同日比赛先统一预测、后统一更新。
- **不造数据**：缺失的比分、赔率时间戳一律标记为缺失；没有逐条采集时间的历史赔率不用于 ROI/CLV。
- **如实报告**：模型逊于市场就写逊于市场，不生成虚假盈利曲线。
- **可复现**：输入数据、实现代码、预测结果均有 SHA-256 指纹。

## 已知问题（封存时未修复）

- 国际赔率采集网关在一次网络失败后会锁死到下个月（详见终结报告）。
- 竞彩官网 WAF 会拦截境外出口 IP。

## 文档

- [终结报告 / Final report](docs/final-report.md)
- [回测报告](docs/backtest-report.md)
- [架构](docs/architecture.md) · [数据字典](docs/data-dictionary.md) · [API](docs/api.md) · [采集](docs/ingestion.md)
- [赛前参考系统](docs/reference-system.md)

## 免责声明

本项目仅用于数据工程与统计研究学习。研究结论明确表明：基于公开数据的竞彩投注期望收益为负。请勿将本项目用于实际投注。
