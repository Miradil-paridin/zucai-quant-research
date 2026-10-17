import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, CheckCircle2, Clock3, Database, RefreshCw, ShieldCheck } from 'lucide-react'
import { Topbar } from '../components/Topbar'
import { HistoricalContextPanel } from '../components/HistoricalContextPanel'
import { PrematchResearch } from '../components/PrematchResearch'

const fmt = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
const date = (value) => value ? fmt.format(new Date(value)) : '暂无记录'
const percent = (value) => value == null ? '—' : `${(value * 100).toFixed(1)}%`
const phaseLabels = { h24: '开赛前24小时', h6: '开赛前6小时', h1: '开赛前1小时', manual: '手动留档' }
const statusLabels = { ready: '赔率参考可用', missing_fresh_odds: '等待新鲜报价', insufficient_sources: '来源不足', market_disagreement: '市场分歧较大' }

function FormRecord({ name, data }) {
  const f = data.overall
  const venue = data.venue
  return <div className="reference-form"><strong>{name}</strong>
    {data.scope && <small>{data.scope}</small>}
    <span>近{f.matches}场 · {f.wins}胜 {f.draws}平 {f.losses}负</span>
    <div className="reference-streak">{f.records.map((r) => <i className={`result-${r.result}`} key={`${r.date}-${r.source_id}`} title={`${r.date} · ${r.goals_for}:${r.goals_against}`}>{({ W: '胜', D: '平', L: '负' })[r.result]}</i>)}</div>
    <small>进{f.goals_for}球 / 失{f.goals_against}球 · 最近赛果 {f.latest_date || '缺失'}</small>
    <small>对应主／客场：近{venue.matches}场 {venue.wins}胜 {venue.draws}平 {venue.losses}负</small>
  </div>
}

function ReferenceCard({ item, onSave, saving }) {
  const market = item.market
  return <article className="reference-card" data-reference-match={item.match_id}>
    <div className="reference-card-top"><span className="league-tag">{item.competition}</span><time>{date(item.kickoff_utc)} · 北京时间</time></div>
    <h3>{item.home_team}<span>vs</span>{item.away_team}</h3>
    <PrematchResearch data={item.research} />
    <div className={`reference-status ${market.status === 'ready' ? 'ready' : ''}`}><span />{statusLabels[market.status]}</div>
    <div className="reference-probabilities">{['主胜', '平局', '客胜'].map((label, i) => <div key={label}><span>{label}</span><strong>{percent(market.probabilities?.[i])}</strong><div><i style={{ width: `${(market.probabilities?.[i] || 0) * 100}%` }} /></div></div>)}</div>
    <p className="reference-method">赔率去水后的市场估计 · {market.source_count}组有效来源 · 最大分歧 {percent(market.dispersion)}</p>
    <div className="reference-forms"><FormRecord name={item.home_team} data={item.forms.home} /><FormRecord name={item.away_team} data={item.forms.away} /></div>
    <details className="reference-evidence"><summary>来源、首发与伤停 <ArrowUpRight size={14} /></summary>
      <div className="reference-sources">{market.sources.map((s) => <div key={s.family}><b>{s.bookmaker}</b><span>{date(s.observed_at_utc)}</span><span>{s.odd_home} / {s.odd_draw} / {s.odd_away}</span></div>)}{!market.sources.length && <p>没有符合时效要求的来源；没有用过期报价填入概率。</p>}</div>
      <p>首发：{item.squads.lineup_status === 'confirmed' ? '双方11人已确认' : '未完整确认'}。伤停：{item.squads.injury_status === 'not_available' ? '资料缺失' : '已获得部分来源记录'}。</p>
      {item.squads.lineups.length > 0 && <p>{item.squads.lineups.map((p) => p.player).join('、')}</p>}
      {item.squads.injuries.map((p) => <p key={`${p.team_id}-${p.player}`}>{p.player} · {p.injury_type || p.status} · {p.source_name} · {date(p.captured_at_utc)}</p>)}
    </details>
    <ul className="reference-warnings">{item.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
    <footer><small>赔率检查于 {date(item.as_of_utc)}</small><button className="secondary-action" disabled={!item.ready_to_archive || saving} onClick={() => onSave(item.match_id)}><ShieldCheck size={15} />{saving ? '保存中' : '保存赔率参考'}</button></footer>
  </article>
}

export function ReferencePage() {
  const [view, setView] = useState(() => new URLSearchParams(window.location.search).get('view') === 'dossiers' ? 'dossiers' : 'upcoming')
  const [data, setData] = useState(null)
  const [performance, setPerformance] = useState(null)
  const [dossiers, setDossiers] = useState(null)
  const [phase, setPhase] = useState('h1')
  const [competition, setCompetition] = useState(() => new URLSearchParams(window.location.search).get('competition') || '')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch(view === 'upcoming' ? '/api/reference?days=30' : view === 'dossiers' ? '/api/reference/dossiers' : `/api/reference/scorecard?phase=${phase}`)
      if (!response.ok) throw new Error(`数据读取失败（${response.status}）`)
      const value = await response.json()
      if (view === 'upcoming') setData(value)
      else if (view === 'dossiers') setDossiers(value)
      else setPerformance(value)
      setError('')
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }, [view, phase])
  useEffect(() => { const timer = setTimeout(load, 0); return () => clearTimeout(timer) }, [load])
  const competitions = useMemo(() => [...new Map((data?.items || []).map((i) => [i.competition_code, { code: i.competition_code, name: i.competition }])).values()], [data])
  const items = (data?.items || []).filter((i) => !competition || i.competition_code === competition)
  const summary = performance?.summary
  async function save(id) {
    setSaving(id)
    try {
      const response = await fetch(`/api/reference/${id}/snapshot`, { method: 'POST' })
      if (!response.ok) throw new Error(`留档失败（${response.status}）`)
      const result = await response.json()
      setNotice(result.status === 'saved' ? `已保存档案 #${result.reference_id}，赛后自动结算。` : result.status === 'already_saved' ? '本场已有手动档案，保留原始判断。' : `当前不能留档：${statusLabels[result.status] || result.status}`)
    } catch (e) { setError(e.message) } finally { setSaving(null) }
  }
  return <div className="page standard-page reference-page"><Topbar title={view === 'dossiers' ? '欧冠赛后复盘' : '赛前参考'} eyebrow="来源可追溯 · 赛前留档 · 赛后验证" />
    <div className="standard-content">
      <header className="content-intro"><div><span className="eyebrow">{view === 'dossiers' ? 'CHAMPIONS LEAGUE / MATCH REVIEW' : 'REFERENCE / PROSPECTIVE RECORD'}</span><h2>{view === 'dossiers' ? '从比分到过程，看清每一场。' : '让每一次判断，留下依据。'}</h2><p>{view === 'dossiers' ? '官方统计、进球、换人和罚牌按场展示；结合比赛事实重新分析，所有来源均可展开核对。' : '市场概率与球队资料并列展示；缺失保持缺失，成绩从实际赛前留档开始累计。'}</p></div>
        <button className="secondary-action" onClick={load} disabled={loading}><RefreshCw size={16} className={loading ? 'spinning' : ''} />{loading ? '读取中' : '刷新已采数据'}</button></header>
      <div className="reference-toolbar"><div className="reference-tabs"><button className={view === 'upcoming' ? 'active' : ''} onClick={() => setView('upcoming')}>未来30天</button><button className={view === 'performance' ? 'active' : ''} onClick={() => setView('performance')}>留档与成绩</button><button className={view === 'dossiers' ? 'active' : ''} onClick={() => setView('dossiers')}>欧冠资料复盘</button></div>
        {view === 'upcoming' ? <select aria-label="赛事筛选" value={competition} onChange={(e) => setCompetition(e.target.value)}><option value="">全部赛事</option>{competitions.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select> : view === 'performance' ? <select aria-label="留档时点" value={phase} onChange={(e) => { setPerformance(null); setPhase(e.target.value) }}>{Object.entries(phaseLabels).map(([v, l]) => <option value={v} key={v}>{l}</option>)}</select> : null}</div>
      {error && <p className="ops-error-text" role="alert">{error}</p>}{notice && <p className="reference-notice" role="status">{notice}</p>}
      {view === 'upcoming' ? <>
        <div className="reference-overview"><div><Database size={18} /><strong>{items.length}</strong><span>场已采赛程</span></div><div><CheckCircle2 size={18} /><strong>{items.filter((i) => i.ready_to_archive).length}</strong><span>场赔率参考可用</span></div><div><Clock3 size={18} /><strong>24h / 6h / 1h</strong><span>自动留档时点</span></div></div>
        {items.some((i) => i.research) && <p className="reference-notice">{items.filter((i) => i.research).length}场已补充历史战绩与新闻分析。先看比赛卡片中的「资料分析」，再核对反面因素；没有赔率时也可以查看初步倾向。</p>}
        <p className="reference-footnote">远期比赛可能尚未开盘。来源不足3组或报价过期时暂停正式留档。刷新页面只读数据库，不额外消耗赔率接口额度。</p>
        <details className="reference-evidence"><summary>这些概率是怎么算的？<ArrowUpRight size={14} /></summary><p>读取多家机构的主胜／平局／客胜十进制赔率，分别取倒数，再除以三项倒数之和，去除账面利润率。同品牌地区版本合并；每项取来源中位数，再归一化为100%。例如赔率2.00／3.50／4.00对应约48.3%／27.6%／24.1%。这是一种去水估计，不代表真实概率已被证明。</p><p>球队战绩、伤停与首发供你对照阅读，当前没有参与概率计算。当前版本不是独立AI预测模型。留档最多允许15个百分点的来源分歧；来源组之间也可能相互关联。</p></details>
        <p className="reference-footnote">留档任务最近成功运行：{date(data?.pipeline?.last_success_at_utc)}。任务状态：{({ ok: '正常', partial: '赔率采集异常，留档／结算已尝试', error: '运行失败', db_unavailable: '数据库不可用', busy: '另一个任务正在运行' })[data?.pipeline?.last_status] || '尚无运行记录'}。电脑需保持开机、联网并登录；超过留档窗口的场次不会补写。</p>
        <div className="reference-grid">{items.map((item) => <ReferenceCard key={item.match_id} item={item} onSave={save} saving={saving === item.match_id} />)}</div>
        {!loading && !items.length && <div className="ops-empty-state">当前范围没有已采集的未来赛程。请到数据运维检查覆盖情况。</div>}
      </> : view === 'dossiers' ? <HistoricalContextPanel data={dossiers} loading={loading} /> : <>
        <div className="reference-overview"><div><Database size={18} /><strong>{summary?.saved ?? '—'}</strong><span>份{phaseLabels[phase]}档案</span></div><div><CheckCircle2 size={18} /><strong>{summary?.settled ?? '—'}</strong><span>场已结算</span></div><div><ShieldCheck size={18} /><strong>{percent(summary?.accuracy)}</strong><span>最高概率结果命中率</span></div></div>
        <p className="reference-footnote">各时点独立统计，每场计一次；手动选择的样本单独统计。只按90分钟赛果结算，延期／取消／开赛时间变化作废。没有历史补写的预测，也没有盈利承诺。</p>
        {performance?.attempts?.length > 0 && <p className="reference-footnote">最近7天全部时点留档尝试：{performance.attempts.map((a) => `${({ saved: '成功', already_saved: '已留档', window_elapsed: '窗口已过', ...statusLabels })[a.status] || a.status} ${a.count}次`).join('；')}。未通过检查的尝试不计入命中率。</p>}
        {summary && <section className="reference-score-panel"><h3>{summary.settled ? '当前样本成绩' : '成绩单从现在开始'}</h3><p>{summary.settled < 500 ? '样本仍在积累。500场仅是重新评估的工作节点，不能保证结论可靠。' : '已达到首轮评估节点，需要结合联赛分层、置信区间和概率校准判断。'}</p>
          <div className="reference-metrics"><span>待结算 <b>{summary.pending}</b></span><span>作废 <b>{summary.void}</b></span><span>Brier <b>{summary.brier?.toFixed(4) ?? '—'}</b></span><span>Log Loss <b>{summary.log_loss?.toFixed(4) ?? '—'}</b></span><span>命中率95%区间 <b>{summary.accuracy_interval_95?.map(percent).join(' ～ ') || '—'}</b></span></div>
          <p>分数越低，概率与结果越吻合。置信区间是未校正比赛间相关性的近似区间；不能直接作为投注收益判断。</p>
        </section>}
        {performance?.leagues?.length > 0 && <section className="reference-score-panel"><h3>按赛事统计</h3><div className="reference-table"><table><thead><tr><th>赛事</th><th>结算</th><th>命中率</th><th>Brier</th></tr></thead><tbody>{performance.leagues.map((r) => <tr key={r.competition}><td>{r.competition}</td><td>{r.settled}</td><td>{percent(r.accuracy)}</td><td>{r.brier?.toFixed(4) ?? '—'}</td></tr>)}</tbody></table></div></section>}
        {summary && <section className="reference-score-panel"><h3>概率校准</h3><p>例如标为60%～70%的结果，实际发生比例是否接近相应概率。</p><div className="reference-table"><table><thead><tr><th>概率区间</th><th>结果样本数</th><th>平均预测</th><th>实际发生</th></tr></thead><tbody>{summary.calibration.map((b) => <tr key={b.from}><td>{percent(b.from)}～{percent(b.to)}</td><td>{b.count}</td><td>{percent(b.predicted)}</td><td>{percent(b.observed)}</td></tr>)}</tbody></table></div></section>}
        <section className="reference-score-panel"><h3>最近留档</h3><div className="reference-table"><table><thead><tr><th>比赛</th><th>留档时间</th><th>主／平／客</th><th>赛果</th><th>原始依据</th></tr></thead><tbody>{performance?.recent.map((r) => <tr key={r.reference_id}><td>{r.home_team} — {r.away_team}</td><td>{date(r.created_at_utc)}</td><td>{r.probabilities.map(percent).join(' / ')}</td><td>{r.outcome === 'void' ? '作废' : r.outcome ? `${r.home_goals}:${r.away_goals}` : '待结算'}</td><td><a href={`/api/reference/archives/${r.reference_id}`} target="_blank" rel="noreferrer">档案 #{r.reference_id}</a></td></tr>)}</tbody></table></div>{!performance?.recent.length && <p>尚无这个时点的有效档案。没有用历史比赛填充成绩。</p>}</section>
      </>}
    </div>
  </div>
}
