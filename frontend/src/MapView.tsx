import type { State,Metrics } from './types'
import { changedRoutes,switchedNodes,entity,capability } from './presentation'

export function MapView({before,after,view,metrics,onSelect,focusTask=''}:{before:State;after:State;view:'before'|'after'|'overlay';metrics:Metrics;focusTask?:string;onSelect:(id:string)=>void}) {
  const state=view==='before'?before:after
  const changed=changedRoutes(before,after),switched=switchedNodes(before,after)
  const width=state.bounds.width,height=state.bounds.height
  const size=Math.max(width,height)/1000
  const task=state.tasks.find(t=>t.id===focusTask)
  const formation=state.formations.find(f=>f.id===task?.formation_id)
  const relevantNodes=new Set(formation?.node_ids??[])
  const positions=new Map<string,typeof state.nodes>()
  for(const n of state.nodes){const k=`${n.position.x},${n.position.y}`;positions.set(k,[...(positions.get(k)??[]),n])}
  const points=(route:{waypoints:{x:number;y:number}[]})=>route.waypoints.map(p=>`${p.x},${height-p.y}`).join(' ')
  return <svg className="situation" viewBox={`${-70*size} ${-45*size} ${width+260*size} ${height+120*size}`} role="img" aria-label="二维任务态势图">
    <defs><pattern id="grid" width={width/10} height={height/10} patternUnits="userSpaceOnUse"><path d={`M ${width/10} 0 H 0 V ${height/10}`} fill="none" stroke="#dde5ee" strokeWidth={size}/></pattern></defs>
    <rect width={width} height={height} fill="url(#grid)"/>
    <rect width={width} height={height} fill="none" stroke="#b8c8d8" strokeWidth={size}/>
    {state.environment.map(z=><g key={z.id}><rect x={z.lower.x} y={height-z.upper.y} width={z.upper.x-z.lower.x} height={z.upper.y-z.lower.y} fill="#ef444422" stroke="#dc3f4d" strokeWidth={2*size}/><text x={z.lower.x} y={height-z.upper.y-9*size} className="map-label" fontSize={21*size} fill="#bb253e">限制区域 {z.id}</text></g>)}
    {view==='overlay'&&before.routes.filter(r=>changed.has(r.id)).map(r=><polyline key={'old'+r.id} points={points(r)} fill="none" stroke="#a0acba" strokeWidth={3*size} strokeDasharray={`${8*size} ${6*size}`}/>)}
    {state.routes.map(r=><g key={r.id} opacity={task&&task.route_id!==r.id?0.16:1} onClick={()=>onSelect(r.id)} className="map-clickable"><polyline points={points(r)} fill="none" stroke={view!=='before'&&changed.has(r.id)?'#07926d':metrics.invalid_routes.includes(r.id)?'#df4754':'#668dab'} strokeWidth={(changed.has(r.id)?5:3)*size}/><text x={(r.waypoints[0].x+r.waypoints.at(-1)!.x)/2} y={height-r.waypoints[0].y-8*size} fontSize={18*size} fill="#637b92">{entity(r.id)}</text></g>)}
    {state.formations.map(f=>{
      const members=state.nodes.filter(n=>f.node_ids.includes(n.id));if(!members.length)return null
      const center={x:members.reduce((n,p)=>n+p.position.x,0)/members.length,y:members.reduce((n,p)=>n+p.position.y,0)/members.length}
      return <g key={f.id} opacity={task&&task.formation_id!==f.id?0.15:1} onClick={()=>onSelect(f.id)} className="map-clickable">{members.map(n=><line key={n.id} x1={center.x} y1={height-center.y} x2={n.position.x} y2={height-n.position.y} stroke={metrics.invalid_formations.includes(f.id)?'#e69734':'#7fa2c1'} strokeWidth={1.5*size} strokeDasharray={`${3*size} ${5*size}`} opacity=".8"/>)}<text x={center.x+22*size} y={height-center.y+38*size} fontSize={24*size} fontWeight="700" fill="#355771">{entity(f.id)}</text></g>
    })}
    {[...state.nodes].sort((a,b)=>Number(a.status==='FAILED')-Number(b.status==='FAILED')).map(n=><g key={n.id} opacity={task&&!relevantNodes.has(n.id)&&n.status!=='FAILED'?0.18:1} className="map-clickable" onClick={()=>onSelect(n.id)}><circle cx={n.position.x} cy={height-n.position.y} r={(n.status==='FAILED'?13:8)*size} fill={n.status==='FAILED'?'#d9374d':n.status==='DEGRADED'?'#e9972c':view!=='before'&&switched.has(n.id)?'#07926d':'#4b789d'} stroke="#fff" strokeWidth={1.5*size}/>{n.status==='FAILED'&&<path d={`M ${n.position.x-3*size} ${height-n.position.y-3*size} l ${6*size} ${6*size} m 0 ${-6*size} l ${-6*size} ${6*size}`} stroke="#fff" strokeWidth={1.3*size}/>}<title>{`${entity(n.id)} · ${n.status==='FAILED'?'失效':n.status==='DEGRADED'?'降级':'正常'} · ${Object.entries(n.capabilities).map(([c,v])=>`${capability[c]??'能力'} ${v}`).join('、')}`}</title>{(n.status!=='NORMAL'||switched.has(n.id))&&<text x={n.position.x+9*size} y={height-n.position.y-7*size} fontSize={21*size} fill={n.status==='FAILED'?'#ba243a':'#087c60'}>{entity(n.id)}</text>}</g>)}
    {[...positions.values()].filter(ns=>ns.length>1).map(ns=><g key={'cluster'+ns[0].id} onClick={()=>onSelect(ns[0].id)}><title>{ns.map(n=>entity(n.id)).join('、')}（同一坐标，未改变真实位置）</title><text x={ns[0].position.x-80*size} y={height-ns[0].position.y-18*size} fontSize={19*size} fill="#355771">{ns.length} 个节点</text></g>)}
    {state.tasks.filter(t=>t.status==='ACTIVE').map(t=><g key={t.id} opacity={task&&task.id!==t.id?0.2:1} className="map-clickable" onClick={()=>onSelect(t.id)}><path d={`M ${t.target.x} ${height-t.target.y-8*size} l ${8*size} ${8*size} l ${-8*size} ${8*size} l ${-8*size} ${-8*size} Z`} fill={metrics.violations.some(v=>v.subject===t.id)?'#fff0d8':'#ffffff'} stroke={metrics.violations.some(v=>v.subject===t.id)?'#c17b13':'#355771'} strokeWidth={2*size}/><text x={t.target.x+13*size} y={height-t.target.y+5*size} fontSize={22*size} fill="#24445f">{entity(t.id)}</text></g>)}
    <text x={0} y={height+25*size} fontSize={18*size} fill="#7b8a9e">0</text><text x={width-70*size} y={height+25*size} fontSize={18*size} fill="#7b8a9e">{width} 米</text>
  </svg>
}
