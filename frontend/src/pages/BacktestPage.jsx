import { useCallback, useEffect, useState } from 'react'
import { Activity, CircleAlert, CircleCheck, Database, RefreshCw, ShieldCheck } from 'lucide-react'
import { Topbar } from '../components/Topbar'
import { CalibrationChart, EvaluationPathChart } from '../components/Charts'

function metric(value, digits = 4) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '—'
}

function dateRange(scope) {
  if (!scope?.test_start_on || !scope?.test_end_on) return '测试区间待确认'
  return `${scope.test_start_on} 至 ${scope.test_end_on}`
}

export function BacktestPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  const loadBacktest = useCallback(async (signal) => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch('/api/research/backtests/latest', { signal })
      if (!response.ok) throw new Error(`回测接口返回 ${response.status}`)
      const payload = await response.json()
      if (payload.data_status !== 'real_historical_backtest') {
        throw new Error('接口未返回可核验的真实历史回测')
      }
      setData(payload)
    } catch (requestError) {
      if (requestError.name !== 'AbortError') {
        setError(requestError.message || '无法读取真实历史回测')
      }
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => loadBacktest(controller.signal), 0)
    return () => {
      window.clearTimeout(timeoutId)
      controller.abort()
    }
  }, [loadBacktest, reloadToken])

  const ready = Boolean(
    data
    && data.status
    && data.headline
    && data.scope
    && Number(data.headline.sample_matches) > 0,
  )
  const headline = data?.headline || {}
  const scope = data?.scope || {}
  const safety = data?.safety || {}
  const leagues = data?.leagues || []
  const provenance = data?.provenance || {}
  const modelBeatsMarket = (
    Number.isFinite(Number(headline.brier))
    && Number.isFinite(Number(headline.market_brier))
    && Number(headline.brier) < Number(headline.market_brier)
  )

  return (
    <div className="page standard-page">
      <Topbar title="样本外回测" eyebrow="真实 walk-forward 概率回测" />
      <div className="standard-content">
        <div className="research-live-notice">
          <ShieldCheck size={17} />
          <div>
            <strong>真实 walk-forward 概率回测</strong>
            <span>
              每个测试窗口只使用此前已经发生的比赛训练；当前仅评价概率质量，不使用缺乏可信采集时间的赔率生成收益结论。
            </span>
          </div>
        </div>

        <header className="content-intro compact-intro backtest-intro">
          <div>
            <span className="eyebrow">
              {data?.model ? `${data.model.name} · ${data.model.version}` : '等待模型运行记录'}
            </span>
            <h2>概率质量先于收益曲线</h2>
            <p>
              {ready
                ? `${dateRange(scope)} · ${scope.matches} 场严格时间外样本`
                : '只有真实回测运行完成并保存后，页面才会展示结果。'}
            </p>
          </div>
          <div className="backtest-heading-actions">
            {ready && safety.probability_metrics_valid && (
              <div className="verified-chip"><CircleCheck size={16} /> 概率指标有效</div>
            )}
            <button
              className="secondary-action"
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
            <strong>正在读取真实回测记录</strong>
            <p>核对模型版本、时间切分和概率指标。</p>
          </div>
        )}

        {error && (
          <div className="research-state error">
            <CircleAlert size={22} />
            <strong>真实回测暂时不可用</strong>
            <p>{error}</p>
            <button className="secondary-action" onClick={() => setReloadToken((value) => value + 1)}>重新读取</button>
          </div>
        )}

        {!loading && !error && !ready && (
          <div className="research-state">
            <Database size={23} />
            <strong>尚无已完成的真实回测</strong>
            <p>页面不会用静态样例填充 ROI、CLV、回撤或概率曲线。</p>
          </div>
        )}

        {!error && ready && (
          <div className={loading ? 'research-results refreshing' : 'research-results'}>
            <div className="kpi-strip backtest-kpis">
              <div><span><Activity size={15} /> Brier Score</span><strong>{metric(headline.brier)}</strong><small>越低越好</small></div>
              <div><span>Log Loss</span><strong>{metric(headline.log_loss)}</strong><small>概率惩罚</small></div>
              <div><span>ECE</span><strong>{metric(headline.ece)}</strong><small>校准误差</small></div>
              <div className="unavailable"><span>ROI</span><strong>不可计算</strong><small>无安全赛前赔率</small></div>
              <div className="unavailable"><span>CLV</span><strong>不可计算</strong><small>无时间戳价格路径</small></div>
              <div className="unavailable"><span>最大回撤</span><strong>不可计算</strong><small>未生成资金曲线</small></div>
            </div>

            <div className="backtest-safety-note">
              <CircleAlert size={17} />
              <div>
                <strong>收益指标安全闸已关闭</strong>
                <span>{safety.reason || '当前赔率不能证明为决策时点可见，因此不计算 ROI、CLV 与回撤。'}</span>
              </div>
            </div>

            <div className={`backtest-comparison ${modelBeatsMarket ? 'better' : 'behind'}`}>
              {modelBeatsMarket ? <CircleCheck size={17} /> : <CircleAlert size={17} />}
              <div>
                <strong>{modelBeatsMarket ? '模型当前优于封盘市场概率基准' : '首版模型当前仍落后于封盘市场基准'}</strong>
                <span>
                  模型 Brier {metric(headline.brier)}，市场 Brier {metric(headline.market_brier)}。
                  {modelBeatsMarket ? '仍需更多赛季稳定性验证。' : '该结果会原样保留，下一版需要继续改进特征和模型。'}
                </span>
              </div>
            </div>

            <div className="research-limitations">
              <strong>赛果核验</strong>
              <span>
                {(provenance.single_source_results ?? 0) === 0
                  ? `当前测试集 ${provenance.cross_source_verified_results ?? scope.matches} 场均已完成跨来源赛果核验。`
                  : `当前测试集 ${provenance.single_source_results} 场为哈希固定的单一来源赛果，${provenance.cross_source_verified_results ?? 0} 场已完成跨来源核验；该限制必须保留。`}
                模型结果带输入、赔率、实现与预测内容指纹，可重复审查。
              </span>
            </div>

            <div className="backtest-grid">
              <section className="chart-surface">
                <div className="section-heading">
                  <div><span>时间外评估路径</span><h3>模型与市场 Brier</h3></div>
                  <small>{headline.sample_matches} 场样本</small>
                </div>
                <EvaluationPathChart values={data.evaluation_path || []} />
              </section>
              <section className="chart-surface">
                <div className="section-heading">
                  <div><span>概率可信度</span><h3>校准曲线</h3></div>
                  <small>预测概率 vs 实际频率</small>
                </div>
                <CalibrationChart values={data.calibration || []} />
              </section>
            </div>

            <section className="backtest-provenance">
              <div><span>训练区间</span><strong>{scope.train_start_on} → {scope.train_end_on}</strong></div>
              <div><span>测试区间</span><strong>{scope.test_start_on} → {scope.test_end_on}</strong></div>
              <div><span>模型 Brier</span><strong>{metric(headline.brier)}</strong></div>
              <div><span>市场 Brier</span><strong>{metric(headline.market_brier)}</strong></div>
              <div><span>市场 Log Loss</span><strong>{metric(headline.market_log_loss)}</strong></div>
              <div><span>预测记录</span><strong>{provenance.persisted_predictions ?? scope.matches} 条已保存</strong></div>
            </section>

            <section className="league-performance">
              <div className="section-heading">
                <div><span>分层稳定性</span><h3>联赛概率表现</h3></div>
                <small>真实时间外样本</small>
              </div>
              <div className="data-table league-table real-backtest-table">
                <div className="data-row table-head">
                  <span>联赛</span><span>样本</span><span>Brier</span><span>市场 Brier</span><span>Log Loss</span><span>ECE</span>
                </div>
                {leagues.map((league) => (
                  <div className="data-row" key={league.code}>
                    <strong>{league.name}</strong>
                    <span>{league.matches}</span>
                    <span className={Number(league.brier) < Number(league.market_brier) ? 'probability-metric better' : 'probability-metric behind'}>{metric(league.brier)}</span>
                    <span>{metric(league.market_brier)}</span>
                    <span>{metric(league.log_loss)}</span>
                    <span>{metric(league.ece)}</span>
                  </div>
                ))}
              </div>
              {!leagues.length && <div className="inline-empty-state">当前回测没有可展示的联赛分层。</div>}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
