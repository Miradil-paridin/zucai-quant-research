import { useEffect, useState } from 'react'
import { CircleAlert, Database, ShieldCheck } from 'lucide-react'
import { Topbar } from '../components/Topbar'

export function PlayersPage() {
  const [counts, setCounts] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/ingestion/status', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`采集状态接口返回 ${response.status}`)
        return response.json()
      })
      .then((payload) => setCounts(payload.storage?.counts || {}))
      .catch((requestError) => {
        if (requestError.name !== 'AbortError') setError(requestError.message)
      })
    return () => controller.abort()
  }, [])

  const playerStats = counts?.player_match_stats ?? 0
  const lineups = counts?.lineup_snapshots ?? 0
  const events = counts?.events ?? 0
  const ready = playerStats > 0 && lineups > 0

  return (
    <div className="page standard-page">
      <Topbar title="球员评分" eyebrow="真实数据开放状态" />
      <div className="standard-content">
        <div className="research-status-notice">
          <CircleAlert size={17} />
          <div>
            <strong>球员评分尚未开放，页面不再展示静态示例数字</strong>
            <span>历史比赛底座没有足够的球员级阵容、出场时间和事件数据，因此现在计算评分会产生伪精度。</span>
          </div>
        </div>

        <header className="content-intro">
          <div>
            <span className="eyebrow">位置分桶 · 近 5 / 10 / 30 场</span>
            <h2>等待真实球员事件链路</h2>
            <p>达到最小数据门槛后，系统才会按位置生成赛后评分、滚动状态、稳定性和样本置信度。</p>
          </div>
        </header>

        {error && <p className="ops-error-text">{error}</p>}

        <section className="player-readiness-grid">
          <article className={playerStats > 0 ? 'ready' : ''}>
            <Database size={19} />
            <span>球员比赛统计</span>
            <strong>{counts ? playerStats : '—'}</strong>
            <small>分钟、位置与基础表现</small>
          </article>
          <article className={lineups > 0 ? 'ready' : ''}>
            <Database size={19} />
            <span>阵容快照</span>
            <strong>{counts ? lineups : '—'}</strong>
            <small>首发、替补与位置</small>
          </article>
          <article className={events > 0 ? 'ready' : ''}>
            <Database size={19} />
            <span>比赛事件</span>
            <strong>{counts ? events : '—'}</strong>
            <small>进球、助攻、牌与换人</small>
          </article>
          <article className={ready ? 'ready' : ''}>
            <ShieldCheck size={19} />
            <span>评分引擎</span>
            <strong>{ready ? '可启动' : '已阻止'}</strong>
            <small>{ready ? '满足首版输入门槛' : '不使用缺失数据生成分数'}</small>
          </article>
        </section>

        <section className="player-gate">
          <h3>开放条件</h3>
          <div><i className={playerStats > 0 ? 'pass' : ''} />连续保存真实出场时间与位置</div>
          <div><i className={lineups > 0 ? 'pass' : ''} />阵容与球员身份映射无冲突</div>
          <div><i className={events > 0 ? 'pass' : ''} />事件和比赛统计完成赛后校验</div>
          <p>在这些条件满足以前，球员页面只显示真实采集状态，不输出任何评分。</p>
        </section>
      </div>
    </div>
  )
}
