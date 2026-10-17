# 项目终结报告 / Final Report

> 状态 Status：**已封存 Archived（2026-10-18）**
> 结论 Verdict：**未发现可实盘的正期望边际 — No exploitable positive-EV edge found.**

[中文](#中文) · [English](#english)

---

## 中文

### 一句话结论

三条研究路线全部给出否定答案：自建模型对公开赔率零增量；竞彩 vs 国际封盘的历史价差不显著；两个半月带时间戳的实时快照也没有发现“竞彩调价滞后”错价。在竞彩约 88% 的返奖率下，任何朴素策略的期望收益约为 **每注 -11%**。本项目不提供、也不应被用作投注建议。

### 路线一：自建概率模型（v1–v3，2026-07）

| 指标（2025/26 五大联赛 1,752 场样本外） | 自建模型 v2 | 开盘市场 | 封盘市场 |
|---|---:|---:|---:|
| Brier | 0.197902 | 0.194180 | 0.194015 |
| Log Loss | 0.995701 | 0.978374 | 0.977441 |

- 模型校准良好（ECE ≈ 0.014），即“预测本身是靠谱的”，但信息量不及市场。
- v3 允许模型与开盘赔率任意混合，验证区选择的最优权重是 **100% 市场、0% 模型** —— 公开比分/射门数据对赔率零增量信息。

### 路线二：竞彩停售价 vs 国际封盘共识（历史 7,051 场）

- 竞彩平均抽水 13.5%，全部方向 EV 均值 -10.9%。
- EV>0% / >2% 的 ROI 为 +3.2% / +6.0%，但 t 值 < 1；EV>5% 反而 -8.3%（阈值非单调 = 逆向选择红旗）。

### 路线三：实时调价滞后检验（2026-07-27 至 2026-10-17）

| 项目 | 数值 |
|---|---:|
| 采集到的竞彩比赛 | 948 |
| 双侧（竞彩 + 锐利庄家）对齐的比赛 | 283 |
| 同时刻价格对齐对 | 1,593 |
| 竞彩平均抽水 | 12.9% |
| 全部方向 EV 均值 | -10.7% |
| EV>0 的方向占比 / EV>2% | 2.3% / 1.1% |

按“首次出现 EV 超阈值即平注 1 单位”以真实赛果结算：

| 阈值 | 注数 | 中 | ROI | ROI 标准误 | CLV 均值 (n) |
|---|---:|---:|---:|---:|---:|
| EV>0% | 31 | 10 | +47% | ±43% | -1.95% (27) |
| EV>2% | 12 | 3 | +56% | ±81% | +1.62% (10) |
| EV>5% | 7 | 1 | -11% | ±114% | +2.56% (5) |

解读：

1. **账面盈利是噪声**：平均赔率 ~8.5、12 注里中 3 注，标准误 ±81%；要在该赔率下证明 +2% 的真实 ROI 约需数万注，按每月 ~5 个信号永远无法达到。
2. **CLV 方向相反**：若竞彩确实调价滞后，信号出现后国际价格应向信号方向移动。实测相反——表观 EV 在 0~2% 的机会到封盘平均回吐约 3.7 个百分点，即国际价格向竞彩靠拢，而非竞彩追赶国际。EV>0 的 27 个信号 CLV 均值 -1.95%（t ≈ -1.8）。
3. **大部分信号买不到单关**：信号比赛中绝大多数胜平负未开单关，只能与 EV≈-11% 的其他场次串关，+5% 的表观边际会被稀释为约 -6.5%。
4. 阈值非单调（>5% 反而亏）再次出现，与历史回测一致。

### 运行期已知问题（封存时未修复）

- `consensus_gate.evaluate_gate`：一次网络失败（2026-10-07 DNS 错误）会把当月 `quota_verified` 置为 false，之后网关每次都返回 `provider_quota_unknown` 跳过，直到下月才恢复——国际赔率采集因此在 10-07 之后完全中断。修复思路：失败后间隔数小时允许一次探测请求。
- 竞彩官网 WAF 对境外出口 IP 返回 567，10 月约三成采集被拦截。
- 所有 Windows 计划任务已于封存时停用。

### 复现

```powershell
cd backend
.\.venv\Scripts\python.exe ..\scripts\final_live_audit.py
```

需要本地 PostgreSQL 中已有采集数据；结果写入 `output/final_live_audit/`。

---

## English

### Bottom line

All three research tracks came back negative: the in-house model adds zero information over public odds; the historical spread between China Sports Lottery (Jingcai) fixed odds and international closing odds is not significant; and 2.5 months of timestamped live snapshots show no "Jingcai repricing lag" mispricing. With Jingcai's ~88% payout ratio, any naive strategy has an expected return of about **-11% per bet**. This project is not, and must not be used as, betting advice.

### Track 1 — In-house probability model (v1–v3, Jul 2026)

| Metric (1,752 out-of-sample Big-5 matches, 2025/26) | Model v2 | Opening market | Closing market |
|---|---:|---:|---:|
| Brier | 0.197902 | 0.194180 | 0.194015 |
| Log loss | 0.995701 | 0.978374 | 0.977441 |

- Well calibrated (ECE ≈ 0.014) — the forecasts are sound, but carry less information than the market.
- v3 allowed any blend of model and opening odds; the validation window chose **100% market, 0% model**.

### Track 2 — Jingcai closing price vs international consensus (7,051 historical matches)

- Average Jingcai overround 13.5%; mean EV across all outcomes -10.9%.
- EV>0% / >2% ROI of +3.2% / +6.0% with t < 1; EV>5% turns to -8.3% (non-monotonic = adverse selection).

### Track 3 — Live repricing-lag test (2026-07-27 → 2026-10-17)

| Item | Value |
|---|---:|
| Jingcai matches collected | 948 |
| Matches with both sides (Jingcai + sharp book) aligned | 283 |
| Time-aligned price pairs | 1,593 |
| Average Jingcai overround | 12.9% |
| Mean EV, all outcomes | -10.7% |
| Share of outcomes with EV>0 / EV>2% | 2.3% / 1.1% |

Flat 1-unit stake on the first time an outcome crosses the threshold, settled on real results:

| Threshold | Bets | Won | ROI | ROI s.e. | Mean CLV (n) |
|---|---:|---:|---:|---:|---:|
| EV>0% | 31 | 10 | +47% | ±43% | -1.95% (27) |
| EV>2% | 12 | 3 | +56% | ±81% | +1.62% (10) |
| EV>5% | 7 | 1 | -11% | ±114% | +2.56% (5) |

Interpretation:

1. **Paper profit is noise.** Average odds ~8.5, 3 hits out of 12, standard error ±81%. Proving a true +2% ROI at these odds needs tens of thousands of bets; at ~5 signals/month that is unreachable.
2. **CLV points the wrong way.** If Jingcai lagged, international prices should move toward the signal. Instead, apparent edges of 0–2% lose ~3.7 points by close — the international market moves toward Jingcai, not the reverse. Mean CLV for the 27 EV>0 signals is -1.95% (t ≈ -1.8).
3. **Most signals cannot be bet as singles.** Most signalled matches had no single-match 1X2 pool; parlaying with a ~-11% EV leg turns a +5% edge into roughly -6.5%.
4. The non-monotonic threshold pattern (EV>5% loses) reappears, matching the historical backtest.

### Known operational issues (left unfixed at archive time)

- `consensus_gate.evaluate_gate`: a single network failure (DNS error on 2026-10-07) sets the month's `quota_verified` to false, after which the gate skips every run with `provider_quota_unknown` until the next month. International odds collection stopped entirely after 10-07. Fix idea: allow one probe request a few hours after a failure.
- The Jingcai site's WAF returns HTTP 567 for non-mainland egress IPs; ~30% of October collections were blocked.
- All Windows scheduled tasks were disabled at archive time.

### Reproduce

```powershell
cd backend
.\.venv\Scripts\python.exe ..\scripts\final_live_audit.py
```

Requires the collected data in local PostgreSQL; output goes to `output/final_live_audit/`.
