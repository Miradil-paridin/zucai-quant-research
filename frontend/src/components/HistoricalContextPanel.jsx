import { useState } from 'react'
import { PostmatchFacts } from './PostmatchFacts'

const statusLabels = { reported_out: '报道缺席', suspended: '停赛报道', doubtful: '出场存疑', not_registered: '未注册', returning: '恢复／回归', no_absences_reported: '来源未报缺席' }
const timingLabels = { reported_pre_match: '赛前报道 · 赛后采集', mixed_updates: '混合更新 · 历史版本未核实', post_match: '赛后报道', official_match_sheet: '正式名单 · 赛后采集' }
const authorityLabels = { official: '官方', media: '媒体', secondary_aggregator: '二级汇总' }
const time = (value) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short', hour12: false }).format(new Date(value))

function SourceLink({ source, label }) {
  return source ? <a href={source.url} target="_blank" rel="noreferrer">{label || source.title} ↗</a> : null
}

export function HistoricalContextPanel({ data, loading }) {
  const [query, setQuery] = useState('')
  const sources = Object.fromEntries((data?.sources || []).map((s) => [s.id, s]))
  const items = (data?.items || []).filter((i) => `${i.label} ${i.home_team} ${i.away_team}`.toLowerCase().includes(query.toLowerCase().trim()))
  const summary = data?.summary
  return <section className="historical-context" aria-label="欧冠资料复盘">
    <div className="reference-overview"><div><strong>{summary?.matches ?? '—'}</strong><span>场已完赛</span></div><div><strong>{summary?.matches_with_statistics ?? '—'}</strong><span>场官方统计已核对</span></div><div><strong>{summary?.goals ?? '—'}</strong><span>粒进球已核对</span></div></div>
    <div className="context-boundary"><strong>先看比赛事实，再看复盘结论</strong><p>{data?.methodology || '读取已核对的欧冠资料中。'}</p></div>
    {summary && <details className="context-coverage"><summary>资料覆盖与缺口 · {summary.starters}条首发 / {summary.substitutions}次换人 / {summary.card_events}条罚牌</summary><p className="reference-footnote">引用来源{summary.sources}份。{summary.teams_with_reports}支球队有部分伤停／可用性报道，{summary.teams_with_unknown_availability}支仍缺来源；有报道不代表完整核实。xG尚未获得。历史时点已验证快照：{summary.verified_prematch_snapshots}份；这些比赛没有可验证的事前预测，不能补算命中率。</p></details>}
    <div className="context-search"><label htmlFor="context-team-search">查找球队</label><input id="context-team-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="球队中文或英文名" /><span>{items.length}场</span></div>
    <div className="context-list">{items.map((item) => <article className="context-card" data-context-match={item.match_id} key={item.match_id}>
      <header><div><span className="league-tag">欧冠 · 第1轮</span><time>{time(item.kickoff_utc)} · 北京时间</time></div><h3>{item.label}<b>{item.score.home} : {item.score.away}</b></h3></header>
      <PostmatchFacts item={item} sources={sources} />
      <details className="context-details"><summary>查看双方22名首发、伤停状态与来源</summary>
        <div className="context-assessment"><h4>阵容资料提出的假设 · 尚未建立因果验证</h4><p>{item.analysis.text}</p></div>
        <ul className="reference-warnings">{item.checks.map((c) => <li key={c}>{c}</li>)}</ul>
        {item.post_match_review && <div className="context-post-review"><h4>补充赛后观察与发言</h4><p>{item.post_match_review.text}</p>{item.post_match_review.source_ids.map((id) => <SourceLink key={id} source={sources[id]} />)}</div>}
        <p className="reference-footnote">{item.lineup.note} <SourceLink source={sources[item.lineup.source_id]} label="查看UEFA原始PDF" /></p>
        <div className="context-teams">{['home', 'away'].map((side) => <section key={side}>
          <h4>{side === 'home' ? item.home_team : item.away_team}</h4>
          <ol className="context-lineup">{item.lineup[side].map((p) => <li key={p.number}><span>{p.number}</span>{p.name}</li>)}</ol>
          <h4>伤停与可用性报道</h4><p className="reference-footnote">{item.availability[side].note}</p>
          {item.availability[side].coverage === 'unknown' && <p className="context-unknown">资料未知，不能视为无伤停。</p>}
          <ul className="context-availability">{item.availability[side].facts.map((f, index) => <li key={`${f.player}-${index}`}><b>{f.player}</b><span>{statusLabels[f.reported_status]}{f.detail ? ` · ${f.detail}` : ''}</span>{f.roster_resolution === 'started' && <strong className="context-resolved">正式名单：已首发</strong>}<SourceLink source={sources[f.source_id]} label="报道来源" /></li>)}</ul>
        </section>)}</div>
        <div className="context-source-list"><h4>来源与时间</h4>{item.source_ids.map((id) => <div key={id}><SourceLink source={sources[id]} /><small>{authorityLabels[sources[id].authority]} · {timingLabels[sources[id].timing]}<br />页面日期：{sources[id].published_label}<br />本次核对：{time(sources[id].reviewed_at_utc)}（北京时间）</small>{sources[id].sha256 && <code title="下载原文件的SHA-256">文件校验：{sources[id].sha256}</code>}</div>)}</div>
      </details>
    </article>)}</div>
    {!loading && !items.length && <p className="ops-empty-state">{data?.items?.length ? '没有匹配的球队。' : '尚未导入资料档案。'}</p>}
    {data?.version && <p className="reference-footnote">版本 {data.version} · 导入 {time(data.imported_at_utc)}（北京时间） · 概率修正：未启用，待多轮数据和独立检验。<a href="/api/reference/dossiers" target="_blank" rel="noreferrer">查看完整数据</a></p>}
  </section>
}
