from ..models.domain import ScenarioState


class InputAdapter:
    def normalize(self, state: ScenarioState) -> ScenarioState:
        # Revalidate even constructed/mutated Pydantic objects and detach ownership.
        return ScenarioState.model_validate(state.model_dump(mode="json"))


class OutputAdapter:
    def export_state(self, state: ScenarioState) -> ScenarioState:
        return state.model_copy(deep=True)
