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

  await page.goto(`${baseUrl}/ops`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.source-health .ops-summary')
  const repricingStatus = await page.evaluate(async () => {
    const response = await fetch('/api/research/repricing/latest')
    const payload = await response.json()
    return { http: response.status, available: payload.available, version: payload.result?.analysis_version }
  })
  if (repricingStatus.http !== 200 || !repricingStatus.available || repricingStatus.version !== 'prematch-observations-v2') {
    throw new Error(`Repricing report unavailable: ${JSON.stringify(repricingStatus)}`)
  }
  await page.screenshot({ path: '../output/runtime/ops-ucl-repair.png', fullPage: true })

  await page.goto(`${baseUrl}/players`, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.player-readiness-grid')
  await assertNoDecisionDemoLabel(page, '/players')

  await page.goto(`${baseUrl}/reference`, { waitUntil: 'networkidle0', timeout: 120000 })
  await page.waitForSelector('.reference-card', { timeout: 120000 })
  const referenceApi = await page.evaluate(async () => {
    const response = await fetch('/api/reference?days=30')
    const payload = await response.json()
    return { status: response.status, count: payload.items.length, ready: payload.items.filter((i) => i.ready_to_archive).length,
      valid: payload.items.every((i) => !i.market.probabilities || Math.abs(i.market.probabilities.reduce((a, b) => a + b, 0) - 1) < 0.000001) }
  })
  if (referenceApi.status !== 200 || !referenceApi.valid || referenceApi.count !== await page.$$eval('.reference-card', (cards) => cards.length)) {
    throw new Error(`Reference API/page mismatch: ${JSON.stringify(referenceApi)}`)
  }
  await page.screenshot({ path: '../output/runtime/reference-desktop.png' })
  const researched = await page.evaluate(async () => (await (await fetch('/api/reference?days=30')).json()).items.filter((i) => i.research))
  if (researched.length) {
    await page.goto(`${baseUrl}/reference?competition=${encodeURIComponent(researched[0].competition_code)}`, { waitUntil: 'networkidle0' })
    await page.waitForSelector('.prematch-research')
    const expected = researched.filter((i) => i.competition_code === researched[0].competition_code)
    if (await page.$$eval('.prematch-research', (nodes) => nodes.length) !== expected.length) throw new Error('Research/competition filter mismatch')
    for (const item of expected) {
      const cardText = await page.$eval(`[data-reference-match="${item.match_id}"]`, (node) => node.textContent)
      if (!cardText.includes(item.research.lean) || !cardText.includes('反面因素') || cardText.includes('近0场')) throw new Error('Research conclusion or linked form missing')
      if (item.research.probabilities !== null || item.research.kind !== 'qualitative_assessment') throw new Error('Research misrepresented as calibrated probability')
    }
    await page.click('.prematch-detail summary')
    if (!await page.$('.prematch-detail[open] .prematch-source-list a')) throw new Error('Research sources cannot be expanded')
    const archive = await page.evaluate(async (id) => (await (await fetch(`/api/reference/research/${id}`)).json()), expected[0].research.research_id)
    if (new Date(archive.created_at_utc) >= new Date(archive.kickoff_utc)) throw new Error('Research was not saved before kickoff')
    await page.click('.prematch-detail summary')
    await page.screenshot({ path: '../output/runtime/efl-prematch-desktop.png' })
  }
  await page.click('.reference-tabs button:nth-child(2)')
  await page.waitForSelector('.reference-score-panel', { timeout: 30000 })
  const scorecardApi = await page.evaluate(async () => {
    const response = await fetch('/api/reference/scorecard?phase=h1')
    return (await response.json()).summary
  })
  if (!scorecardApi.settled && scorecardApi.accuracy !== null) throw new Error('Empty reference ledger invented an accuracy')
  await page.screenshot({ path: '../output/runtime/reference-scorecard.png' })
  await page.click('.reference-tabs button:nth-child(3)')
  await page.waitForSelector('.context-card', { timeout: 30000 })
  const dossierApi = await page.evaluate(async () => (await fetch('/api/reference/dossiers')).json())
  const contextCount = await page.$$eval('.context-card', (cards) => cards.length)
  if (contextCount !== 18 || dossierApi.summary.starters !== 396 || dossierApi.items.some((i) => i.eligible_for_prediction_backtest)) {
    throw new Error(`Historical context coverage or time boundary failed: ${contextCount}`)
  }
  if (dossierApi.summary.matches_with_statistics !== 18 || dossierApi.summary.goals !== 69 || dossierApi.summary.substitutions !== 168 || dossierApi.summary.card_events !== 74) {
    throw new Error('Official post-match statistics/event coverage is incomplete')
  }
  const firstStats = await page.$eval('.context-card:first-child .postmatch-stat-panel', (node) => node.textContent)
  if (!firstStats.includes('射正') || !firstStats.includes('控球')) throw new Error('Verified match statistics are not visible')
  await page.select('.context-card:first-child .postmatch-panel-heading select', 'first_half')
  const firstHalfShots = await page.$$eval('.context-card:first-child .postmatch-stat:nth-of-type(4) b', (nodes) => nodes.map((n) => n.textContent))
  if (firstHalfShots.join(':') !== '5:14') throw new Error(`Half-time statistics selector failed: ${firstHalfShots}`)
  await page.select('.context-card:first-child .postmatch-panel-heading select', 'total')
  await page.click('.context-card:first-child .postmatch-all-events summary')
  if (await page.$$eval('.context-card:first-child .event-substitution', (nodes) => nodes.length) !== dossierApi.items[0].postmatch.events.filter((e) => e.type === 'SUBSTITUTION').length) throw new Error('Substitution timeline does not match source data')
  await page.click('.context-card:first-child .postmatch-all-events summary')
  await page.click('.context-card .context-details summary')
  const firstRoster = await page.$$eval('.context-card:first-child .context-lineup li', (players) => players.length)
  if (firstRoster !== 22) throw new Error('Context card must display both official starting lineups')
  await page.screenshot({ path: '../output/runtime/ucl-context-desktop.png' })
  await page.$eval('.context-card:first-child .postmatch-facts', (node) => node.scrollIntoView({ block: 'start' }))
  await page.screenshot({ path: '../output/runtime/ucl-postmatch-desktop.png' })
  await page.type('#context-team-search', '利物浦')
  await page.waitForFunction(() => globalThis.document.querySelectorAll('.context-card').length === 1)
  await page.click('.context-card .context-details summary')
  await page.waitForSelector('.context-resolved')

  if (runtimeErrors.length) throw new Error(`Browser runtime errors:\n${runtimeErrors.join('\n')}`)
  console.log(JSON.stringify({
    status: 'passed',
    faviconStatus,
    historicalMatchId,
    realTeamCount,
    chartCount,
    unavailableMetrics,
    referenceApi,
    referenceSettled: scorecardApi.settled,
    dossierSummary: dossierApi.summary,
    runtimeErrors: 0,
  }))
} finally {
  await browser.close()
}
