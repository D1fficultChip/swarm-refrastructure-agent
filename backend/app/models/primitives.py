"""Results of independently callable deterministic capabilities, not a policy."""
from .domain import DomainModel
from .phase3 import LocalOption, Phase3ExecutionTrace, RouteChangeRequest, RouteDecision, StrategyLevel


class LocalOptionsResult(DomainModel):
    base_state_version: int
    base_state_digest: str
    strategy_level: StrategyLevel
    options: list[LocalOption]
    search_complete: bool
    trace: Phase3ExecutionTrace


class RouteImpactsResult(DomainModel):
    requests: list[RouteChangeRequest]
    decisions: list[RouteDecision]
