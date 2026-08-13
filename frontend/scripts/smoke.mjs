import fs from 'node:fs'
import puppeteer from 'puppeteer-core'

const candidates = [
  process.env.BROWSER_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  '/usr/bin/microsoft-edge',
  '/usr/bin/google-chrome',
].filter(Boolean)

const executablePath = candidates.find((candidate) => fs.existsSync(candidate))
if (!executablePath) throw new Error('No supported local browser found. Set BROWSER_PATH.')

const runtimeErrors = []
const baseUrl = process.env.APP_URL || 'http://127.0.0.1:5174'
const browser = await puppeteer.launch({ executablePath, headless: true, args: ['--disable-gpu'] })
const forbiddenDecisionLabels = ['示例数据', '暂不用于判断']

async function assertNoDecisionDemoLabel(page, route) {
  const bodyText = await page.$eval('body', (node) => node.textContent)
  const found = forbiddenDecisionLabels.find((label) => bodyText.includes(label))
  if (found) throw new Error(`${route} still renders forbidden decision-demo label: ${found}`)
}

try {
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 1024, deviceScaleFactor: 1 })
  page.on('pageerror', (error) => runtimeErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') runtimeErrors.push(message.text())
  })

  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.match-workspace')
  await page.click('[data-mode="history"]')
  await page.waitForSelector('.historical-details[data-real-historical="true"]', { timeout: 120000 })
  const historicalMatchId = await page.$eval('.historical-match-id', (node) => Number(node.textContent))
  if (!Number.isFinite(historicalMatchId) || historicalMatchId < 1) {
    throw new Error(`expected a real historical match id, found ${historicalMatchId}`)
  }
  const historicalTimeLabel = await page.$eval('.historical-time-source', (node) => node.textContent)
  if (!historicalTimeLabel.includes('来源本地时间')) {
    throw new Error(`historical time provenance missing: ${historicalTimeLabel}`)
  }
  await assertNoDecisionDemoLabel(page, '/')

  const historicalApiStatus = await page.evaluate(async () => {
    const response = await fetch('/api/historical/matches?limit=1')
    const payload = await response.json()
    return { http: response.status, dataStatus: payload.data_status, count: payload.items?.length || 0 }
  })
  if (historicalApiStatus.http !== 200 || historicalApiStatus.dataStatus !== 'real_historical_rows' || historicalApiStatus.count < 1) {
    throw new Error(`historical API is not serving real rows: ${JSON.stringify(historicalApiStatus)}`)
  }

  const faviconStatus = await page.evaluate(async () => (await fetch('/favicon.svg')).status)
  if (faviconStatus !== 200) throw new Error(`favicon returned ${faviconStatus}`)

  await page.goto(`${baseUrl}/teams`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.real-team-card', { timeout: 120000 })
  const realTeamCount = await page.$$eval('.real-team-card', (nodes) => nodes.length)
  if (realTeamCount < 1) throw new Error('expected at least one real historical team baseline')
  const teamApiStatus = await page.evaluate(async () => {
    const response = await fetch('/api/research/teams?competition=PL&window=20')
    const payload = await response.json()
    return { http: response.status, dataStatus: payload.data_status, count: payload.items?.length || 0 }
  })
  if (teamApiStatus.http !== 200 || teamApiStatus.dataStatus !== 'real_historical_computed' || teamApiStatus.count < 1) {
    throw new Error(`teams API is not serving real computations: ${JSON.stringify(teamApiStatus)}`)
  }
  await assertNoDecisionDemoLabel(page, '/teams')

  await page.goto(`${baseUrl}/backtest`, { waitUntil: 'networkidle0', timeout: 120000 })
  await page.waitForSelector('.research-results .backtest-grid canvas', { timeout: 120000 })
  const chartCount = await page.$$eval('.backtest-grid canvas', (nodes) => nodes.length)
  if (chartCount !== 2) throw new Error(`expected 2 backtest charts, found ${chartCount}`)
  const unavailableMetrics = await page.$$eval('.backtest-kpis .unavailable strong', (nodes) => (
    nodes.filter((node) => node.textContent.includes('不可计算')).length
  ))
  if (unavailableMetrics !== 3) throw new Error(`expected 3 unavailable trading metrics, found ${unavailableMetrics}`)
  const backtestApiStatus = await page.evaluate(async () => {
    const response = await fetch('/api/research/backtests/latest')
    const payload = await response.json()
    return {
      http: response.status,
      dataStatus: payload.data_status,
      samples: payload.headline?.sample_matches || 0,
      roiAvailable: payload.safety?.roi_available,
      clvAvailable: payload.safety?.clv_available,
    }
  })
  if (
    backtestApiStatus.http !== 200
    || backtestApiStatus.dataStatus !== 'real_historical_backtest'
    || backtestApiStatus.samples < 1
    || backtestApiStatus.roiAvailable !== false
    || backtestApiStatus.clvAvailable !== false
  ) {
    throw new Error(`backtest API safety contract failed: ${JSON.stringify(backtestApiStatus)}`)
  }
  await assertNoDecisionDemoLabel(page, '/backtest')

  await page.goto(`${baseUrl}/players`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.player-readiness-grid')
  await assertNoDecisionDemoLabel(page, '/players')

  if (runtimeErrors.length) throw new Error(`Browser runtime errors:\n${runtimeErrors.join('\n')}`)
  console.log(JSON.stringify({
    status: 'passed',
    faviconStatus,
    historicalMatchId,
    realTeamCount,
    chartCount,
    unavailableMetrics,
    runtimeErrors: 0,
  }))
} finally {
  await browser.close()
}
