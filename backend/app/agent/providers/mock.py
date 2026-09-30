from copy import deepcopy

from .base import ModelError, ModelProvider, ModelReply


class MockModelProvider(ModelProvider):
    """Explicit scripted actions for offline tests, never presented as model reasoning."""
    name = "mock-scripted"

    def __init__(self, actions):
        self.actions = iter(actions)
        self.contexts = []

    def generate_action(self, context, tool_schemas, *, timeout_seconds, max_attempts):
        self.contexts.append(deepcopy(context))
        try:
            action = next(self.actions)
        except StopIteration:
            raise ModelError("MOCK_SEQUENCE_EXHAUSTED") from None
        if callable(action):
            action = action(context)
        if isinstance(action, Exception):
            raise action
        return ModelReply(action=deepcopy(action), model_name=self.name)
