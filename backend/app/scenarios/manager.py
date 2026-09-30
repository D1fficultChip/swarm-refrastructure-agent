import json
from dataclasses import replace
from pathlib import Path
from threading import RLock
from uuid import uuid4

from ..adapters.io import InputAdapter, OutputAdapter
from ..assessment.service import process_event
from ..events.injector import EventApplicationError
from ..models.api import ScenarioSnapshot, ScenarioSummary
from ..models.events import Event, ScenarioDefinition
from ..models.domain import Position
from ..models.phase2 import EventErrorCode, Phase2Result
from ..models.phase3 import ReconstructionProposal
from ..reconstruction.planner import HierarchicalReconstructionPlanner
from ..reconstruction.store import SessionRecord


class ScenarioNotFoundError(LookupError):
    pass


class SessionNotFoundError(LookupError):
    pass


class ScenarioManager:
    """Single-process demo store. Returned snapshots never alias stored state.

    Filenames are indexed by validated IDs on startup. Request IDs are never
    interpolated into filesystem paths. Invalid fixture files fail fast.
    """

    def __init__(self, directory: Path):
        self._definitions: dict[str, ScenarioDefinition] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._detached_results = {}
        self._lock = RLock()
        self._input = InputAdapter()
        self._output = OutputAdapter()
        if not directory.is_dir():
            raise ValueError(f"scenario directory does not exist: {directory}")
        for path in sorted(directory.glob("*.json")):
            definition = ScenarioDefinition.model_validate(json.loads(path.read_text(encoding="utf-8")))
            scenario_id = definition.initial_state.scenario_id
            if scenario_id in self._definitions:
                raise ValueError(f"duplicate scenario id: {scenario_id}")
            self._definitions[scenario_id] = definition
        if not self._definitions:
            raise ValueError("scenario directory contains no JSON scenarios")

    def list_scenarios(self) -> list[ScenarioSummary]:
        return [
            ScenarioSummary(
                scenario_id=key, name=definition.name,
                node_count=len(definition.initial_state.nodes),
                task_count=len(definition.initial_state.tasks),
                formation_count=len(definition.initial_state.formations),
                event_count=len(definition.events),
            )
            for key, definition in sorted(self._definitions.items())
        ]

    def load(self, scenario_id: str) -> ScenarioSnapshot:
        definition = self._definitions.get(scenario_id)
        if definition is None:
            raise ScenarioNotFoundError(scenario_id)
        state = self._input.normalize(definition.initial_state)
        snapshot = ScenarioSnapshot(
            session_id=str(uuid4()), state=state,
            events=[event.model_copy(deep=True) for event in definition.events],
        )
        with self._lock:
            self._sessions[snapshot.session_id] = SessionRecord(snapshot=snapshot)
        return self.get(snapshot.session_id)

    def create(self, definition: ScenarioDefinition) -> ScenarioSnapshot:
        """Create an in-memory session from a validated generated definition."""
        state = self._input.normalize(definition.initial_state)
        snapshot = ScenarioSnapshot(
            session_id=str(uuid4()), state=state,
            events=[event.model_copy(deep=True) for event in definition.events],
        )
        with self._lock:
            self._sessions[snapshot.session_id] = SessionRecord(snapshot=snapshot)
        return self.get(snapshot.session_id)

    def sync_positions(self, session_id: str, expected_version: int,
                       positions: dict[str, Position], simulation_time: float) -> ScenarioSnapshot:
        """Publish one explicit runtime-position snapshot; animation ticks never call this."""
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            state = record.snapshot.state
            if state.version != expected_version:
                raise EventApplicationError(EventErrorCode.VERSION_CONFLICT,
                    f"Expected version {expected_version}; current version is {state.version}")
            known = {node.id for node in state.nodes}
            if not set(positions).issubset(known):
                raise ValueError("runtime snapshot contains an unknown node")
            updated = state.model_copy(deep=True)
            updated.nodes = [node.model_copy(update={"position": positions.get(node.id, node.position)})
                             for node in state.nodes]
            updated.version += 1
            updated.clock = max(updated.clock, simulation_time)
            updated = type(state).model_validate(updated.model_dump())
            snapshot = record.snapshot.model_copy(deep=True)
            snapshot.state = updated
            self._sessions[session_id] = replace(record, snapshot=snapshot)
        return self.get(session_id)

    def get(self, session_id: str) -> ScenarioSnapshot:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            snapshot = record.snapshot
            return ScenarioSnapshot(
                session_id=snapshot.session_id,
                state=self._output.export_state(snapshot.state),
                events=[event.model_copy(deep=True) for event in snapshot.events],
            )

    def inject(self, session_id: str, expected_version: int, event: Event) -> Phase2Result:
        # A single lock serializes compare/apply/analyze/commit. No partially
        # accepted events and no last-writer-wins race in this demo process.
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            record = self._sessions[session_id]
            reports = record.reports
            if event.event_id in reports:
                existing = reports[event.event_id]
                if existing.trace.event != event:
                    raise EventApplicationError(EventErrorCode.EVENT_ID_CONFLICT, "Event ID was used with different contents")
                replay = existing.model_copy(deep=True)
                replay.replayed = True
                return replay
            snapshot = record.snapshot
            if expected_version != snapshot.state.version:
                raise EventApplicationError(EventErrorCode.VERSION_CONFLICT,
                    f"Expected version {expected_version}; current version is {snapshot.state.version}")
            result = process_event(snapshot.state, event)
            result.session_id = session_id
            new_snapshot = snapshot.model_copy(deep=True)
            new_snapshot.state = result.application.state.model_copy(deep=True)
            response = result.model_copy(deep=True)
            self._sessions[session_id] = replace(record, snapshot=new_snapshot,
                                                reports={**reports, event.event_id: result})
            return response

    def assessment(self, session_id: str, event_id: str | None = None) -> Phase2Result:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            reports = self._sessions[session_id].reports
            if not reports:
                raise EventApplicationError(EventErrorCode.ASSESSMENT_NOT_FOUND, "No event has been assessed in this session")
            key = event_id if event_id is not None else next(reversed(reports))
            if key not in reports:
                raise EventApplicationError(EventErrorCode.ASSESSMENT_NOT_FOUND, "Event assessment not found")
            return reports[key].model_copy(deep=True)

    def propose(self, session_id: str, expected_version: int) -> ReconstructionProposal:
        with self._lock:
            snapshot = self.get(session_id)
            if snapshot.state.version != expected_version:
                raise EventApplicationError(EventErrorCode.VERSION_CONFLICT, "Proposal base version is stale")
            receipts = [report.model_copy(deep=True) for report in self._sessions[session_id].reports.values()]
        proposal = HierarchicalReconstructionPlanner().propose(snapshot.state, receipts)
        with self._lock:
            if self._sessions[session_id].snapshot.state.version != expected_version:
                raise EventApplicationError(EventErrorCode.VERSION_CONFLICT, "State changed while proposal was being computed")
        return proposal  # Never writes _sessions or applies proposal deltas.

    def validate(self, session_id, proposal):
        from ..reconstruction.validator import GlobalConstraintValidator
        return GlobalConstraintValidator().validate_proposal(self.get(session_id).state, proposal)

    def commit(self, session_id, proposal):
        from ..reconstruction.engine import TransactionalReconstructionEngine
        return TransactionalReconstructionEngine(self).commit(session_id, proposal)

    def reconstruct(self, session_id, expected_version, commit=True):
        from ..reconstruction.engine import DeterministicReconstructionEngine
        return DeterministicReconstructionEngine(self).reconstruct(session_id, expected_version, commit)

    def reconstruction(self, reconstruction_id):
        from ..reconstruction.engine import ReconstructionError
        with self._lock:
            if reconstruction_id in self._detached_results:
                return self._detached_results[reconstruction_id].model_copy(deep=True)
            for record in self._sessions.values():
                if reconstruction_id in record.reconstructions:
                    return record.reconstructions[reconstruction_id].model_copy(deep=True)
        raise ReconstructionError("RECONSTRUCTION_NOT_FOUND", "Reconstruction trace not found", 404)
