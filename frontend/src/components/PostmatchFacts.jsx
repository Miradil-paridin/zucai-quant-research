import { useState } from 'react'

const rows = [['possession','控球','%'],['shots','射门',''],['shots_on_target','射正',''],['corners','角球',''],['passes_completed','成功传球',''],['fouls','犯规',''],['yellow_cards','黄牌',''],['red_cards','红牌*','']]
const labels = { GOAL: '进球', SUBSTITUTION: '换人', YELLOW_CARD: '黄牌', RED_CARD: '红牌' }

function Timeline({ events, home, away }) {
  return <ol className="postmatch-timeline">{events.map((event) => {
    const side = event.scoring_side || event.side
    return <li key={event.id} className={`event-${event.type.toLowerCase()}`}>
      <time>{event.minute_label}</time><span className="event-kind">{labels[event.type]}</span>
      <div><strong>{event.type === 'SUBSTITUTION' ? `${event.incoming.name} ↑ / ${event.outgoing.name} ↓` : event.actor.name}</strong>
        <small>{side === 'home' ? home : away}{event.subtype === 'OWN' ? ' · 对方乌龙' : event.subtype === 'PENALTY' ? ' · 点球' : event.detail === 'DIRECT_FREE_KICK' ? ' · 直接任意球' : ''}
          {event.type === 'RED_CARD' ? event.on_pitch ? ' · 场上球员被罚下' : ' · 教练／非场上人员，不减少场上人数' : ''}</small></div>
      {event.score_after && <b>{event.score_after.home} : {event.score_after.away}</b>}
    </li>
  })}</ol>
}

export function PostmatchFacts({ item, sources }) {
  const [phase, setPhase] = useState('total')
  const post = item.postmatch
  if (!post) return <p className="context-unknown">本场赛后统计尚未核对。</p>
  const half = post.statistics.goals.first_half
  const [home, away] = item.label.split(' — ')
  return <div className="postmatch-facts">
    <div className="postmatch-verified"><strong>已完赛 · 官方数据已核对</strong><span>半场 {half.home} : {half.away}</span><span>全场 {item.score.home} : {item.score.away}</span></div>
    <div className="postmatch-columns"><section className="postmatch-stat-panel" aria-label={`${item.label}比赛统计`}>
      <div className="postmatch-panel-heading"><h4>比赛统计</h4><select aria-label={`${item.label}统计时段`} value={phase} onChange={(e) => setPhase(e.target.value)}><option value="total">全场</option><option value="first_half">上半场</option><option value="second_half">下半场</option></select></div>
      <div className="postmatch-team-labels"><b>{home}</b><b>{away}</b></div>
      {rows.map(([key,label,unit]) => {
        const value = post.statistics[key][phase]
        const total = value.home + value.away
        return <div className="postmatch-stat" key={key}><div><b>{value.home}{unit}</b><span>{label}</span><b>{value.away}{unit}</b></div><div className="postmatch-bar"><i style={{ width: `${total ? value.home/total*100 : 0}%` }} /><i style={{ opacity: total ? 1 : 0 }} /></div></div>
      })}
      <p className="reference-footnote">* 红牌总数可能包含教练或替补，场上人数变化以事件说明为准。xG：本批官方来源未提供。</p>
      <div className="postmatch-source-links">{post.source_ids.filter((id) => id.endsWith('-ts') || id.endsWith('-fr')).map((id) => <a key={id} href={sources[id].url} target="_blank" rel="noreferrer">{id.endsWith('-ts') ? '官方统计表 ↗' : '官方赛后报告 ↗'}</a>)}</div>
    </section><section aria-label={`${item.label}进球与红牌`}><h4>进球与红牌 · 按发生顺序</h4><Timeline events={post.events.filter((e) => e.type === 'GOAL' || e.type === 'RED_CARD')} home={home} away={away} />
      <details className="postmatch-all-events"><summary>全部关键事件（含换人、黄牌）· {post.events.length}条</summary><Timeline events={post.events} home={home} away={away} /><a href={sources[post.source_ids.find((id) => id.endsWith('-events'))].url} target="_blank" rel="noreferrer">官方事件记录 ↗</a></details>
    </section></div>
    <div className="context-assessment postmatch-assessment"><h4>根据比赛事实重新分析</h4><p>{post.interpretation}</p></div>
  </div>
}
