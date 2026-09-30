from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ModelError(RuntimeError):
    def __init__(self, code, attempts=1):
        super().__init__(code)  # Never put raw HTTP bodies, headers or prompts in errors.
        self.code, self.attempts = code, attempts


@dataclass
class ModelReply:
    action: Any
    model_name: str
    attempts: int = 1
    token_usage: dict[str, int] = field(default_factory=dict)
    prompt_metrics: dict = field(default_factory=dict)


class ModelProvider(ABC):
    name = "provider"

    @abstractmethod
    def generate_action(self, context, tool_schemas, *, timeout_seconds, max_attempts) -> ModelReply:
        """One policy decision; transport retries share the supplied time/call budget."""
