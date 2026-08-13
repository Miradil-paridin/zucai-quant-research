# 真实数据采集与持久化

## 当前链路

```text
API-Football / football-data.org
  -> raw.ingestion_batches
  -> raw.source_payloads（原始 JSON，SHA-256 去重）
  -> core.competitions / seasons / teams / matches
  -> core.injuries / odds_snapshots
  -> core.lineups / match_events / player_match_stats
  -> FastAPI 只读查询
  -> React 比赛中心与数据运维页

football-data.co.uk 供应商公开 CSV（官网直链）
  -> data/historical/raw（原文件，不改写）
  -> SHA-256 + HTTP Last-Modified + 逐文件校验报告
  -> raw.source_files / raw.historical_import_runs
  -> core.historical_matches / core.historical_odds
  -> 历史训练、时间序列回测与市场基线
```

## 五大联赛历史底座

`backend/scripts/import_historical.py` 默认低频下载五大联赛近五个赛季
（2021/22–2025/26）的供应商公开 CSV，并在入库前执行严格校验：

- 必须是 Football-Data 供应商预期的 CSV schema，HTML/错误页直接拒绝。
- 比分必须与 `FTR` 胜平负一致；半场数据存在时也必须自洽。
- 同一联赛、赛季、日期和主客队的自然键不得重复。
- 十进制赔率必须大于 1；0、负数和非法字符串仅保留在原始行，
  不进入 `core.historical_odds`，具体行号与字段写入校验报告。
- 缺失的半场、裁判或比赛统计保持 `NULL`/缺失，不使用 0 猜测。
- 每份文件保留来源 URL、SHA-256、HTTP Last-Modified、解析器版本和
  原始行 JSON，可以复查与重放。
- 2019/20 以前没有明确早盘/封盘字段的赔率标为 `preclosing_legacy`；
  2019/20 起按字段区分 `opening` / `closing`。由于都没有逐行采集时间，
  `point_in_time_safe` 一律为 `false`，禁止直接用于更早时点的赛前特征。
- CSV 没有显式时区。经第二来源逐场对时确认，大陆联赛的 `Time` 也是
  英国本地时间口径，因此统一标记为 `Europe/London`；绝不按联赛所在国猜测。

下载、校验并入库：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\import_historical.py
```

只下载和校验，不连接数据库：

```powershell
.\.venv\Scripts\python.exe scripts\import_historical.py --no-db
```

只使用已经缓存的原始文件：

```powershell
.\.venv\Scripts\python.exe scripts\import_historical.py --offline
```

低频串行回填 1993/94–2025/26 的五大联赛完整可下载范围：

```powershell
.\.venv\Scripts\python.exe scripts\import_historical.py --all-history
```

该命令共请求 165 个小型 CSV，默认请求间隔至少 0.5 秒。可通过
`HISTORICAL_REQUEST_DELAY_SECONDS` 进一步放慢，但不能低于程序的 0.25 秒
安全下限。1993/94–1999/00 主要只有赛果；缺失赔率和统计保持为空。

当前供应商明确提示 2025-07-23 之后 Pinnacle 公共赔率可能滞后。
这些赔率会原样保留，但带 `provider_warned_stale_after_2025-07-23`
质量标记，不能作为唯一市场基准。

## 独立真实性复核

采集文件通过内部校验，并不自动等于“已被独立证明真实”。2024/25 五大联赛
还使用 football-data.org 逐场核对日期、球队和全场比分；核验报告及参与核验
的原始文件都固定 SHA-256。应用核验结果和球队别名：

```powershell
.\.venv\Scripts\python.exe scripts\apply_cross_source_validation.py
.\.venv\Scripts\python.exe scripts\import_team_aliases.py
```

当前核验覆盖 1,752 场，football-data.org 全场比分一致 1,752 场、冲突 0 场。
两场官方裁定赛单独标为 `awarded`，相关半场或比赛统计保持缺失/风险标记；
Fiorentina–Inter 的半场聚合源冲突依据俱乐部官方比赛中心复查，未采用多数票。
其余 32 个赛季保持 `unverified`，在完成独立复核前不宣称已被交叉验证。

每次重新导入会以最新通过校验的文件替换该联赛/赛季的标准化赔率集合；
如果原始行哈希变化，既有的核验状态会自动失效，必须重新核验。含非空拒绝行
的文件拒绝部分入库，避免新旧版本混杂。

全量数据库还可由与导入器分离的只读审计器逐文件重建：

```powershell
.\backend\.venv\Scripts\python.exe scripts\audit_historical_database.py `
  --output output\historical-database-audit-final.json
```

当前最终报告见 `docs/historical-database-final-audit.md`：165/165 文件哈希、
59,079/59,079 场比赛及 818,368/818,368 条赔率均可追溯重建，P0 为 0。

## Nordic 次级历史底座

芬超、挪超、瑞超使用 Football-Data 官网的 `FIN.csv`、`NOR.csv`、`SWE.csv`
合并文件。独立只读审查确认 2012–2026 共 9,618 行，比分/赛果冲突、重复键、
非法日期和必填缺失均为 0。下载并入库：

```powershell
.\.venv\Scripts\python.exe scripts\import_nordic_historical.py
```

审查发现并强制执行以下边界：

- 2026 是进行中的日历年赛季，只标记为 partial snapshot，不假装完整赛季。
- `Time` 没有来源时区或 UTC 偏移，保存为 `source_timezone='unknown'`，禁止换算。
- 2025-07-23 后 252 组 Pinnacle 三项赔率不进入规范化表。
- 2021-08-31 KTP–Inter Turku 的 `AvgCA=1.75` 高于 `MaxCA=1.29`，
  对应 MarketMax/MarketAverage 两组 quote 均隔离，不猜测修正。
- 所有 Nordic 赔率为 `closing` 且 `point_in_time_safe=false`。

机器审查证据位于 `data/source_samples/nordic_source_validation.json`。

## 调度与额度

后端内置单实例调度器，默认每 3 小时运行一次。API-Football 免费账户的批量 `ids` 参数不可用，因此采集器采用以下策略控制每日 100 次额度：

- 当天赛程：每次调度 1 次请求。
- 未来 7 天五大联赛赛程：由 football-data.org 单次请求补充。
- 伤停：按日期单次获取，至少间隔 12 小时。
- 赔率：按日期分页获取，找到目标比赛后立即停止，至少间隔 6 小时。
- 阵容、事件、球员统计：仅对赛前 90 分钟内、进行中或已结束的目标比赛逐场获取。
- 调度前读取账户剩余额度；额度不足时停止增强数据请求。额度接口
  不可用时采取 fail-closed，剩余额度按 0 处理，不猜测还有 100 次。

竞彩官方赔率与 The Odds API 国际共识赔率使用
`scripts/collect_jingcai_odds.py`、`scripts/collect_consensus_odds.py` 两个一次性脚本，
不属于上述进程内调度器。若由 Windows 计划任务周期启动，任务的启用状态仍由
Windows 管理；运维页只展示脚本最近一次运行结果，不能据此判断计划任务是否启用。

共识赔率按成功采集批次门控：临近停售时默认最短间隔 3 小时，否则最多每天心跳一次；
失败后默认退避 3 小时。月度请求硬预算默认 400 次，并为数据源账户保留 100 次额度，
可通过 `ODDS_API_MONTHLY_REQUEST_BUDGET` 与 `ODDS_API_QUOTA_RESERVE` 调整；将月度预算设为
`0` 可关闭外部共识赔率请求。数据库不可用时两个脚本均先退避，不访问外部数据源。

## 幂等与时间

- 原始响应以 `source + resource_type + payload_hash` 去重。
- 标准比赛以 `season + home_team + away_team + kickoff_utc` 幂等更新。
- 来源 ID 通过 `core.source_entity_mappings` 映射到内部实体。
- 赔率按 `match + source + bookmaker + market + selection + captured_at` 只追加。
- 所有数据库业务时间均使用 UTC，前端转换为 `Asia/Shanghai`。

## 当前限制

- 免费 API-Football 不允许查询 2026 赛季级聚合接口，因此当前以比赛 ID 为主线逐日积累。
- 赔率刷新为 6 小时级，暂不能保证精确捕获每场 closing line。
- 阵容、事件和球员统计只有在数据源实际发布后才会入库。
- xG/xA 在当前免费来源中没有稳定覆盖，字段保留为空，不进行推测填充。
- 历史 CSV 不包含球员级阵容、事件和 xG；历史底座只用于赛果、比赛
  统计和可验证的历史赔率，不从赛果反推或合成球员数据。
