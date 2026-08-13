# API 设计

所有接口以 `/api` 为前缀，响应中的业务时间为 ISO 8601 UTC；前端负责转换为 `Asia/Shanghai`。

| 方法 | 路径 | 当前状态 | 说明 |
|---|---|---|---|
| GET | `/api/health` | 真实 | 服务、数据库和密钥配置健康 |
| GET | `/api/fixtures` | 真实 | 读取已保存的 API-Football / football-data.org 赛程 |
| GET | `/api/ingestion/status` | 真实 | PostgreSQL 记录数、采集批次和调度状态 |
| POST | `/api/ingestion/run?days=7` | 真实 | 手动执行一次配额感知采集 |
| GET | `/api/historical/status` | 真实 | 历史源文件、比赛、赔率、核验和隔离统计 |
| GET | `/api/historical/matches` | 真实 | 分页读取真实历史比赛、比分、统计与开/封盘参考赔率 |
| GET | `/api/research/teams` | 真实计算 | 按联赛和截止日期计算真实赛果滚动基线 |
| GET | `/api/research/backtests/latest` | 真实计算 | 最近一次五大联赛 walk-forward 概率回测 |
| POST | `/api/research/backtests/run` | 真实计算 | 幂等重跑固定配置的历史概率回测 |
| GET | `/api/dashboard/summary` | 演示 | 固定脚手架，响应含 `demo_not_for_decision` |
| GET | `/api/matches` | 演示 | 固定概率卡片，响应含 `demo_not_for_decision` |
| GET | `/api/matches/{match_id}` | 演示 | 固定概率卡片，响应含 `demo_not_for_decision` |
| GET | `/api/backtests/summary` | 旧演示接口 | 仅为兼容旧脚手架；前端不再调用 |
| GET | `/api/ops/health` | 演示 | 旧版固定运维样例，响应含 `demo_not_for_decision` |
| POST | `/api/simulations/watchlist` | 模拟 | 只建立模拟观察，不创建真实资金操作 |

当前参数校验与业务错误采用 FastAPI 标准 `detail` 格式，例如：

```json
{
  "detail": "lineup confidence below hard safety floor"
}
```

`/api/research/backtests/latest` 的首版只开放概率质量指标。历史赔率的
`point_in_time_safe` 全部为 `false`，因此响应中的 `roi_available`、
`clv_available` 和 `max_drawdown_available` 均为 `false`；封盘赔率只作为
`ex_post_market_diagnostic` 市场概率基准，不能解释为可成交价格。

球员历史评分 API 仍未实现；球员页只展示数据库当前真实覆盖率和安全闸状态，
不再渲染任何示例球员、示例评分或虚构数据。
