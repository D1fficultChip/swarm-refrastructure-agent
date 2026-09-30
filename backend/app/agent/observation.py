from ..models.phase4 import ValidationResult
from .models import ToolObservation
from .security import redact


class ObservationAdapter:
    def make(self, tool, status, workspace, data=None, reason_codes=()):
        return ToolObservation(tool=tool, status=status, data=redact(data or {}), reason_codes=redact(list(reason_codes)),
            state_version=workspace.base.version, proposal_id=workspace.proposal.proposal_id if workspace.proposal else None)

    def validation(self, workspace, result: ValidationResult):
        observation = self.make("validate_proposal", result.status.value, workspace,
            {"validation_id": result.validation_id, "hard_failures": [c.model_dump(mode="json") for c in result.hard_failures],
             "not_evaluated": result.not_evaluated, "metrics": result.metrics.model_dump(mode="json")},
            sorted({c.code for c in result.hard_failures}))
        observation.validation_id = result.validation_id
        return observation
