import { Activity, BarChart3, Database, Gauge, ShieldCheck, UsersRound } from 'lucide-react'
import { NavLink } from 'react-router-dom'

const navigation = [
  { to: '/', label: '比赛', icon: Gauge },
  { to: '/players', label: '球员', icon: UsersRound },
  { to: '/teams', label: '球队', icon: ShieldCheck },
  { to: '/backtest', label: '回测', icon: BarChart3 },
  { to: '/ops', label: '数据运维', icon: Database },
]

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand" aria-label="量化研究台">
        <span className="brand-mark"><Activity size={19} strokeWidth={2.4} /></span>
        <span className="brand-name">EDGE<span>/LAB</span></span>
      </div>

      <nav className="primary-nav" aria-label="主导航">
        {navigation.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="system-pulse"><span /> 系统运行中</div>
        <p>研究与模拟环境</p>
        <strong>v0.1.0</strong>
      </div>
    </aside>
  )
}

