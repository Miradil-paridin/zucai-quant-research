# 历史足球数据真实性与质量验收规范

本规范只用于验收外部真实数据，不生成、不修补、不插值任何比赛、比分或赔率。缺失值保留为空并进入隔离/告警；不得用 `0`、均值、模型结果或语言模型推测值替代。

## 1. 验收分层

数据必须分别通过三层检查，不能把其中一层冒充另一层：

1. **来源与文件完整性**：原始 HTTPS URL 属于数据提供方；下载清单记录 UTC 时间、HTTP 状态、字节数和 SHA-256；审计时哈希一致。
2. **内部语义一致性**：CSV 字段、日期、队名、比分、赛果、赛季覆盖、重复键和赔率满足规则。
3. **独立来源抽样核验**：用与原始 CSV 不同的来源核对确定性抽样比赛的日期、对阵和最终比分，并保留核验 URL。

前两层通过、第三层未运行时，只能标记为“内部检查通过，尚未独立验证”，不能宣称数据已被证明真实。

## 2. 必须保留的下载清单

原始 CSV 必须只读保存，并配套 JSON manifest。每个文件至少包含：

```json
{
  "files": [
    {
      "path": "E0.csv",
      "url": "https://www.football-data.co.uk/...",
      "downloaded_at_utc": "2026-07-27T00:00:00Z",
      "http_status": 200,
      "content_type": "text/csv",
      "bytes": 123456,
      "sha256": "64-character-lowercase-hex"
    }
  ]
}
```

重定向时还应保存初始 URL 与最终 URL；若响应提供 `ETag` 或 `Last-Modified`，也一并保存。SHA-256证明下载后内容没有变化，但它本身不能证明提供方内容绝对无误。

## 3. 自动检查清单

### P0：失败即禁止入模

- 五大联赛范围必须包含 `E0`（英超）、`SP1`（西甲）、`D1`（德甲）、`I1`（意甲）、`F1`（法甲）。
- 完整赛季必须满足双循环场次：`N × (N − 1)`；每队主场与客场各 `N − 1` 场。
- 唯一键 `(Div, Date, HomeTeam, AwayTeam)` 不得重复；主客队不得相同。
- `FTHG/FTAG` 必须为非负整数，`FTR` 必须与比分一致；半场比分不能超过全场比分。
- 完整历史赛季不得含未来日期、空比分、占位/演示队名。
- 赔率必须为十进制且大于 1；1X2 三项必须成组出现；市场最大值不得低于平均值。
- manifest 缺失、URL 非官方 HTTPS、SHA-256 不一致或声明为 synthetic/generated 时拒绝。

### P1：告警并人工复核

- 队名仅因标点或 `FC/AFC/CF` 后缀不同而形成疑似别名。
- 2019/20 以后 closing 1X2 覆盖低于 90%。
- 单场 1X2 的隐含概率和明显偏离常见区间。
- 数据源已公开说明某个博彩公司或时间段存在质量风险。

## 4. 赔率与防未来泄漏

football-data.co.uk 说明 2019/20 起同时提供开盘与封盘字段，封盘字段名含 `C`。但 CSV 没有逐行赔率采集时间，因此：

- closing odds 可作为市场基准、CLV 标签或“假定临近封盘下注”的回测价格；
- closing odds 不得进入更早 `as_of_utc` 的赛前特征；
- opening odds 在没有逐行捕获时间时同样不能被声称为某个精确 `as_of_utc` 已知；
- 比分、赛果、赛后统计只能作为标签，禁止出现在赛前特征；
- walk-forward 必须满足 `train_end < test_start`，并对每个特征验证 `event_time/captured_at < as_of_utc`。

提供方还明确警告：自 **2025-07-23** 起 Pinnacle 公共 API 数据可能相对其他博彩公司系统性滞后。该日期后的 `PS*` 字段必须带质量告警，不应单独作为封盘基准。

## 5. 入库血缘要求

蓝图中的 `core.historical_matches` / `core.historical_odds` 还不足以逐行追溯。正式导入前至少应增加或关联：

- `source_file_id`
- `source_file_sha256`
- `source_row_number`
- `source_url`
- `downloaded_at_utc`
- `price_timing`：`opening` / `closing` / `preclosing_legacy` / `unknown`
- `point_in_time_safe`：默认 `false`

建议新增 `raw.source_files` 保存文件级元数据，历史表只保存 `source_file_id + source_row_number`。任何标准化记录都必须能反查到未修改的原始 CSV 行。

## 6. 运行审计

完成赛季五大联赛：

```powershell
python scripts/audit_historical_csv.py source_samples `
  --manifest source_samples/manifest.json `
  --season 2024/2025 `
  --expected-leagues E0,D1,I1,SP1,F1 `
  --require-complete `
  --output output/historical-audit-2024-2025.json
```

独立核验文件需包含：

```csv
Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,SourceURL
```

加入 `--reference verified-sample.csv` 后，脚本将核对每条参考记录并把参考文件自身的 SHA-256 写入报告。参考文件必须由人工/独立采集步骤从另一个来源制作，不能从被审计 CSV 复制。

脚本输出非零退出码表示至少一项 P0 失败。报告中的 `authenticity_verdict` 会明确区分“内部检查”与“独立抽样核验”。

## 7. 模型使用闸门

只有同时满足以下条件的数据批次才能进入训练或回测：

- P0 失败数为 0；
- 五大联赛和目标赛季覆盖完整；
- 原始哈希与 manifest 一致；
- 队名映射冲突已处理或隔离；
- 独立比分抽样无冲突；
- 特征构建的时间边界测试通过；
- 所有赔率明确标注 timing 与 point-in-time 安全级别。

北欧联赛按相同规则验收，但优先级低于五大联赛；不能为了凑覆盖率降低真实性标准。
