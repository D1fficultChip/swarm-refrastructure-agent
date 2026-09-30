import random

from ..assessment.service import process_event
from ..models.domain import EnvironmentConstraint, EnvironmentKind, Position, Task, TimeWindow
from ..models.events import (Event, NodeDegradation, NodeFailure, RestrictedAreaAdd,
                             RestrictedAreaRemove, TargetMove, TaskAdd, TaskCancel,
                             TaskPriorityChange)
from ..reconstruction.common import apply_option
from ..reconstruction.primitives import ReconstructionPrimitives
from .timing import TASK_TIME_WINDOW_SECONDS


class ChallengeNotAvailable(ValueError):
    pass


class DynamicEventSampler:
    def __init__(self,max_sampling_attempts: int = 40):
        self.max_sampling_attempts=max_sampling_attempts

    @staticmethod
    def _eid(index: int, suffix: str = "") -> str:
        return f"D{index:04d}{suffix}"

    @staticmethod
    def _assigned(state):
        members={node_id:f for f in state.formations for node_id in f.node_ids}
        return [(node,members[node.id]) for node in state.nodes if node.id in members and node.status.value!='FAILED']

    @staticmethod
    def _role_nodes(state,formation,role):
        nodes={n.id:n for n in state.nodes}
        return [nodes[n] for n in formation.node_ids if role in nodes[n].capabilities and nodes[n].status.value=='NORMAL']

    def sample(self,state,difficulty: str,rng: random.Random,index: int,simulation_time: float,
               event_type: str | None = None,target_id: str | None = None):
        now=max(float(state.clock),float(simulation_time))
        if event_type:
            return self._typed(state,event_type,rng,index,now,target_id),None
        if difficulty=='L1': return self._l1(state,rng,index,now),None
        if difficulty=='L2': return self._l2(state,rng,index,now),None
        if difficulty=='L3': return self._l3(state,rng,index,now),None
        if difficulty=='L4': return self._l4(state,rng,index,now)
        raise ValueError("unknown challenge difficulty")

    def _l1(self,state,rng,index,now):
        formations=list(state.formations);rng.shuffle(formations)
        for formation in formations[:self.max_sampling_attempts]:
            relay=self._role_nodes(state,formation,'relay')
            if len(relay)>=2:
                event=NodeDegradation(event_id=self._eid(index),occurred_at=now,node_id=rng.choice(relay).id,health=.6)
                candidate=process_event(state,event)
                if candidate.impact_analysis.affected_tasks and not ReconstructionPrimitives().get_outstanding_violations(candidate.application.state):
                    return [event]
        raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: no redundant provider')

    def _l2(self,state,rng,index,now):
        task_counts={f.id:sum(t.formation_id==f.id and t.status.value=='ACTIVE' for t in state.tasks) for f in state.formations}
        formations=[f for f in state.formations if task_counts[f.id]==1];rng.shuffle(formations)
        for formation in formations[:self.max_sampling_attempts]:
            nav=self._role_nodes(state,formation,'navigation')
            if len(nav)==1:
                event=NodeFailure(event_id=self._eid(index),occurred_at=now,node_id=nav[0].id)
                candidate=process_event(state,event).application.state
                failed=ReconstructionPrimitives().get_outstanding_violations(candidate)
                affected=[t for t in candidate.tasks if t.formation_id==formation.id and t.status.value=='ACTIVE']
                if failed and any(ReconstructionPrimitives().try_in_place_repair(candidate,t.id).options for t in affected):
                    return [event]
        raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: no local repair event')

    def _restricted(self,state,rng,index,now,suffix='A'):
        tasks=[t for t in state.tasks if t.status.value=='ACTIVE' and t.route_id]
        if not tasks:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: no route')
        task=rng.choice(tasks);route=next(r for r in state.routes if r.id==task.route_id)
        a,b=route.waypoints[0],route.waypoints[-1];cx=(a.x+b.x)/2;cy=(a.y+b.y)/2
        size=30
        region=EnvironmentConstraint(id=f"Z{index:04d}{suffix}",kind=EnvironmentKind.RESTRICTED,
            lower=Position(x=max(0,cx-size),y=max(0,cy-size)),
            upper=Position(x=min(state.bounds.width,cx+size),y=min(state.bounds.height,cy+size)))
        return RestrictedAreaAdd(event_id=self._eid(index,suffix),occurred_at=now,region=region)

    def _l3(self,state,rng,index,now):
        failure=self._l2(state,rng,index,now)[0]
        after=process_event(state,failure).application.state
        area=self._restricted(after,rng,index,now,'A')
        final=process_event(after,area).application.state
        categories={v.category for v in ReconstructionPrimitives().get_outstanding_violations(final)}
        if len(categories)<2:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: mixed categories unavailable')
        return [failure,area]

    def _l4(self,state,rng,index,now):
        groups={f.id:[t for t in state.tasks if t.formation_id==f.id and t.status.value=='ACTIVE'] for f in state.formations}
        candidates=[(next(f for f in state.formations if f.id==fid),tasks) for fid,tasks in groups.items() if len(tasks)>=2]
        rng.shuffle(candidates)
        for formation,tasks in candidates[:self.max_sampling_attempts]:
            nav=self._role_nodes(state,formation,'navigation')
            if len(nav)!=1:continue
            event=NodeFailure(event_id=self._eid(index),occurred_at=now,node_id=nav[0].id)
            changed=process_event(state,event).application.state
            early=min(tasks,key=lambda t:t.window.start)
            options=ReconstructionPrimitives().try_in_place_repair(changed,early.id).options
            if not options:continue
            preview=apply_option(changed,options[0])
            regressions=[v for v in ReconstructionPrimitives().get_outstanding_violations(preview) if v.subject!=early.id]
            if regressions:return [event],early.id
        raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: validator-feedback opportunity unavailable')

    def _typed(self,state,event_type,rng,index,now,target_id):
        nodes=[n for n in state.nodes if n.status.value!='FAILED']
        tasks=[t for t in state.tasks if t.status.value=='ACTIVE']
        if event_type=='NodeFailure':
            node=next((n for n in nodes if n.id==target_id),None) if target_id else rng.choice(nodes)
            if not node:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: node unavailable')
            return [NodeFailure(event_id=self._eid(index),occurred_at=now,node_id=node.id)]
        if event_type=='NodeDegradation':
            node=next((n for n in nodes if n.id==target_id),None) if target_id else rng.choice(nodes)
            if not node:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: node unavailable')
            return [NodeDegradation(event_id=self._eid(index),occurred_at=now,node_id=node.id,health=.6)]
        if event_type=='RestrictedAreaAdd':return [self._restricted(state,rng,index,now)]
        if event_type=='RestrictedAreaRemove':
            if not state.environment:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: no restricted area')
            region=next((z for z in state.environment if z.id==target_id),None) if target_id else rng.choice(state.environment)
            if not region:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: restricted area unavailable')
            return [RestrictedAreaRemove(event_id=self._eid(index),occurred_at=now,region_id=region.id)]
        if not tasks:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: no active task')
        task=next((t for t in tasks if t.id==target_id),None) if target_id else rng.choice(tasks)
        if not task:raise ChallengeNotAvailable('CHALLENGE_NOT_AVAILABLE: task unavailable')
        if event_type=='TargetMove':
            return [TargetMove(event_id=self._eid(index),occurred_at=now,task_id=task.id,
                target=Position(x=rng.uniform(600,930),y=rng.uniform(60,940)))]
        if event_type=='TaskCancel':return [TaskCancel(event_id=self._eid(index),occurred_at=now,task_id=task.id)]
        if event_type=='TaskPriorityChange':
            priority=1 if task.priority==5 else task.priority+1
            return [TaskPriorityChange(event_id=self._eid(index),occurred_at=now,task_id=task.id,priority=priority)]
        if event_type=='TaskAdd':
            number=max([int(t.id[1:]) for t in state.tasks if t.id[1:].isdigit()]+[0])+1
            added=Task(id=f"T{number:02d}",name=f"动态新增任务 {number}",requirements={"sensor":1},
                resource_required=5,min_nodes=1,priority=4,
                window=TimeWindow(start=now,end=now+TASK_TIME_WINDOW_SECONDS),
                start=task.start,target=task.target,formation_id=task.formation_id,route_id=None)
            return [TaskAdd(event_id=self._eid(index),occurred_at=now,task=added)]
        raise ValueError('unsupported event type')
