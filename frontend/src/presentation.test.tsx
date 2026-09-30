import { describe,it,expect } from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import { MapView } from './MapView'
import {modes,time,changedRoutes,switchedNodes} from './presentation'
import type {State,Metrics} from './types'

const state:State={scenario_id:'TEST',version:1,clock:10,bounds:{width:100,height:100},
  nodes:[{id:'U1',position:{x:10,y:20},status:'FAILED',capabilities:{relay:1}},{id:'U2',position:{x:20,y:20},status:'NORMAL',capabilities:{relay:1}}],
  formations:[{id:'F1',node_ids:['U1'],roles:{}}],tasks:[],routes:[{id:'R1',waypoints:[{x:0,y:0},{x:100,y:0}]}],environment:[]}
const metrics:Metrics={nodes:{FAILED:1,NORMAL:1},node_count:2,task_count:0,feasible_tasks:0,formation_count:1,invalid_formations:['F1'],invalid_routes:[],capability_gap:1,task_decisions:{},violations:[],plan_status:'INVALID'}
describe('presentation contracts',()=>{
  it('labels deterministic explicitly and formats zero model time',()=>{expect(modes.deterministic_realtime.note).toContain('0 次模型');expect(time(0)).toBe('0.0 毫秒');expect(time(7200)).toBe('7.20 秒')})
  it('detects route and membership changes without declaring a failed node repaired',()=>{
    const after=structuredClone(state);after.formations[0].node_ids=['U2'];after.routes[0].waypoints=[{x:0,y:0},{x:50,y:20},{x:100,y:0}]
    expect([...changedRoutes(state,after)]).toEqual(['R1']);expect([...switchedNodes(state,after)]).toEqual(['U1','U2'])
    const svg=renderToStaticMarkup(<MapView before={state} after={after} view="overlay" metrics={metrics} onSelect={()=>{}}/>)
    expect(svg).toContain('节点 U1 · 失效');expect(svg).toContain('stroke-dasharray');expect(after.nodes[0].status).toBe('FAILED')
  })
})
