from ..reconstruction.engine import proposal_digest
from .action import ActionRejected


class ToolGuard:
    def check(self, action, args, workspace, state):
        name = action.tool_name
        if state.needs_refresh and name != "get_current_state_summary":
            raise ActionRejected("STATE_REFRESH_REQUIRED", {"allowed_tools": ["get_current_state_summary"]})
        if action.action_type == "USE_FALLBACK":
            raise ActionRejected("FALLBACK_NOT_ALLOWED_IN_NORMAL_AGENT_MODE")
        if name in {"try_in_place_repair", "find_task_reassignment", "try_formation_reconstruction", "replan_route"}:
            if args.task_id not in workspace.scope.affected_tasks:
                raise ActionRejected("SCOPE_EXPANSION_REQUIRED", {"entity": f"task:{args.task_id}"})
        if name == "validate_proposal" and workspace.proposal is None:
            raise ActionRejected("WORKING_PROPOSAL_REQUIRED")
        if name == "commit_validated_proposal":
            validation, proposal = workspace.validation, workspace.proposal
            if proposal is None or validation is None or validation.status not in {"PASS", "PASS_WITH_LIMITATIONS"}:
                raise ActionRejected("AUTHORITATIVE_VALIDATION_REQUIRED")
            if validation.proposal_id != proposal.proposal_id or workspace.validated_fingerprint != proposal_digest(proposal):
                raise ActionRejected("VALIDATED_PROPOSAL_CHANGED")
        if name == "request_scope_expansion" and args.source_validation_id is not None:
            feedback = state.validation_feedback
            if feedback is None or args.source_validation_id not in {"latest", feedback.validation_id}:
                raise ActionRejected("UNKNOWN_VALIDATION_REFERENCE")
