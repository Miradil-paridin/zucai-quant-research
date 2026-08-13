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

const collectorStatusNames = {
  ok: '正常',
  partial: '部分成功',
  skipped: '已跳过',
  db_backoff: '数据库退避',
  db_unavailable: '数据库不可用',
  blocked: '来源拦截',
  failed: '失败',
  failure_backoff: '失败后退避',
  error: '异常',
}

const collectorReasonNames = {
  collector_busy: '已有采集进程正在运行',
  monthly_budget_exhausted: '本月请求预算已用尽',
  batch_period_elapsed: '采集跨月，等待新批次',
  provider_quota_reserve: '已到数据源额度保留线',
  provider_quota_unknown: '数据源剩余额度无法确认',
  provider_rejected_requests: '数据源拒绝请求',
  provider_response_error: '数据源响应异常',
  provider_request_error: '数据源请求失败',
  persistence_error: '数据写入失败',
  no_mapped_sports: '暂无可映射的在售联赛',
}

function localizeCollectorReason(reason) {
  if (!reason) return ''
  const key = reason.split('(', 1)[0]
  if (key === 'failure_backoff') return '采集失败，正在退避'
  if (key === 'previous_run_incomplete') return '上次采集未正常结束'
  if (key === 'recent_success') return '距离最近成功采集尚短'
  return collectorReasonNames[key] || reason
}

function collectorDetail(collector) {
  const details = []
  if (collector.next_db_probe_at_utc) {
    const nextProbe = new Date(collector.next_db_probe_at_utc)
    details.push(
      nextProbe > new Date()
        ? `下次数据库探测 ${timeFormatter.format(nextProbe)}`
        : '数据库退避已结束，可再次探测',
    )
  }
  if (collector.last_reason) details.push(localizeCollectorReason(collector.last_reason))
  if (collector.last_error) details.push(collector.last_error)
  if (!details.length && collector.last_success_at_utc) {
    details.push(`最近成功 ${timeFormatter.format(new Date(collector.last_success_at_utc))}`)
  }
  return details.join(' · ') || '尚无成功记录'
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
    const intervalId = window.setInterval(loadStatus, 60_000)
    return () => {
      window.clearTimeout(timeoutId)
      window.clearInterval(intervalId)
    }
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
  const storageAvailable = data?.storage?.available === true
  const historicalAvailable = data?.historical?.available === true
  const successRate = storageAvailable && batches.length
    ? Math.round(successful / batches.length * 100)
    : null
  const nextRun = data?.scheduler?.next_run_time
  const databaseAvailable = data?.database?.status === 'ok'
  const collectorRunning = Boolean(data?.scheduler?.collector_running)
  const jingcaiCollector = data?.odds_collectors?.jingcai || {}
  const consensusCollector = data?.odds_collectors?.consensus || {}
  const databaseMessage = data && !databaseAvailable ? data?.database?.message || 'PostgreSQL 连接不可用' : ''
  const storageMessage = data && databaseAvailable && !storageAvailable
    ? data?.storage?.error || '采集统计暂不可用'
    : ''
  const headline = error
    ? '采集链路需要检查'
    : !data
      ? '正在读取采集状态'
      : !databaseAvailable
      ? 'PostgreSQL 不可用，采集无法执行'
      : !storageAvailable
        ? 'PostgreSQL 已连接，采集统计需要检查'
      : collectorRunning
        ? '当前正在采集数据'
        : data?.scheduler?.enabled
          ? 'PostgreSQL 已连接，定时调度已启用'
          : data?.scheduler?.configured
            ? 'PostgreSQL 已连接，重启后端可恢复定时调度'
            : 'PostgreSQL 已连接，定时调度未配置'
  const statusEyebrow = !data
    ? '正在连接数据服务'
    : !databaseAvailable
    ? '数据库连接需要恢复'
    : !storageAvailable
      ? '数据库统计查询需要恢复'
    : collectorRunning
      ? '采集任务正在执行'
      : nextRun
        ? `下次自动采集 ${timeFormatter.format(new Date(nextRun))}`
        : '等待调度信息'

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
            <span className="eyebrow">{statusEyebrow} · 顶部调度状态仅指 API-Football / football-data.org 进程内任务</span>
            <h2>{headline}</h2>
            {(error || databaseMessage || storageMessage) && <p className="ops-error-text">{error || databaseMessage || storageMessage}</p>}
          </div>
          <button className="secondary-action" onClick={runCollection} disabled={running || collectorRunning || !databaseAvailable || !storageAvailable}>
            <RefreshCw size={16} className={running ? 'spinning' : ''} /> {running ? '正在采集' : '立即采集'}
          </button>
        </header>

        <div className="ops-summary collector-summary">
          <div><RefreshCw size={18} /><span>竞彩脚本最近状态</span><strong>{collectorStatusNames[jingcaiCollector.last_status] || '尚无状态'}</strong><small>{collectorDetail(jingcaiCollector)}</small></div>
          <div><RefreshCw size={18} /><span>共识脚本最近状态</span><strong>{collectorStatusNames[consensusCollector.last_status] || '尚无状态'}</strong><small>{collectorDetail(consensusCollector)}</small></div>
        </div>

        <div className="ops-summary">
          <div><Database size={18} /><span>原始响应</span><strong>{storageAvailable ? (counts.raw_payloads ?? 0) : '—'}</strong><small>{storageAvailable ? '不可变 API payload' : '统计不可用'}</small></div>
          <div><Check size={18} /><span>标准比赛</span><strong>{storageAvailable ? (counts.matches ?? 0) : '—'}</strong><small>{storageAvailable ? `${counts.teams ?? 0} 支球队` : '统计不可用'}</small></div>
          <div>
            <RefreshCw size={18} />
            <span>历史数据底座</span>
            <strong>{historicalAvailable ? (historical.matches ?? counts.historical_matches ?? 0) : '—'}</strong>
            <small>
              {historicalAvailable ? `${historical.verified_matches ?? 0} 场已交叉核验 · ${historical.odds ?? counts.historical_odds ?? 0} 条历史赔率` : '历史统计不可用'}
            </small>
          </div>
          <div className={successRate !== null && successRate < 90 ? 'warning' : ''}><CircleAlert size={18} /><span>任务成功率</span><strong>{successRate === null ? '—' : `${successRate}%`}</strong><small>{storageAvailable ? `最近 ${batches.length} 个批次` : '统计不可用'}</small></div>
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
            {!latestByResource.length && <div className="ops-empty-state">{storageAvailable ? '尚无采集记录，点击“立即采集”建立第一批数据。' : '采集记录暂不可用。'}</div>}
          </div>
        </section>
      </div>
    </div>
  )
}
