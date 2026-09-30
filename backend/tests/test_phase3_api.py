import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.scenarios.manager import ScenarioManager
from backend.app.events.injector import EventApplicationError
from backend.app.models.phase2 import EventErrorCode


def test_propose_api_keeps_observation_and_event_history(scenario_dir):
    with TestClient(create_app(scenario_dir)) as client:
        session=client.post("/api/v1/scenario/load",json={"scenario_id":"SC01"}).json()
        for version,event in enumerate(session["events"]):
            client.post("/api/v1/event/inject",json={"session_id":session["session_id"],
                "expected_version":version,"event":event}).raise_for_status()
        params={"session_id":session["session_id"]}
        before=client.get("/api/v1/scenario/state",params=params).json()
        history=client.post("/api/v1/assessment",json=params).json()
        response=client.post("/api/v1/reconstruction/propose",json={**params,"expected_version":2})
        assert response.status_code==200,response.text
        proposal=response.json()
        assert proposal["solver_status"]=="FEASIBLE"
        assert set(proposal["reconstruction_scope"]["affected_tasks"])=={"T03","T04"}
        assert proposal["committed"] is False
        assert proposal["base_state_version"]==2
        assert client.get("/api/v1/scenario/state",params=params).json()==before
        assert client.post("/api/v1/assessment",json=params).json()==history
        assert client.post("/api/v1/reconstruction/propose",json={**params,"expected_version":1}).status_code==409
        assert client.post("/api/v1/reconstruction/propose",json={"session_id":"unknown","expected_version":0}).status_code==404
        assert client.post("/api/v1/reconstruct",json={"state":before["state"],"event":session["events"][0]}).status_code==409


def test_concurrent_event_invalidates_proposal_base(scenario_dir,monkeypatch):
    from backend.app.reconstruction.planner import HierarchicalReconstructionPlanner
    manager=ScenarioManager(scenario_dir)
    session=manager.load("SC01")
    original=HierarchicalReconstructionPlanner.propose
    def changed_during_search(self,state,receipts):
        proposal=original(self,state,receipts)
        manager.inject(session.session_id,0,session.events[0])
        return proposal
    monkeypatch.setattr(HierarchicalReconstructionPlanner,"propose",changed_during_search)
    with pytest.raises(EventApplicationError) as error:
        manager.propose(session.session_id,0)
    assert error.value.code==EventErrorCode.VERSION_CONFLICT
    assert manager.get(session.session_id).state.version==1
