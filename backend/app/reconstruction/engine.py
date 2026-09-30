"""Versioned atomic commit and deterministic orchestration, without model APIs."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from ..assessment.service import process_event
from ..models.api import ScenarioSnapshot
from ..models.phase3 import ReconstructionProposal
from ..models.phase4 import (AppliedDeltas, ReconstructionCommitReceipt, ReconstructionResult,
                             ReconstructionTiming, ValidationStatus)
from .common import digest
from .materializer import ProposalMaterializer
from .planner import HierarchicalReconstructionPlanner
from .store import SessionRecord
from .validator import GlobalConstraintValidator


class ReconstructionError(ValueError):
    def __init__(self, code, message, status_code=409, validation=None):
        super().__init__(message)
        self.code, self.status_code, self.validation = code, status_code, validation


def proposal_digest(proposal):
    return hashlib.sha256(json.dumps(proposal.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class TransactionalReconstructionEngine:
    def __init__(self, store):
        self.store = store

    def commit(self, session_id, proposal, reconstruction_id=None, timing=None, started=None):
        begin = perf_counter()
        started = begin if started is None else started
        # Take ownership before hashing: caller mutation cannot change content under the lock.
        proposal = ReconstructionProposal.model_validate(proposal.model_dump(mode="json"))
        fingerprint = proposal_digest(proposal)
        with self.store._lock:
            record = self.store._sessions.get(session_id)
            if record is None:
                raise ReconstructionError("SESSION_NOT_FOUND", "Session not found", 404)
            previous = record.commits.get(proposal.proposal_id)
            if previous:
                if previous[0] != fingerprint:
                    raise ReconstructionError("PROPOSAL_ID_CONFLICT", "Proposal ID was committed with different content")
                receipt = record.reconstructions[previous[1]].receipt.model_copy(deep=True)
                receipt.replayed = True
                return receipt
            observed = record.snapshot.state
            if observed.version != proposal.base_state_version or digest(observed) != proposal.base_state_digest:
                raise ReconstructionError("STALE_PROPOSAL", "Version or digest changed; assess and propose again")
            validation = GlobalConstraintValidator().validate_proposal(observed, proposal)
            if validation.status == ValidationStatus.FAIL:
                raise ReconstructionError("VALIDATION_FAILED", "Global validation rejected proposal", 422, validation)
            candidate = ProposalMaterializer().materialize(observed, proposal)
            if digest(candidate) != validation.candidate_state_digest:
                raise ReconstructionError("CANDIDATE_DIGEST_MISMATCH", "Materialized candidate differs from validation", 422)
            candidate.version = observed.version + 1
            reconstruction_id = reconstruction_id or str(uuid4())
            metrics = timing.model_copy(deep=True) if timing else ReconstructionTiming()
            metrics.validation_ms = validation.validation_time_ms
            receipt = ReconstructionCommitReceipt(reconstruction_id=reconstruction_id, proposal_id=proposal.proposal_id,
                validation_id=validation.validation_id, base_version=observed.version, committed_version=candidate.version,
                candidate_state_digest=validation.candidate_state_digest, committed_state_digest=digest(candidate),
                applied_deltas=AppliedDeltas(**{k: getattr(proposal, k) for k in AppliedDeltas.model_fields}),
                validation_status=validation.status, validation_summary=validation.metrics,
                committed_at=datetime.now(timezone.utc))
            snapshot = record.snapshot.model_copy(deep=True)
            snapshot.state = candidate
            outcome = ReconstructionResult(reconstruction_id=reconstruction_id, session_id=session_id, committed=True,
                proposal=proposal, validation=validation, receipt=receipt, state=candidate,
                event_traces=list(record.reports.values()), metrics=metrics).model_copy(deep=True)
            # All potentially failing construction/copy work precedes the single publication.
            response = receipt.model_copy(deep=True)
            replacement = replace(record, snapshot=snapshot,
                commits={**record.commits, proposal.proposal_id: (fingerprint, reconstruction_id)},
                reconstructions={**record.reconstructions, reconstruction_id: outcome})
            commit_ms = max(0, (perf_counter() - begin) * 1000 - validation.validation_time_ms)
            response.commit_ms = commit_ms
            outcome.receipt.commit_ms = commit_ms
            outcome.metrics.commit_ms = commit_ms
            outcome.metrics.deterministic_total_ms = (perf_counter() - started) * 1000
            self.store._sessions[session_id] = replacement
            return response


class DeterministicReconstructionEngine:
    """Deterministic baseline, model-API fallback and regression test engine.

    Also the comparison baseline for future Agent Policies. This is one caller
    of deterministic capabilities, not the exclusive high-level control entry.
    Policies may call Phase 2–4 primitives directly and independently submit a
    Proposal to the same global validator and transactional commit boundary.
    Model availability detection and Agent Policy orchestration belong to Phase 5.
    """

    def __init__(self, store):
        self.store = store

    def reconstruct(self, session_id, expected_version, commit=True):
        started = perf_counter()
        with self.store._lock:
            record = self.store._sessions.get(session_id)
            if record is None:
                raise ReconstructionError("SESSION_NOT_FOUND", "Session not found", 404)
            observed = record.snapshot.state.model_copy(deep=True)
            reports = [r.model_copy(deep=True) for r in record.reports.values()]
            if observed.version != expected_version:
                raise ReconstructionError("STALE_PROPOSAL", "Expected version differs from current state")
        proposal_start = perf_counter()
        proposal = HierarchicalReconstructionPlanner().propose(observed, reports)
        timing = ReconstructionTiming(proposal_ms=(perf_counter() - proposal_start) * 1000)
        reconstruction_id = str(uuid4())
        if commit:
            # The transaction performs the authoritative validation under the lock.
            try:
                receipt = TransactionalReconstructionEngine(self.store).commit(
                    session_id, proposal, reconstruction_id, timing, started)
            except ReconstructionError as exc:
                if exc.code != "VALIDATION_FAILED":
                    raise
                validation = exc.validation
            else:
                with self.store._lock:
                    return self.store._sessions[session_id].reconstructions[receipt.reconstruction_id].model_copy(deep=True)
        else:
            validation = GlobalConstraintValidator().validate_proposal(observed, proposal)
        timing.validation_ms = validation.validation_time_ms
        timing.deterministic_total_ms = (perf_counter() - started) * 1000
        result = ReconstructionResult(reconstruction_id=reconstruction_id, session_id=session_id, committed=False,
            proposal=proposal, validation=validation, state=observed, event_traces=reports, metrics=timing)
        with self.store._lock:
            current = self.store._sessions[session_id]
            if digest(current.snapshot.state) != proposal.base_state_digest:
                raise ReconstructionError("STALE_PROPOSAL", "State changed while reconstruction was being computed")
            stored = result.model_copy(deep=True)
            self.store._sessions[session_id] = replace(current,
                reconstructions={**current.reconstructions, reconstruction_id: stored})
        return result

    def reconstruct_input(self, state, event, commit=True):
        """Compatibility boundary: state is BEFORE event; operate in a private session.

        Returns resulting state, receipt and trace; never changes a loaded session.
        """
        phase2 = process_event(state, event)
        key = str(uuid4())
        private = SimpleNamespace(_lock=RLock(), _sessions={key: SessionRecord(
            snapshot=ScenarioSnapshot(session_id=key, state=phase2.application.state, events=[]),
            reports={event.event_id: phase2})})
        result = DeterministicReconstructionEngine(private).reconstruct(key, phase2.application.state.version, commit)
        result.session_id = None
        with self.store._lock:
            self.store._detached_results[result.reconstruction_id] = result.model_copy(deep=True)
        return result
