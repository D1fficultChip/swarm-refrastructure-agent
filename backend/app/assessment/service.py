"""Phase 2 orchestration only. No plan generation, solver, or reconstruction."""

from time import perf_counter

from ..events.injector import apply_event
from ..graph.dependency_graph import TaskReconstructionGraph
from ..graph.impact_propagation import analyze_impact
from ..models.domain import ScenarioState
from ..models.events import Event
from ..models.phase2 import Phase2ExecutionTrace, Phase2Result, Phase2Timing, TaskDecision
from .engine import TaskAssessmentEngine


def process_event(state: ScenarioState, event: Event) -> Phase2Result:
    start = perf_counter()
    application = apply_event(state, event)
    graph_start = perf_counter()
    before_graph = TaskReconstructionGraph.from_state(state)
    after_graph = TaskReconstructionGraph.from_state(application.state)
    graph_ms = (perf_counter() - graph_start) * 1000
    impact = analyze_impact(state, application.state, event, after_graph, before_graph)
    assessment_start = perf_counter()
    assessment = TaskAssessmentEngine().assess(application.state, impact)
    assessment_ms = (perf_counter() - assessment_start) * 1000
    timing = Phase2Timing(
        event_application_ms=application.event_application_ms, graph_build_ms=graph_ms,
        impact_analysis_ms=impact.analysis_time_ms, assessment_ms=assessment_ms,
        total_phase2_ms=(perf_counter() - start) * 1000,
    )
    trace = Phase2ExecutionTrace(
        event=event.model_copy(deep=True), changes=application.changes,
        state_version_before=state.version, state_version_after=application.state.version,
        clock_before=state.clock, clock_after=application.state.clock,
        graph_stats_before=before_graph.stats(), graph_stats_after=after_graph.stats(),
        propagation_records=impact.impact_records, task_assessments=assessment, timing=timing,
    )
    result = Phase2Result(
        application=application, impact_analysis=impact, task_assessment=assessment, trace=trace, metrics=timing,
        reconstruction_required=any(item.decision == TaskDecision.RECONSTRUCT for item in assessment),
        adjustment_required=any(item.decision == TaskDecision.ADJUST for item in assessment),
    )
    # Includes trace/result construction, excludes HTTP serialization/session queueing.
    timing.total_phase2_ms = (perf_counter() - start) * 1000
    return result
