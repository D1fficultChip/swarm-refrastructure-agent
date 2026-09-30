from fastapi import APIRouter, HTTPException, Request

from ..models.api import (
    AssessmentRequest, ErrorResponse, HealthResponse, InjectEventRequest, LoadScenarioRequest,
    ProposeReconstructionRequest, ReconstructRequest, ScenarioSnapshot, ScenarioSummary,
)
from ..models.phase2 import Phase2Result
from ..models.phase3 import ReconstructionProposal
from ..models.phase4 import (ProposalActionRequest, ReconstructionCommitReceipt, ReconstructionResult,
                             SessionReconstructRequest, ValidationResult)
from ..reconstruction.engine import DeterministicReconstructionEngine
from ..agent.models import AgentReconstructRequest, AgentReconstructionResult
from ..agent.orchestrator import AgentReconstructionOrchestrator
from ..agent.optimized_orchestrator import OptimizedAgentOrchestrator
from ..scenarios.manager import (
    ScenarioManager, ScenarioNotFoundError, SessionNotFoundError,
)

router = APIRouter(prefix="/api/v1")


@router.post("/agent/reconstruct", response_model=AgentReconstructionResult)
def agent_reconstruct(body: AgentReconstructRequest, request: Request):
    config = request.app.state.agent_config
    factory = request.app.state.agent_provider_factory
    try:
        optimized = config.policy_profile!="original" or config.execution_mode!="adaptive_agent" or body.mode in {
            "adaptive_agent","bounded_agent","deterministic_realtime"}
        if optimized:
            fast=factory(config.model_copy(update={"model_name":config.fast_model_name or config.model_name}))
            strong=factory(config.model_copy(update={"model_name":config.strong_model_name or config.model_name}))
            engine=OptimizedAgentOrchestrator(manager(request),fast,config,strong_provider=strong)
        else:
            engine=AgentReconstructionOrchestrator(manager(request),factory(config),config)
        result = engine.run(
            body.session_id, body.expected_version, body.mode, body.objective, body.initial_proposal)
    except SessionNotFoundError:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在"}) from None
    with manager(request)._lock:
        request.app.state.agent_runs[result.agent_run_id] = result.model_copy(deep=True)
    return result


@router.get("/agent/runs/{run_id}", response_model=AgentReconstructionResult)
def agent_run(run_id: str, request: Request):
    with manager(request)._lock:
        result = request.app.state.agent_runs.get(run_id)
        if result is None:
            raise HTTPException(404, detail={"code": "AGENT_RUN_NOT_FOUND", "message": "执行记录不存在"})
        return result.model_copy(deep=True)


def manager(request: Request) -> ScenarioManager:
    return request.app.state.scenario_manager


@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse()


@router.get("/scenarios", response_model=list[ScenarioSummary])
def list_scenarios(request: Request):
    return manager(request).list_scenarios()


@router.post("/scenario/load", response_model=ScenarioSnapshot,
             responses={404: {"model": ErrorResponse}})
def load_scenario(body: LoadScenarioRequest, request: Request):
    try:
        return manager(request).load(body.scenario_id)
    except ScenarioNotFoundError:
        raise HTTPException(404, detail={"code": "SCENARIO_NOT_FOUND", "message": "场景不存在"}) from None


@router.get("/scenario/state", response_model=ScenarioSnapshot,
            responses={404: {"model": ErrorResponse}})
def get_state(session_id: str, request: Request):
    try:
        return manager(request).get(session_id)
    except SessionNotFoundError:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在，请先加载场景"}) from None


@router.post("/event/inject", response_model=Phase2Result,
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def inject_event(body: InjectEventRequest, request: Request):
    try:
        return manager(request).inject(body.session_id, body.expected_version, body.event)
    except SessionNotFoundError:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在，请先加载场景"}) from None


@router.post("/assessment", response_model=Phase2Result,
             responses={404: {"model": ErrorResponse}})
def assessment(body: AssessmentRequest, request: Request):
    try:
        return manager(request).assessment(body.session_id, body.event_id)
    except SessionNotFoundError:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在，请先加载场景"}) from None


@router.post("/reconstruct", response_model=ReconstructionResult)
def reconstruct(body: SessionReconstructRequest | ReconstructRequest, request: Request):
    engine = DeterministicReconstructionEngine(manager(request))
    if isinstance(body, SessionReconstructRequest):
        return engine.reconstruct(body.session_id, body.expected_version, body.commit)
    return engine.reconstruct_input(body.state, body.event, body.commit)


@router.post("/reconstruction/validate", response_model=ValidationResult)
def validate_reconstruction(body: ProposalActionRequest, request: Request):
    try:
        return manager(request).validate(body.session_id, body.proposal)
    except SessionNotFoundError:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在"}) from None


@router.post("/reconstruction/commit", response_model=ReconstructionCommitReceipt)
def commit_reconstruction(body: ProposalActionRequest, request: Request):
    return manager(request).commit(body.session_id, body.proposal)


@router.post("/reconstruction/propose", response_model=ReconstructionProposal,
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def propose_reconstruction(body: ProposeReconstructionRequest, request: Request):
    try:
        return manager(request).propose(body.session_id, body.expected_version)
    except SessionNotFoundError:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在，请先加载场景"}) from None


@router.get("/reconstruction/{request_id}", response_model=ReconstructionResult)
def get_reconstruction(request_id: str, request: Request):
    return manager(request).reconstruction(request_id)


@router.get("/trace/{request_id}", response_model=ReconstructionResult)
def get_trace(request_id: str, request: Request):
    return manager(request).reconstruction(request_id)
