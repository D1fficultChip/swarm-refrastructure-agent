import {describe,expect,it} from 'vitest'
import {isTaskExpired,projectRuntimeState} from './DynamicMission'
import type {DynamicRuntime,State,Task} from './types'

const planning:State={scenario_id:'DYN1',version:3,clock:0,bounds:{width:100,height:100},
 nodes:[{id:'U01',position:{x:1,y:2},status:'NORMAL',capabilities:{sensor:1}}],
 formations:[],tasks:[],routes:[],environment:[]}

const runtime:DynamicRuntime={simulation_time:12,node_runtime_positions:{U01:{x:40,y:50}},
 formation_runtime_positions:{},task_progress:{},task_runtime_status:{},current_route_progress:{},
 simulation_running:true,simulation_speed:2,pending_dynamic_events:[],last_planning_sync_time:0}

describe('dynamic runtime projection',()=>{
 it('projects high-frequency positions without mutating planning state or its version',()=>{
  const projected=projectRuntimeState(planning,runtime)
  expect(projected.nodes[0].position).toEqual({x:40,y:50})
  expect(projected.version).toBe(3)
  expect(planning.nodes[0].position).toEqual({x:1,y:2})
 })

 it('distinguishes an unfinished missed time window from a current constraint blockage',()=>{
  const task:Task={id:'T01',name:'巡检',formation_id:'F01',route_id:'R01',start:{x:0,y:0},target:{x:10,y:10},status:'ACTIVE',priority:3,window:{start:0,end:300}}
  expect(isTaskExpired(task,{...runtime,simulation_time:440,task_progress:{T01:.985}})).toBe(true)
  expect(isTaskExpired(task,{...runtime,simulation_time:250,task_progress:{T01:.985}})).toBe(false)
  expect(isTaskExpired(task,{...runtime,simulation_time:440,task_progress:{T01:1}})).toBe(false)
 })
})
