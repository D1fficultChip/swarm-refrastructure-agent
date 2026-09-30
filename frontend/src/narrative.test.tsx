import {describe,it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import {renderToStaticMarkup} from 'react-dom/server'
import type {Run} from './types'
import {capabilityRows,dependencyGraph,impactChains,issueText,preservation,readablePath,entity} from './presentation'
import {FeedbackStory,Changes,ImpactPanel,TechnologyChain,ValidationPanel} from './Narrative'
const load=(file:string):Run=>JSON.parse(readFileSync(new URL('../../docs/evidence/phase6/'+file,import.meta.url),'utf8'))
const sc08=load('final-sc08.json'),sc07=load('feedback-sc07.json')
describe('readable projections of real recorded results',()=>{
 it('retains actual P2 edges and capability values, including arbitrary changed IDs',()=>{
  const copy=structuredClone(sc08);copy.impact_analysis=JSON.parse(JSON.stringify(copy.impact_analysis).replaceAll('T03','T99').replaceAll('U17','U88'))
  const chains=impactChains(copy.impact_analysis);expect(chains.map(c=>c.task)).toEqual(expect.arrayContaining(['T99','T04','T05']))
  expect(chains.find(c=>c.task==='T99')!.path).toEqual(['node:U88','formation:F03','capability:formation:F03:relay','task:T99'])
  expect(readablePath(chains[0].path,[])).toContain('中继能力')
  const a=structuredClone(sc08.task_assessment.find(a=>a.task_id==='T03')!)
  expect(issueText(a)).toContain('需求 1 / 当前可用 0 / 缺口 1')
 expect(capabilityRows(a).find(c=>c.key==='sensor')?.available).toBe(3)
 })
 it('projects recorded TRDG edges and the layered execution flow without teaching labels',()=>{
  const graph=dependencyGraph(sc08.impact_analysis,sc08.event)
  expect(graph.edges).toContainEqual({from:'node:U17',to:'formation:F03'})
  expect(graph.edges).toContainEqual({from:'formation:F03',to:'capability:formation:F03:relay'})
  expect(graph.edges).toContainEqual({from:'route:R05',to:'task:T05'})
  const impact=renderToStaticMarkup(<ImpactPanel impacts={sc08.impact_analysis} assessments={sc08.task_assessment} events={sc08.event} state={sc08.before_state} onFocus={()=>{}}/>)
  expect(impact).toContain('TRDG 任务依赖图');expect(impact).toContain('data-edge="node:U17&gt;formation:F03"')
  expect(impact).not.toContain('为什么原方案需要调整');expect(impact).not.toContain('技术 1')
  const pipeline=renderToStaticMarkup(<TechnologyChain stage={5} running={false} run={sc08} mode={sc08.execution_mode} impacts={sc08.impact_analysis} assessments={sc08.task_assessment}/>)
  expect(pipeline).toContain('状态与事件数据');expect(pipeline).toContain('原语工具调度');expect(pipeline).toContain('事务化提交')
 })
 it('calculates preserved plans from actual task, formation and route data',()=>{
  const p=preservation(sc08)!;expect(p.unaffected).toHaveLength(5);expect(p.routes).toHaveLength(7);expect(p.assignments).toBe(8)
  const changed=structuredClone(sc08);changed.after_state.routes.find(r=>r.id==='R01')!.waypoints[0].x+=1
  const q=preservation(changed)!;expect(q.routes).toHaveLength(6);expect(q.unaffected).not.toContain('T01')
  expect(p.taskRows.find(t=>t.task==='T03')!.routeKept).toBe(true)
 })
 it('labels preview modifications separately from committed results',()=>{
  const preview=structuredClone(sc08);preview.commit=null;preview.status='READY';preview.candidate_state=preview.after_state;preview.after_state=preview.before_state
  expect(renderToStaticMarkup(<Changes run={preview}/>)).toContain('待提交方案变更')
  expect(preservation(preview)!.routes).toHaveLength(7)
 })
 it('explains true SC07 feedback without inventing a second plan when trace is incomplete',()=>{
  const full=renderToStaticMarkup(<FeedbackStory run={sc07} steps={sc07.trace_steps}/>)
  expect(full).toContain('任务 T06被连带破坏');expect(full).toContain('任务 T03的已检查约束满足');expect(full).toContain('扩大重构范围：加入任务 T06');expect(full).toContain('选择替代方案')
  const first=renderToStaticMarkup(<FeedbackStory run={sc07} steps={sc07.trace_steps.slice(0,1)}/>)
  expect(first).toContain('尚未出现第二次校核');expect(first).not.toContain('选择替代方案')
  const noFeedback=renderToStaticMarkup(<FeedbackStory run={sc08} steps={sc08.trace_steps}/>)
  expect(noFeedback).toBe('')
 })
 it('shows validation scope and physical limits in Chinese, with raw evidence retained',()=>{
  const html=renderToStaticMarkup(<ValidationPanel validation={sc08.validation!} committed={true}/>)
  expect(html).toContain('真实飞行时间');expect(html).toContain('节点转场时间');expect(html).toContain('137');expect(html).toContain('全局校核技术详情')
  expect(entity('task:T55')).toBe('任务 T55');expect(entity(null)).toBe('未分配')
 })
})
