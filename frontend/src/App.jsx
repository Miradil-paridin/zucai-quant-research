import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { ErrorBoundary } from './components/ErrorBoundary'

const MatchDesk = lazy(() => import('./pages/MatchDesk').then((module) => ({ default: module.MatchDesk })))
const PlayersPage = lazy(() => import('./pages/PlayersPage').then((module) => ({ default: module.PlayersPage })))
const TeamsPage = lazy(() => import('./pages/TeamsPage').then((module) => ({ default: module.TeamsPage })))
const BacktestPage = lazy(() => import('./pages/BacktestPage').then((module) => ({ default: module.BacktestPage })))
const OpsPage = lazy(() => import('./pages/OpsPage').then((module) => ({ default: module.OpsPage })))

export default function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <ErrorBoundary>
          <Suspense fallback={<div className="page-loader"><span />正在装载研究数据</div>}>
            <Routes>
              <Route path="/" element={<MatchDesk />} />
              <Route path="/matches/:matchId" element={<MatchDesk />} />
              <Route path="/players" element={<PlayersPage />} />
              <Route path="/teams" element={<TeamsPage />} />
              <Route path="/backtest" element={<BacktestPage />} />
              <Route path="/ops" element={<OpsPage />} />
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  )
}
