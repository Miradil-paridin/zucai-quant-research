# 历史数据库最终独立审计

审计时间：2026-07-28（Asia/Shanghai）  
结论：`PASS_WITH_WARNINGS`  
P0 阻断项：**0**

## 最终范围

- 数据源：football-data.co.uk 哈希固定 CSV
- 范围：五大联赛，1993/94–2025/26，共 33 个赛季、165 个文件
- PostgreSQL：59,079 场比赛、818,368 条赔率
- 球队：248 个标准实体、340 条来源别名
- 2019/20 法甲按真实停赛结果保存 279 场；没有补造未进行的比赛

## P0 审计结果

全部通过：

- 165/165 个本地源文件的 SHA-256、字节数、URL、赛季和联赛与数据库一致。
- 59,079/59,079 场可由源 CSV 逐行重建；`raw_row`、行号、行哈希、日期、球队、全场/半场比分和比赛统计一致。
- 818,368/818,368 条赔率可由原始列重建；bookmaker、market、盘口、价格、来源列、质量标记和时间类型一致。
- 比赛自然键重复 0、来源行重复 0、赔率键重复 0。
- 比分/赛果语义错误 0、完整赛季双循环配对错误 0、球队映射缺失 0。
- 规范化赔率中小于等于 1 的价格 0、market shape 错误 0。
- `price_timing` 错误 0，所有赔率均正确标记 `point_in_time_safe=false`。
- 2024/25 的 1,752 场全场比分均经独立来源确认；其中 1,750 场为 `verified_normal`，2 场为 `awarded`。
- Union Berlin–Bochum 的缺失半场/统计保持 `NULL/{}`；Montpellier–St Etienne 的中止/判定状态和非完整 90 分钟统计标记已保留。
- Fiorentina–Inter 的半场冲突没有采用多数票覆盖：数据库保留官方比赛中心支持的 0-0，并记录冲突标记。
- 两次连续全量离线导入均为 165/165、59,079、818,368、0 errors；哈希输入指纹相同，幂等验证通过。

## P1 与使用边界

1. 其余 57,327 场是“来源真实、哈希固定、逐行可复现”，但仍是单一来源，状态保持 `unverified`。不能宣称这些赛季已经完成双源比分验证。
2. 18 个原始非法赔率单元格仍保存在 `raw_row`，35 个非法或不完整 quote group 未进入规范化赔率；不得用均值、默认值或模型推测补齐。
3. 169,825 条 closing、205,556 条 opening、442,987 条 legacy pre-closing 赔率均无逐行采集时间，只可作为市场标签/赛后基准，不能用于任意更早 `as_of` 的训练特征。
4. 2025/26 的 5,383 条 Pinnacle 赔率已全部带来源陈旧风险标记，不应作为唯一市场基准。

## 可复现命令

```powershell
.\backend\.venv\Scripts\python.exe -m py_compile scripts\audit_historical_database.py
.\backend\.venv\Scripts\python.exe scripts\audit_historical_database.py --output output\historical-database-audit-final.json
```

2024/25 独立比分和官方异常证据复核：

```powershell
.\backend\.venv\Scripts\python.exe scripts\crosscheck_historical_results.py `
  data\source_samples\football-data-co-uk\2024-25 `
  data\source_samples\openfootball\2024-25 `
  data\source_samples\football-data-org\2024-25-validation `
  --official-root data\source_samples\official-validation\2024-25 `
  --output output\historical-crosscheck-2024-2025.json
```

## 固定证据

- 最终数据库审计 JSON SHA-256：`861abdd58f803323bab99a0081bdebf4d99d4557a65b5d9a4ac634141aa5e071`
- 来源文件逻辑指纹：`8d2df5b85522de9213dd0ea32e6a2ecdd575ea8ad88b1d7196ac46967edca6d2`
- 比赛逻辑指纹：`5dd7fc64feda8ce6d265f10e0105264dbf3bc1d203e1e230986f69840f9cb905`
- 赔率逻辑指纹：`081f0a640515f3c7b991a7e74673571328b3566761f7c7fb317bc0782e160c77`
- 球队别名逻辑指纹：`f36311bf6e3e2c500b237a1e463125bd33c53527ca9dcdf767abd8c169deb3e8`
- 两次全量导入的固定输入指纹：`48c8d99e29f7c29dfa8e8704a6c25971ec155c410999ba1cef8abc1b72e7ab00`

本次审计没有发现数据库中存在凭空生成、无法追溯到原始 CSV 的比赛或赔率。
