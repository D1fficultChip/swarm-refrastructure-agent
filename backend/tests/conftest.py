from pathlib import Path

import pytest

from backend.app.models.events import ScenarioDefinition


@pytest.fixture
def scenario_dir():
    return Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture
def definition(scenario_dir):
    return ScenarioDefinition.model_validate_json(
        (scenario_dir / "sc01_node_failure.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def state_payload(definition):
    return definition.initial_state.model_dump(mode="json")
