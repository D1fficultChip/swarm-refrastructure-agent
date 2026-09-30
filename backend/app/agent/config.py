import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..models.domain import DomainModel


class AgentContextBudget(DomainModel):
    max_affected_tasks: int = Field(default=8, ge=1)
    max_candidate_summaries: int = Field(default=6, ge=1)
    max_history_steps: int = Field(default=5, ge=1)
    max_validation_violations: int = Field(default=12, ge=1)
    max_serialized_chars: int = Field(default=14000, ge=2000)


class AgentConfig(DomainModel):
    provider: Literal["mock", "openai_compatible"] = "openai_compatible"
    model_name: str = "qwen3.8-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_timeout_seconds: float = Field(default=25, gt=0)
    model_max_retries: int = Field(default=1, ge=0, le=5)
    model_temperature: float = Field(default=0, ge=0, le=2)
    model_max_output_tokens: int = Field(default=1000, ge=100, le=8192)
    structured_output: Literal["json_object", "json_schema"] = "json_schema"
    # Provider transport settings only, never policy decisions or credentials.
    enable_thinking: bool | None = False
    max_agent_steps: int = Field(default=16, ge=1, le=100)
    max_model_calls: int = Field(default=20, ge=1, le=200)
    max_validation_retries: int = Field(default=3, ge=0, le=20)
    max_stale_restarts: int = Field(default=2, ge=0, le=10)
    max_total_seconds: float = Field(default=120, gt=0, le=1800)
    fallback_enabled: bool = False
    structured_retry_limit: int = Field(default=2, ge=0, le=10)
    context_budget: AgentContextBudget = Field(default_factory=AgentContextBudget)
    # Original remains available for P5 compatibility and ablation.
    policy_profile: Literal["original", "snapshot", "optimized"] = "original"
    execution_mode: Literal["adaptive_agent", "bounded_agent", "deterministic_realtime"] = "adaptive_agent"
    fast_model_name: str | None = None
    strong_model_name: str | None = None
    auto_commit_after_validation: bool = True
    decision_time_budget: float = Field(default=15, gt=0, le=120)
    bounded_max_model_calls: int = Field(default=2, ge=1, le=10)
    bounded_agent_seconds: float = Field(default=5, gt=0, le=120)
    max_option_tasks: int = Field(default=8, ge=1, le=32)
    max_options_per_strategy: int = Field(default=4, ge=1, le=32)
    snapshot_option_limit: int = Field(default=16, ge=3, le=64)
    delta_context_chars: int = Field(default=9000, ge=3000)

    @classmethod
    def load(cls):
        path = Path(__file__).resolve().parents[3] / "config/agent.json"
        values = json.loads(path.read_text())
        names = {"MODEL_PROVIDER": "provider", "MODEL_BASE_URL": "base_url", "MODEL_NAME": "model_name",
                 "MODEL_TIMEOUT_SECONDS": "model_timeout_seconds", "MODEL_MAX_RETRIES": "model_max_retries",
                 "MODEL_TEMPERATURE": "model_temperature", "AGENT_FALLBACK_ENABLED": "fallback_enabled",
                 "FAST_POLICY_MODEL": "fast_model_name", "STRONG_POLICY_MODEL": "strong_model_name",
                 "FAST_MODEL_NAME": "fast_model_name", "STRONG_MODEL_NAME": "strong_model_name",
                 "AGENT_EXECUTION_MODE": "execution_mode", "AGENT_POLICY_PROFILE": "policy_profile",
                 "AUTO_COMMIT_AFTER_VALIDATION": "auto_commit_after_validation",
                 "DECISION_TIME_BUDGET": "decision_time_budget"}
        values.update({field: os.environ[name] for name, field in names.items() if name in os.environ})
        return cls.model_validate(values)
