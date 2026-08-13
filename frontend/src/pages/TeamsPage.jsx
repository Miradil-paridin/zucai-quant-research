import { useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, CircleAlert, Database, RefreshCw, ShieldCheck } from 'lucide-react'
import { Topbar } from '../components/Topbar'

const DEFAULT_COMPETITION = 'PL'
const DEFAULT_WINDOW = 20

function numeric(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function fixed(value, digits = 2) {
  return numeric(value).toFixed(digits)
}

function barWidth(value) {
  return `${Math.max(0, Math.min(100, numeric(value)))}%`
}

export function TeamsPage() {
  const [competition, setCompetition] = useState(DEFAULT_COMPETITION)
  const [windowMatches, setWindowMatches] = useState(DEFAULT_WINDOW)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  const loadTeams = useCallback(async (signal) => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({
        competition,
        window: String(windowMatches),
      })
      const response = await fetch(`/api/research/teams?${params}`, { signal })
      if (!response.ok) throw new Error(`球队研究接口返回 ${response.status}`)
      const payload = await response.json()
      if (payload.data_status !== 'real_historical_computed') {
        throw new Error('接口未返回可核验的真实历史计算结果')
      }
      setData(payload)
    } catch (requestError) {
      if (requestError.name !== 'AbortError') {
        setError(requestError.message || '无法读取球队历史统计')
      }
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [competition, windowMatches])

  useEffect(() => {
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => loadTeams(controller.signal), 0)
    return () => {
      window.clearTimeout(timeoutId)
      controller.abort()
    }
  }, [loadTeams, reloadToken])

  const teams = data?.items || []
  const competitions = data?.available_competitions || []

  return (
    <div className="page standard-page">
      <Topbar title="球队强度" eyebrow="真实历史赛果滚动基线" />
      <div className="standard-content">
        <div className="research-live-notice">
          <Database size={17} />
          <div>
            <strong>真实赛果滚动基线，不含阵容 / xG / 球员</strong>
            <span>
              所有指标由 PostgreSQL 中已保存的历史赛果按时间窗口计算，不代表完整预测模型，也不会补造缺失特征。
            </span>
          </div>
        </div>

        <header className="content-intro team-research-intro">
          <div>
            <span className="eyebrow">进攻 · 防守 · 积分效率 · 近期动量</span>
            <h2>{data?.competition_name || '球队滚动表现'}</h2>
            <p>
              截止 {data?.as_of_date || '最近已入库比赛'}，以每队最近 {data?.window_matches || windowMatches} 场正式赛果进行同联赛标准化。
              指数只描述已经发生的比赛，不包含未来信息。
            </p>
          </div>
          <div className="research-controls" aria-label="球队统计筛选">
            <label>
              <span>联赛</span>
              <select value={competition} onChange={(event) => setCompetition(event.target.value)}>
                {(competitions.length ? competitions : [{ code: competition, name: competition }]).map((item) => (
                  <option value={item.code} key={item.code}>{item.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>窗口</span>
              <select value={windowMatches} onChange={(event) => setWindowMatches(Number(event.target.value))}>
                <option value={10}>近 10 场</option>
                <option value={20}>近 20 场</option>
                <option value={30}>近 30 场</option>
              </select>
            </label>
            <button
              className="secondary-action research-refresh"
              onClick={() => setReloadToken((value) => value + 1)}
              disabled={loading}
            >
              <RefreshCw size={15} className={loading ? 'spinning' : ''} /> 刷新
            </button>
          </div>
        </header>

        {loading && !data && (
          <div className="research-state">
            <span className="state-spinner" />
            <strong>正在计算真实球队基线</strong>
            <p>读取历史比赛并按所选窗口聚合。</p>
          </div>
        )}

        {error && (
          <div className="research-state error">
            <CircleAlert size={22} />
            <strong>真实球队数据暂时不可用</strong>
            <p>{error}</p>
            <button className="secondary-action" onClick={() => setReloadToken((value) => value + 1)}>重新读取</button>
          </div>
        )}

        {!loading && !error && !teams.length && (
          <div className="research-state">
            <Database size={22} />
            <strong>当前筛选没有足够的历史比赛</strong>
            <p>请选择其他联赛或缩短滚动窗口。</p>
          </div>
        )}

        {!error && teams.length > 0 && (
          <>
            <div className="team-grid real-team-grid" aria-busy={loading}>
              {teams.map((team) => {
                const momentum = numeric(team.momentum)
                const translated = team.name_original && team.name_original !== team.name
                return (
                  <article className="team-card real-team-card" key={team.team_key}>
                    <header>
                      <span className="team-badge"><ShieldCheck size={19} /></span>
                      <div>
                        <h3>{team.name}</h3>
                        <p>{translated ? team.name_original : `${team.matches} 场真实赛果`}</p>
                      </div>
                      <strong>{fixed(team.overall_index, 1)}</strong>
                    </header>

                    <div className="team-record-line">
                      <span><b>{team.matches}</b> 场</span>
                      <span><b>{team.wins}</b> 胜</span>
                      <span><b>{team.draws}</b> 平</span>
                      <span><b>{team.losses}</b> 负</span>
                      <span><b>{fixed(team.goals_for_per_match)}</b> 进球</span>
                      <span><b>{fixed(team.goals_against_per_match)}</b> 失球</span>
                    </div>

                    <div className="team-metrics">
                      {[
                        ['进攻指数', numeric(team.attack_index) / 1.5, fixed(team.attack_index, 1)],
                        ['防守指数', numeric(team.defense_index) / 1.5, fixed(team.defense_index, 1)],
                        ['场均积分', numeric(team.points_per_match) / 3 * 100, fixed(team.points_per_match)],
                        ['近期积分', numeric(team.recent_points_per_match) / 3 * 100, fixed(team.recent_points_per_match)],
                      ].map(([label, width, display]) => (
                        <div key={label}>
                          <span>{label}</span>
                          <div><i style={{ width: barWidth(width) }} /></div>
                          <strong>{display}</strong>
                        </div>
                      ))}
                    </div>

                    <footer className={momentum >= 0 ? 'up' : 'down'}>
                      {momentum >= 0 ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
                      近期动量 {momentum >= 0 ? '+' : ''}{momentum.toFixed(2)}
                      <span className="team-confidence">样本置信度 {Math.round(numeric(team.confidence) * 100)}%</span>
                    </footer>
                  </article>
                )
              })}
            </div>

            {!!data?.limitations?.length && (
              <div className="research-limitations">
                <strong>口径限制</strong>
                <span>{data.limitations.join(' · ')}</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
