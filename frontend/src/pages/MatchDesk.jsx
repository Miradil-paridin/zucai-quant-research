import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  History,
  Info,
  MapPin,
  RefreshCw,
  Server,
  ShieldCheck,
} from 'lucide-react'
import { Topbar } from '../components/Topbar'

const HISTORICAL_COMPETITIONS = [
  { code: '', name: '全部历史联赛' },
  { code: 'PL', name: '英超' },
  { code: 'PD', name: '西甲' },
  { code: 'BL1', name: '德甲' },
  { code: 'SA', name: '意甲' },
  { code: 'FL1', name: '法甲' },
  { code: 'SE1', name: '瑞超' },
  { code: 'NO1', name: '挪超' },
  { code: 'FI1', name: '芬超' },
]

const shanghaiListDateTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const shanghaiDateTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: 'long',
  day: 'numeric',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

const statusLabels = {
  scheduled: '未开始',
  live: '进行中',
  finished: '已结束',
  postponed: '已延期',
  cancelled: '已取消',
}

const resultStatusLabels = {
  verified_normal: '跨来源核验',
  awarded: '官方判定赛果',
  abandoned: '中止比赛',
  unverified: '单一来源待复核',
}

const statDefinitions = [
  ['射门', 'HS', 'AS'],
  ['射正', 'HST', 'AST'],
  ['角球', 'HC', 'AC'],
  ['犯规', 'HF', 'AF'],
  ['黄牌', 'HY', 'AY'],
  ['红牌', 'HR', 'AR'],
]

function TeamMark({ team, secondary = false }) {
  const [failed, setFailed] = useState(false)
  if (team.crest && !failed) {
    return <img className="team-crest" src={team.crest} alt="" onError={() => setFailed(true)} />
  }
  return <span className={`team-monogram ${secondary ? 'secondary' : ''}`}>{team.name.slice(0, 1)}</span>
}

function TeamName({ team }) {
  const translated = team.name_original && team.name_original !== team.name
  return <div className="localized-team-name"><h2>{team.name}</h2>{translated && <small>{team.name_original}</small>}</div>
}

function FixtureRow({ match, active, onClick }) {
  const score = match.score.home == null ? statusLabels[match.status] : `${match.score.home} : ${match.score.away}`
  return (
    <button className={`match-row ${active ? 'active' : ''}`} onClick={onClick}>
      <div className="match-row-top">
        <span className="league-tag">{match.competition}</span>
        <span>{shanghaiListDateTime.format(new Date(match.kickoff_utc))}</span>
      </div>
      <div className="match-teams">
        <strong>{match.home_team.short_name}</strong><span>主</span>
        <strong>{match.away_team.short_name}</strong><span>客</span>
      </div>
      <div className="match-row-bottom">
        <span>{match.round || match.country || '正式赛程'}</span>
        <b>{score}</b>
      </div>
    </button>
  )
}

function HistoricalRow({ match, active, onClick }) {
  const verification = resultStatusLabels[match.result_status] || match.result_status
  return (
    <button className={`match-row historical-row ${active ? 'active' : ''}`} onClick={onClick}>
      <div className="match-row-top">
        <span className="league-tag">{match.competition}</span>
        <span>{match.match_date}</span>
      </div>
      <div className="match-teams">
        <strong>{match.home_team.name}</strong><span>主</span>
        <strong>{match.away_team.name}</strong><span>客</span>
      </div>
      <div className="match-row-bottom">
        <span className={`verification-label ${match.result_status}`}>{verification}</span>
        <b>{match.score.home} : {match.score.away}</b>
      </div>
    </button>
  )
}

function ProviderPill({ name, provider }) {
  const healthy = provider?.status === 'ok'
  return (
    <div className={`provider-pill ${healthy ? 'healthy' : 'warning'}`}>
      {healthy ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />}
      <span>{name}</span>
      <b>{healthy ? `${provider.count} 场` : '暂不可用'}</b>
    </div>
  )
}

function sourceLocalTime(match) {
  const time = match.match_time_local ? String(match.match_time_local).slice(0, 5) : '时间未记录'
  const timezone = !match.source_timezone || match.source_timezone === 'unknown'
    ? '时区未知，不转换'
    : match.source_timezone
  return `${match.match_date} ${time} · 来源本地时间 · ${timezone}`
}

function formatOdd(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(2) : '—'
}

function OddsLine({ label, odds }) {
  return (
    <div className="historical-odds-line">
      <span>{label}</span>
      {odds ? (
        <div>
          <i>主 <b>{formatOdd(odds.home)}</b></i>
          <i>平 <b>{formatOdd(odds.draw)}</b></i>
          <i>客 <b>{formatOdd(odds.away)}</b></i>
        </div>
      ) : <em>该场未提供</em>}
    </div>
  )
}

function FixtureDetails({ match }) {
  return (
    <article className="match-analysis fixture-details">
      <header className="match-hero">
        <div className="match-meta-line">
          <span>{match.country || '国际赛事'}</span><i />
          <span>{match.competition}</span><i />
          <span>{match.round || '赛程'}</span>
        </div>
        <div className="match-title-line">
          <div className="team-title"><TeamMark team={match.home_team} /><TeamName team={match.home_team} /></div>
          <div className="versus"><span>{statusLabels[match.status]}</span><strong>{match.score.home == null ? 'VS' : `${match.score.home}:${match.score.away}`}</strong></div>
          <div className="team-title away"><TeamName team={match.away_team} /><TeamMark team={match.away_team} secondary /></div>
        </div>
        <div className="hero-status">
          <div><Clock3 size={14} /> <b>{shanghaiDateTime.format(new Date(match.kickoff_utc))}</b></div>
          <div><MapPin size={14} /> <b>{[match.venue, match.city].filter(Boolean).join(' · ') || '场地待公布'}</b></div>
          <div><Database size={14} /> 数据源 <b>{match.source}</b></div>
        </div>
      </header>

      <div className="fixture-overview-grid">
        <section className="fixture-card data-card">
          <div className="fixture-card-heading"><Server size={18} /><div><span>DATA PROVENANCE</span><h3>数据来源</h3></div></div>
          <dl>
            <div><dt>统一比赛 ID</dt><dd>{match.fixture_id}</dd></div>
            <div><dt>API 原始 ID</dt><dd>{Object.values(match.provider_ids).join(' / ') || '未提供'}</dd></div>
            <div><dt>比赛状态</dt><dd className={match.status === 'live' ? 'live-text' : ''}>{statusLabels[match.status]}</dd></div>
            <div><dt>时区转换</dt><dd>UTC → Asia/Shanghai</dd></div>
          </dl>
        </section>

        <section className="fixture-card research-card">
          <div className="fixture-card-heading"><ShieldCheck size={18} /><div><span>RESEARCH READINESS</span><h3>研究就绪度</h3></div></div>
          <div className="readiness-item ready"><CheckCircle2 size={16} /><span>真实赛程与球队身份</span><b>已接入</b></div>
          <div className="readiness-item"><CircleAlert size={16} /><span>赛前赔率时间安全性</span><b>待验证</b></div>
          <div className="readiness-item"><CircleAlert size={16} /><span>阵容、伤停与球员特征</span><b>尚未接入</b></div>
          <div className="readiness-item"><CircleAlert size={16} /><span>模型概率与价值差</span><b>禁止伪造</b></div>
        </section>
      </div>

      <section className="honesty-banner">
        <ShieldCheck size={19} />
        <div><strong>当前展示的比赛是真实赛程</strong><p>在赔率时间、阵容和模型管线完成以前，系统不会生成假的概率、价值差或投注结论。</p></div>
      </section>
    </article>
  )
}

function HistoricalDetails({ match }) {
  const statistics = match.statistics || {}
  const availableStats = statDefinitions.filter(([, homeKey, awayKey]) => (
    statistics[homeKey] != null || statistics[awayKey] != null
  ))
  const halfTimeAvailable = match.score.half_time_home != null && match.score.half_time_away != null
  const qualityFlags = Array.isArray(match.quality_flags) ? match.quality_flags : []
  const verification = resultStatusLabels[match.result_status] || match.result_status

  return (
    <article className="match-analysis fixture-details historical-details" data-real-historical="true">
      <header className="match-hero">
        <div className="match-meta-line">
          <span>{match.country}</span><i />
          <span>{match.competition}</span><i />
          <span>{match.season_label}</span>
        </div>
        <div className="match-title-line">
          <div className="team-title"><TeamMark team={match.home_team} /><TeamName team={match.home_team} /></div>
          <div className="versus">
            <span>已结束</span>
            <strong>{match.score.home}:{match.score.away}</strong>
            {halfTimeAvailable && <small>半场 {match.score.half_time_home}:{match.score.half_time_away}</small>}
          </div>
          <div className="team-title away"><TeamName team={match.away_team} /><TeamMark team={match.away_team} secondary /></div>
        </div>
        <div className="hero-status historical-hero-status">
          <div className="historical-time-source"><Clock3 size={14} /> <b>{sourceLocalTime(match)}</b></div>
          <div><ShieldCheck size={14} /> 核验 <b className={`verification-text ${match.result_status}`}>{verification}</b></div>
          <div><Database size={14} /> 数据源 <b>football-data.co.uk</b></div>
        </div>
      </header>

      <div className="fixture-overview-grid historical-overview-grid">
        <section className="fixture-card data-card">
          <div className="fixture-card-heading"><Server size={18} /><div><span>TRACEABLE SOURCE ROW</span><h3>历史数据来源</h3></div></div>
          <dl>
            <div><dt>历史比赛 ID</dt><dd className="historical-match-id">{match.historical_match_id}</dd></div>
            <div><dt>联赛 / 赛季</dt><dd>{match.competition_code} / {match.season_label}</dd></div>
            <div><dt>赛果状态</dt><dd className={`verification-text ${match.result_status}`}>{verification}</dd></div>
            <div><dt>原始时间</dt><dd>{sourceLocalTime(match)}</dd></div>
            {statistics.Referee && <div><dt>裁判</dt><dd>{statistics.Referee}</dd></div>}
          </dl>
        </section>

        <section className="fixture-card historical-odds-card">
          <div className="fixture-card-heading"><History size={18} /><div><span>MARKETAVERAGE 1X2</span><h3>历史平均赔率</h3></div></div>
          <OddsLine label="开盘" odds={match.odds?.opening} />
          <OddsLine label="封盘" odds={match.odds?.closing} />
          <div className="odds-safety-flag">
            <CircleAlert size={14} />
            <span>无逐行采集时间戳，point_in_time_safe=false；不得用于严格交易回测或模拟当时可成交价格。</span>
          </div>
        </section>
      </div>

      <section className="historical-statistics">
        <div className="fixture-card-heading"><Database size={18} /><div><span>RECORDED MATCH FACTS</span><h3>比赛统计</h3></div></div>
        {availableStats.length ? (
          <div className="historical-stat-grid">
            {availableStats.map(([label, homeKey, awayKey]) => (
              <div key={label}>
                <b>{statistics[homeKey] ?? '—'}</b>
                <span>{label}</span>
                <b>{statistics[awayKey] ?? '—'}</b>
              </div>
            ))}
          </div>
        ) : (
          <p className="historical-no-stats">该来源文件没有记录本场技术统计，系统保持缺失，不进行推测。</p>
        )}
      </section>

      <section className="honesty-banner historical-honesty">
        <ShieldCheck size={19} />
        <div>
          <strong>当前展示的是可追溯的真实历史记录</strong>
          <p>
            比分、半场、统计与赔率均来自已保存的原始行。
            {qualityFlags.length
              ? ` 质量标记：${qualityFlags.map((flag) => typeof flag === 'string' ? flag : JSON.stringify(flag)).join('、')}。`
              : ' 本场没有额外质量标记。'}
          </p>
        </div>
      </section>
    </article>
  )
}

export function MatchDesk() {
  const [mode, setMode] = useState('fixtures')
  const [feed, setFeed] = useState(null)
  const [historyFeed, setHistoryFeed] = useState(null)
  const [fixtureLoading, setFixtureLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [fixtureError, setFixtureError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [selectedFixtureId, setSelectedFixtureId] = useState('')
  const [selectedHistoricalId, setSelectedHistoricalId] = useState('')
  const [fixtureCompetition, setFixtureCompetition] = useState('全部赛事')
  const [historicalCompetition, setHistoricalCompetition] = useState('')
  const [autoFallback, setAutoFallback] = useState(false)

  const loadFixtures = useCallback(async (signal) => {
    setFixtureLoading(true)
    setFixtureError('')
    try {
      const response = await fetch('/api/fixtures?days=7', { signal })
      if (!response.ok) throw new Error(`比赛接口返回 ${response.status}`)
      const payload = await response.json()
      setFeed(payload)
      setSelectedFixtureId((current) => payload.items.some((item) => item.fixture_id === current) ? current : payload.items[0]?.fixture_id || '')
      if (!payload.items.length) {
        setAutoFallback(true)
        setMode('history')
      }
    } catch (requestError) {
      if (requestError.name !== 'AbortError') setFixtureError(requestError.message || '无法读取真实比赛数据')
    } finally {
      if (!signal?.aborted) setFixtureLoading(false)
    }
  }, [])

  const loadHistory = useCallback(async (signal) => {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const params = new URLSearchParams({ limit: '50' })
      if (historicalCompetition) params.set('competition', historicalCompetition)
      const response = await fetch(`/api/historical/matches?${params}`, { signal })
      if (!response.ok) throw new Error(`历史比赛接口返回 ${response.status}`)
      const payload = await response.json()
      if (payload.data_status !== 'real_historical_rows') {
        throw new Error('接口未返回可追溯的真实历史比赛')
      }
      setHistoryFeed(payload)
      setSelectedHistoricalId((current) => (
        payload.items.some((item) => String(item.historical_match_id) === current)
          ? current
          : String(payload.items[0]?.historical_match_id || '')
      ))
    } catch (requestError) {
      if (requestError.name !== 'AbortError') setHistoryError(requestError.message || '无法读取真实历史比赛')
    } finally {
      if (!signal?.aborted) setHistoryLoading(false)
    }
  }, [historicalCompetition])

  useEffect(() => {
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => loadFixtures(controller.signal), 0)
    return () => {
      window.clearTimeout(timeoutId)
      controller.abort()
    }
  }, [loadFixtures])

  useEffect(() => {
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => loadHistory(controller.signal), 0)
    return () => {
      window.clearTimeout(timeoutId)
      controller.abort()
    }
  }, [loadHistory])

  const fixtureCompetitions = useMemo(
    () => ['全部赛事', ...Array.from(new Set(feed?.items.map((item) => item.competition) || [])).sort()],
    [feed],
  )
  const visibleFixtures = useMemo(
    () => (feed?.items || []).filter((item) => fixtureCompetition === '全部赛事' || item.competition === fixtureCompetition),
    [feed, fixtureCompetition],
  )
  const fixtureMatch = visibleFixtures.find((item) => item.fixture_id === selectedFixtureId) || visibleFixtures[0]
  const historicalMatches = historyFeed?.items || []
  const historicalMatch = historicalMatches.find(
    (item) => String(item.historical_match_id) === selectedHistoricalId,
  ) || historicalMatches[0]
  const isHistory = mode === 'history'
  const loading = isHistory ? historyLoading : fixtureLoading
  const error = isHistory ? historyError : fixtureError

  function selectMode(nextMode) {
    setMode(nextMode)
    setAutoFallback(false)
  }

  return (
    <div className="page match-page">
      <Topbar title="真实比赛中心" eyebrow={isHistory ? '可追溯历史数据' : '未来授权赛程'} />

      <section className="feed-toolbar desk-toolbar">
        <div className="desk-mode-switch" aria-label="比赛数据模式">
          <button data-mode="fixtures" className={!isHistory ? 'active' : ''} onClick={() => selectMode('fixtures')}>
            <CalendarDays size={14} /> 未来赛程
          </button>
          <button data-mode="history" className={isHistory ? 'active' : ''} onClick={() => selectMode('history')}>
            <History size={14} /> 历史数据
          </button>
        </div>

        <div className="desk-source-summary">
          <span className="live-source-dot" />
          <strong>{isHistory ? 'Football-Data 历史库' : '授权 API 数据'}</strong>
          <small>
            {isHistory
              ? `${historyFeed?.total ?? '—'} 场可查询 · 当前显示 ${historicalMatches.length} 场`
              : feed ? `${feed.items.length} 场 · PostgreSQL 已保存` : '正在连接'}
          </small>
        </div>

        {!isHistory && (
          <div className="provider-pills">
            <ProviderPill name="API-Football" provider={feed?.providers?.['api-football']} />
            <ProviderPill name="football-data.org" provider={feed?.providers?.['football-data.org']} />
          </div>
        )}
        {isHistory && (
          <div className="provider-pills historical-provider-pills">
            <div className="provider-pill healthy"><CheckCircle2 size={14} /><span>原始行可追溯</span><b>真实</b></div>
            <div className="provider-pill warning"><CircleAlert size={14} /><span>赔率时间安全</span><b>否</b></div>
          </div>
        )}

        <button
          className="refresh-button"
          onClick={() => isHistory ? loadHistory() : loadFixtures()}
          disabled={loading}
        >
          <RefreshCw size={15} className={loading ? 'spinning' : ''} /> 刷新
        </button>
      </section>

      {autoFallback && isHistory && (
        <div className="coverage-notice historical-fallback-notice">
          <Info size={14} /> 未来 7 天没有已保存赛程，已自动显示最近的真实历史比赛；这里不是即将开赛列表。
        </div>
      )}
      {!isHistory && feed?.providers?.['api-football']?.coverage === feed?.date_from && feed?.date_to !== feed?.date_from && (
        <div className="coverage-notice">
          <Info size={14} /> 免费 API-Football 当前只开放当日赛程；未来 7 天由 football-data.org 补充，休赛期可能没有五大联赛比赛。
        </div>
      )}

      <section className="match-workspace">
        <aside className="match-list-panel">
          <div className="panel-heading">
            {isHistory ? (
              <div><span>来源本地日期 · 不做未知时区转换</span><strong>{historicalMatches.length} / {historyFeed?.total ?? 0} 场</strong></div>
            ) : (
              <div><span>{feed ? `${feed.date_from} 至 ${feed.date_to}` : '未来 7 天'} · 上海时间</span><strong>{visibleFixtures.length} 场比赛</strong></div>
            )}
            {isHistory ? <History size={17} /> : <CalendarDays size={17} />}
          </div>

          {isHistory ? (
            <select className="fixture-filter-select" value={historicalCompetition} onChange={(event) => setHistoricalCompetition(event.target.value)}>
              {HISTORICAL_COMPETITIONS.map((item) => <option value={item.code} key={item.code || 'all'}>{item.name}</option>)}
            </select>
          ) : (
            <select className="fixture-filter-select" value={fixtureCompetition} onChange={(event) => setFixtureCompetition(event.target.value)}>
              {fixtureCompetitions.map((item) => <option key={item}>{item}</option>)}
            </select>
          )}

          {loading && <div className="fixture-loading"><span />正在读取{isHistory ? '历史' : '未来'}比赛</div>}
          {error && (
            <div className="fixture-error">
              <CircleAlert size={18} />
              <strong>真实数据暂时不可用</strong>
              <p>{error}。请确认后端已启动。</p>
            </div>
          )}
          {!loading && !error && isHistory && !historicalMatches.length && (
            <div className="fixture-error"><Info size={18} /><strong>没有历史比赛</strong><p>请选择其他联赛。</p></div>
          )}
          {!loading && !error && !isHistory && !visibleFixtures.length && (
            <div className="fixture-error"><Info size={18} /><strong>未来 7 天暂无比赛</strong><p>可切换到历史数据查看已入库赛果。</p></div>
          )}

          <div className="match-list">
            {isHistory
              ? historicalMatches.map((item) => (
                <HistoricalRow
                  key={item.historical_match_id}
                  match={item}
                  active={String(item.historical_match_id) === String(historicalMatch?.historical_match_id)}
                  onClick={() => setSelectedHistoricalId(String(item.historical_match_id))}
                />
              ))
              : visibleFixtures.map((item) => (
                <FixtureRow
                  key={item.fixture_id}
                  match={item}
                  active={item.fixture_id === fixtureMatch?.fixture_id}
                  onClick={() => setSelectedFixtureId(item.fixture_id)}
                />
              ))}
          </div>
          <div className="list-footnote">
            <Info size={14} /> {isHistory ? '比分、统计和赔率均来自已保存原始行' : '赛事名称与开赛时间均来自真实接口'}
          </div>
        </aside>

        {isHistory && historicalMatch && <HistoricalDetails match={historicalMatch} />}
        {!isHistory && fixtureMatch && <FixtureDetails match={fixtureMatch} />}
        {!loading && !error && ((isHistory && !historicalMatch) || (!isHistory && !fixtureMatch)) && (
          <div className="fixture-main-empty"><Database size={24} /><strong>当前范围没有真实比赛数据</strong></div>
        )}
      </section>
    </div>
  )
}
