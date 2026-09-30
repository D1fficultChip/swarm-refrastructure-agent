"""Thin Phase 2–4 capability adapters; no policy, escalation or implicit commit.

Callers choose each operation and carry their private working snapshot between
calls. Existing solvers and the global validation/transaction boundary remain
authoritative. The deterministic baseline is not invoked by these methods.
"""
from ..assessment.engine import TaskAssessmentEngine
from ..models.domain import ScenarioState, Task, TaskStatus
from ..models.phase2 import TaskAssessmentResult
from ..models.phase3 import (CandidateSet, Phase3ExecutionTrace, Phase3Timing, ReconstructionProposal,
                             RouteChangeRequest, RoutePlanResult, StrategyLevel, TaskRequirement)
from ..models.phase4 import ReconstructionCommitReceipt, ValidationCheck, ValidationResult
from ..models.primitives import LocalOptionsResult, RouteImpactsResult
from .candidates import CandidateGenerator
from .common import digest
from .config import PlannerConfig
from .engine import TransactionalReconstructionEngine
from .formation import FormationReconstructor
from .reservations import ReservationLedger
from .routes import RouteImpactDetector, RoutePlanner
from .scope import ReconstructionScopeBuilder
from .task import TaskReconstructor
from .validator import GlobalConstraintValidator, mission_checks


class ReconstructionPrimitives:
    def __init__(self, store=None, config: PlannerConfig | None = None):
        self.store = store  # Required only for commit; all other calls use explicit snapshots.
        self.config = config or PlannerConfig.load()

    @staticmethod
    def _snapshot(state: ScenarioState) -> ScenarioState:
        return ScenarioState.model_validate(state.model_dump(mode="json"))

    @staticmethod
    def _task(state: ScenarioState, task_id: str) -> Task:
        task = next((t for t in state.tasks if t.id == task_id), None)
        if task is None:
            raise ValueError(f"Unknown task: {task_id}")
        if task.status != TaskStatus.ACTIVE:
            raise ValueError(f"Task is not ACTIVE: {task_id}")
        return task

    def assess_current_task_state(self, state: ScenarioState) -> list[TaskAssessmentResult]:
        """Current full Phase 2 assessment, not a historical event receipt."""
        return TaskAssessmentEngine().assess(self._snapshot(state), full_check=True)

    def get_outstanding_violations(self, state: ScenarioState) -> list[ValidationCheck]:
        """Current global mission failures, without needing a proposal.

        Does not certify proposal structure, scope, metadata or preconditions.
        """
        per_task, _, _ = mission_checks(self._snapshot(state))
        failures = {(c.category, c.code, c.subject): c for values in per_task.values() for c in values if not c.passed}
        return list(failures.values())

    def generate_candidates(self, state: ScenarioState, task_id: str, gap: dict[str, float],
                            level: StrategyLevel = StrategyLevel.IN_PLACE_REPAIR,
                            *, count_or_resource_gap: bool = False) -> CandidateSet:
        """Gap controls candidate filtering only; it never changes task requirements."""
        state = self._snapshot(state)
        task = self._task(state, task_id)
        scope = ReconstructionScopeBuilder().build(state)
        return CandidateGenerator().generate(state, TaskRequirement.from_task(task), scope,
            ReservationLedger(state), dict(gap), level, count_or_resource_gap=count_or_resource_gap)

    def _options(self, state, task_id, level, protected_task_ids):
        state = self._snapshot(state)
        task = self._task(state, task_id)
        scope = ReconstructionScopeBuilder().build(state)
        protected = set(scope.protected_task_ids) | set(protected_task_ids)
        if not protected <= {t.id for t in state.tasks}:
            raise ValueError("Unknown protected task ID")
        trace = Phase3ExecutionTrace(requirements=[TaskRequirement.from_task(task)], candidate_sets=[],
            strategy_attempts=[], reservations=ReservationLedger(state).reservations, route_decisions=[], route_results=[],
            selected_strategies=[], backtracks=0, branches_explored=0, timing=Phase3Timing())
        if level == StrategyLevel.TASK_REASSIGNMENT:
            options = TaskReconstructor().options(state, task, protected, trace)
            complete = True
        else:
            options, complete = FormationReconstructor(self.config).options(state, task, scope, level, protected, trace)
        return LocalOptionsResult(base_state_version=state.version, base_state_digest=digest(state),
            strategy_level=level, options=options, search_complete=complete, trace=trace)

    def try_in_place_repair(self, state: ScenarioState, task_id: str,
                            protected_task_ids: tuple[str, ...] = ()) -> LocalOptionsResult:
        return self._options(state, task_id, StrategyLevel.IN_PLACE_REPAIR, protected_task_ids)

    def find_task_reassignment_options(self, state: ScenarioState, task_id: str,
                                       protected_task_ids: tuple[str, ...] = ()) -> LocalOptionsResult:
        return self._options(state, task_id, StrategyLevel.TASK_REASSIGNMENT, protected_task_ids)

    def try_formation_reconstruction(self, state: ScenarioState, task_id: str,
                                     protected_task_ids: tuple[str, ...] = ()) -> LocalOptionsResult:
        return self._options(state, task_id, StrategyLevel.FORMATION_RECONSTRUCTION, protected_task_ids)

    def detect_route_impacts(self, state: ScenarioState, task_ids: list[str] | None = None) -> RouteImpactsResult:
        state = self._snapshot(state)
        ids = [t.id for t in state.tasks if t.status == TaskStatus.ACTIVE] if task_ids is None else task_ids
        for task_id in ids:
            self._task(state, task_id)
        requests, decisions = RouteImpactDetector().detect(state, set(ids))
        return RouteImpactsResult(requests=requests, decisions=decisions)

    def replan_route(self, state: ScenarioState, request: RouteChangeRequest) -> RoutePlanResult:
        return RoutePlanner(self.config).plan(self._snapshot(state), request.model_copy(deep=True))

    def validate_proposal(self, state: ScenarioState, proposal: ReconstructionProposal) -> ValidationResult:
        return GlobalConstraintValidator().validate_proposal(state, proposal)

    def commit_validated_proposal(self, session_id: str, proposal: ReconstructionProposal) -> ReconstructionCommitReceipt:
        """Always revalidate under the transaction lock; a prior PASS is not authority."""
        if self.store is None:
            raise ValueError("A session store is required for commit")
        return TransactionalReconstructionEngine(self.store).commit(session_id, proposal)
