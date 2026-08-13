import { Bell, ChevronDown, Clock3, Search } from 'lucide-react'

export function Topbar({ title, eyebrow = '量化研究台' }) {
  const today = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' }).format(new Date())
  return (
    <header className="topbar">
      <div className="page-identity">
        <span>{eyebrow}</span>
        <h1>{title}</h1>
      </div>

      <div className="topbar-actions">
        <div className="global-search">
          <Search size={16} />
          <span>搜索比赛、球队或球员</span>
          <kbd>⌘ K</kbd>
        </div>
        <div className="phase-chip"><span /> 模拟下注期</div>
        <div className="clock-chip"><Clock3 size={15} /> {today} · 上海</div>
        <button className="icon-button" aria-label="通知"><Bell size={17} /></button>
        <button className="avatar-button" aria-label="用户菜单">QF <ChevronDown size={14} /></button>
      </div>
    </header>
  )
}
