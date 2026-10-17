const fmt = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
const date = (value) => fmt.format(new Date(value))
const record = (s) => `${s.matches}场 · ${s.wins}胜 ${s.draws}平 ${s.losses}负 · 进${s.goals_for}失${s.goals_against}`

export function PrematchResearch({ data }) {
  if (!data) return null
  const sourceById = Object.fromEntries(data.sources.map((s) => [s.id, s]))
  const refs = (ids) => <span className="prematch-refs">{ids.map((id) => <a key={id} href={sourceById[id].url} target="_blank" rel="noreferrer" title={sourceById[id].title}>[{data.sources.findIndex((s) => s.id === id) + 1}]</a>)}</span>
  return <section className="prematch-research" data-research-id={data.research_id}>
    <div className="prematch-label"><b>资料分析 · 90分钟</b><span>{data.confidence_label}</span></div>
    <h4>{data.lean}</h4>
    <p>{data.summary}</p>
    {data.review_due && <p className="prematch-review-due" role="status">这份分析已到复核时点：请先核对最新新闻与首发，再采用原有倾向。</p>}
    <h5>判断依据</h5>
    <ul>{data.support.map((c) => <li key={c.text}>{c.text} {refs(c.source_ids)}</li>)}</ul>
    <h5>反面因素</h5>
    <ul>{data.counterpoints.map((c) => <li key={c.text}>{c.text} {refs(c.source_ids)}</li>)}</ul>
    <details className="prematch-detail"><summary>新闻、历史成绩与复核条件</summary>
      <h5>最新报道</h5>
      {data.news.map((c) => <p key={c.text}>{c.text} {refs(c.source_ids)}</p>)}
      <h5>两个赛季分开比较</h5>
      {Object.values(data.teams).map((t) => <div className="prematch-season" key={t.team_id}><b>{t.name}</b>
        {['previous', 'current'].map((k) => <span key={k}>{t[k].season} {t[k].competition}<br />{record(t[k].summary)}</span>)}
      </div>)}
      <p>{data.coverage_note}</p>
      <h5>哪些消息会改变判断</h5>
      <ul>{data.review_triggers.map((c) => <li key={c}>{c}</li>)}</ul>
      <p>{data.method}</p>
      <h5>来源与查阅时间</h5>
      <ol className="prematch-source-list">{data.sources.map((s) => <li key={s.id}><a href={s.url} target="_blank" rel="noreferrer">{s.title}</a><small>{s.published_on ? `发布 ${s.published_on} · ` : ''}查阅 {date(s.retrieved_at_utc)}（北京时间）</small></li>)}</ol>
    </details>
    <div className="prematch-saved"><span>分析截至 {date(data.as_of_utc)} · 已赛前留档</span><a href={`/api/reference/research/${data.research_id}`} target="_blank" rel="noreferrer">查看原始依据</a></div>
    <small className="prematch-boundary">这是资料支持的初步倾向，尚未通过历史检验形成胜率。新闻由本次检索补充，刷新页面不会自动搜索新闻。</small>
  </section>
}
