import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, CircleAlert, Database, RefreshCw } from 'lucide-react'
import { Topbar } from '../components/Topbar'

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const resourceNames = {
  fixtures: '赛程主数据',
  injuries: '伤停快照',
  odds: '1X2 赔率快照',
  fixture_bundle: '赛中/赛后详情',
}

export function OpsPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)

  const loadStatus = useCallback(async () => {
    try {
      const [ingestionResponse, historicalResponse] = await Promise.all([
        fetch('/api/ingestion/status'),
        fetch('/api/historical/status'),
      ])
      if (!ingestionResponse.ok) throw new Error(`采集状态接口返回 ${ingestionResponse.status}`)
      if (!historicalResponse.ok) throw new Error(`历史状态接口返回 ${historicalResponse.status}`)
      const [ingestion, historical] = await Promise.all([
        ingestionResponse.json(),
        historicalResponse.json(),
      ])
      setData({ ...ingestion, historical })
      setError('')
    } catch (requestError) {
      setError(requestError.message || '无法读取采集状态')
    }
  }, [])

  useEffect(() => {
    const timeoutId = window.setTimeout(loadStatus, 0)
    return () => window.clearTimeout(timeoutId)
  }, [loadStatus])

  async function runCollection() {
    setRunning(true)
    setError('')
    try {
      const response = await fetch('/api/ingestion/run?days=7', { method: 'POST' })
      if (!response.ok) throw new Error(`采集任务返回 ${response.status}`)
      await loadStatus()
    } catch (requestError) {
      setError(requestError.message || '采集任务执行失败')
    } finally {
      setRunning(false)
    }
  }

  const counts = data?.storage?.counts || {}
  const historical = data?.historical?.totals || {}
  const batches = useMemo(() => data?.storage?.latest_batches || [], [data])
  const successful = batches.filter((batch) => batch.status === 'succeeded').length
  const successRate = batches.length ? Math.round(successful / batches.length * 100) : 0
  const nextRun = data?.scheduler?.next_run_time
  const headline = error
    ? '采集链路需要检查'
    : data?.scheduler?.enabled
      ? 'PostgreSQL 已连接，定时采集运行中'
      : '数据已持久化，定时任务未启动'

  const latestByResource = useMemo(() => {
    const seen = new Set()
    return batches.filter((batch) => {
      const key = `${batch.source_name}:${batch.resource_type}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [batches])

  return (
    <div className="page standard-page">
      <Topbar title="数据运维" eyebrow="真实采集、持久化与来源健康" />
      <div className="standard-content">
        <header className="content-intro compact-intro">
          <div>
            <span className="eyebrow">{nextRun ? `下次自动采集 ${timeFormatter.format(new Date(nextRun))}` : '等待调度信息'}</span>
            <h2>{headline}</h2>
            {error && <p className="ops-error-text">{error}</p>}
          </div>
          <button className="secondary-action" onClick={runCollection} disabled={running || data?.scheduler?.collector_running}>
            <RefreshCw size={16} className={running ? 'spinning' : ''} /> {running ? '正在采集' : '立即采集'}
          </button>
        </header>

        <div className="ops-summary">
          <div><Database size={18} /><span>原始响应</span><strong>{counts.raw_payloads ?? '—'}</strong><small>不可变 API payload</small></div>
          <div><Check size={18} /><span>标准比赛</span><strong>{counts.matches ?? '—'}</strong><small>{counts.teams ?? 0} 支球队</small></div>
          <div>
            <RefreshCw size={18} />
            <span>历史数据底座</span>
            <strong>{historical.matches ?? counts.historical_matches ?? '—'}</strong>
            <small>
              {historical.verified_matches ?? 0} 场已交叉核验 · {historical.odds ?? counts.historical_odds ?? 0} 条历史赔率
            </small>
          </div>
          <div className={successRate < 90 ? 'warning' : ''}><CircleAlert size={18} /><span>任务成功率</span><strong>{successRate}%</strong><small>最近 {batches.length} 个批次</small></div>
        </div>

        <section className="source-health">
          <div className="section-heading">
            <div><span>采集批次</span><h3>来源与最近写入</h3></div>
            <small>所有时间已转换为 Asia/Shanghai</small>
          </div>
          <div className="source-list">
            {latestByResource.map((batch) => {
              const healthy = batch.status === 'succeeded'
              return (
                <div className="source-row" key={`${batch.source_name}:${batch.resource_type}`}>
                  <span className={`source-status ${healthy ? '' : 'warning'}`} />
                  <div><strong>{resourceNames[batch.resource_type] || batch.resource_type}</strong><small>{batch.source_name}</small></div>
                  <div><span>最近完成</span><strong>{batch.finished_at_utc ? timeFormatter.format(new Date(batch.finished_at_utc)) : '运行中'}</strong></div>
                  <div><span>接口 / 记录</span><strong>{batch.request_count} / {batch.record_count}</strong></div>
                  <div className="health-line"><i style={{ width: healthy ? '100%' : '35%' }} /></div>
                  <span className={`status-label ${healthy ? '' : 'warning'}`}>{healthy ? '已保存' : '需检查'}</span>
                </div>
              )
            })}
            {!latestByResource.length && <div className="ops-empty-state">尚无采集记录，点击“立即采集”建立第一批数据。</div>}
          </div>
        </section>
      </div>
    </div>
  )
}
