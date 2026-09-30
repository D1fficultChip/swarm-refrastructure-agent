"""Protocol-independent policy loop. Only the P4 transaction publishes plans."""
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

from ..reconstruction.common import digest
from ..reconstruction.engine import DeterministicReconstructionEngine, ReconstructionError
from .action import ActionRejected, AgentActionValidator
from .config import AgentConfig
from .context_compiler import ContextCompiler
from .models import (AgentExecutionTrace, AgentReconstructionResult, AgentState, AgentTiming, AgentTraceStep)
from .providers.base import ModelError
from .providers.mock import MockModelProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .security import redact
from .tool_guard import ToolGuard
from .tool_registry import ToolExecutor, tool_registry, tool_schemas
from .workspace import AgentWorkspace


def configured_provider(config):
    # Mock mode must receive an explicit script; never disguise a rule policy as an LLM.
    return MockModelProvider([]) if config.provider == "mock" else OpenAICompatibleProvider(config)


class AgentReconstructionOrchestrator:
    def __init__(self, store, provider=None, config=None, *, primitives=None):
        self.store = store
        self.config = config or AgentConfig.load()
        self.provider = provider or configured_provider(self.config)
        self.primitives = primitives

    def run(self, session_id, expected_version, mode="agent", objective="Restore ACTIVE tasks with minimal disruption.",
            initial_proposal=None):
        if mode not in {"agent", "deterministic"}:
            raise ValueError("Unknown policy mode")
        started = perf_counter()
        config = self.config
        w = AgentWorkspace(self.store, session_id, self.primitives)
        if w.base.version != expected_version:
            raise ReconstructionError("STALE_PROPOSAL", "Expected version differs from current state")
        if initial_proposal is not None:
            w.install_initial(initial_proposal)
        now = datetime.now(timezone.utc)
        state = AgentState(session_id=session_id, base_state_version=w.base.version, current_state_version=w.base.version,
            objective=redact(objective), current_scope=w.scope, max_steps=config.max_agent_steps,
            start_time=now, deadline=now+timedelta(seconds=config.max_total_seconds))
        trace = AgentExecutionTrace(trace_id=str(uuid4()), agent_run_id=state.agent_run_id, session_id=session_id,
            policy_mode="deterministic" if mode == "deterministic" else "mock" if isinstance(self.provider, MockModelProvider) else "agent")
        timing = AgentTiming()
        executor = ToolExecutor(w, state, config)
        executor._sync()
        registry = tool_registry()
        compiler, validator, guard = ContextCompiler(config.context_budget), AgentActionValidator(), ToolGuard()
        schemas = tool_schemas(registry)
        invalid_actions = 0
        termination = "MAX_STEPS_REACHED"
        fallback_used = False

        def stale():
            if not state.needs_refresh:
                state.stale_restarts += 1
            state.needs_refresh = True
            state.current_state_version = self.store.get(session_id).state.version
            w.validation = None
            w.validated_fingerprint = None

        if mode == "agent":
            for index in range(1, config.max_agent_steps+1):
                if perf_counter()-started >= config.max_total_seconds:
                    termination = "TIME_BUDGET_EXCEEDED"
                    break
                if state.model_calls >= config.max_model_calls:
                    termination = "MAX_MODEL_CALLS_REACHED"
                    break
                state.step_index = index
                begin = perf_counter()
                available = ["get_current_state_summary"] if state.needs_refresh else list(registry)
                if w.proposal is None:
                    available = [n for n in available if n not in {"validate_proposal", "commit_validated_proposal"}]
                elif w.validation is None or w.validation.status == "FAIL":
                    available = [n for n in available if n != "commit_validated_proposal"]
                context = compiler.compile(w, state, available, elapsed_seconds=begin-started)
                timing.context_compile_ms += (perf_counter()-begin)*1000
                action, args, reply, feedback = None, None, None, None
                model_ms = 0
                stop = False
                begin = perf_counter()
                try:
                    reply = self.provider.generate_action(context, schemas,
                        timeout_seconds=max(.001, min(config.model_timeout_seconds,
                            config.max_total_seconds-(perf_counter()-started))),
                        max_attempts=config.max_model_calls-state.model_calls)
                    state.model_calls += reply.attempts
                except ModelError as exc:
                    state.model_calls += exc.attempts
                    termination = "TIME_BUDGET_EXCEEDED" if perf_counter()-started >= config.max_total_seconds else "MODEL_ERROR"
                    observation = executor.adapter.make("model", "ERROR", w, {}, [exc.code])
                    stop = True
                except Exception:
                    state.model_calls += 1
                    termination = "MODEL_ERROR"
                    observation = executor.adapter.make("model", "ERROR", w, {}, ["MODEL_PROVIDER_EXCEPTION"])
                    stop = True
                finally:
                    model_ms = (perf_counter()-begin)*1000
                    timing.model_total_ms += model_ms
                if not stop:
                    if perf_counter()-started >= config.max_total_seconds:
                        observation = executor.adapter.make("budget", "REJECTED", w, {}, ["TIME_BUDGET_EXCEEDED"])
                        termination, stop = "TIME_BUDGET_EXCEEDED", True
                    else:
                        try:
                            action, args = validator.validate(reply.action, registry, w)
                            guard.check(action, args, w, state)
                            if action.action_type == "STOP":
                                if digest(self.store.get(session_id).state) != digest(w.base):
                                    stale()
                                    raise ActionRejected("STALE_PROPOSAL", {"refresh_required": True})
                                if args.reason == "SUCCESS":
                                    raise ActionRejected("COMMIT_RECEIPT_REQUIRED")
                                if args.reason == "NO_RECONSTRUCTION_REQUIRED" and w.tools.get_outstanding_violations(w.base):
                                    raise ActionRejected("OUTSTANDING_VIOLATIONS_REMAIN")
                                termination, stop = args.reason, True
                                observation = executor.adapter.make("STOP", termination, w)
                            else:
                                name = action.tool_name
                                if name == "validate_proposal" and state.validation_attempts >= config.max_validation_retries+1:
                                    termination, stop = "VALIDATION_RETRY_LIMIT", True
                                    observation = executor.adapter.make(name, "REJECTED", w, {}, [termination])
                                else:
                                    state.tool_calls += 1
                                    state.tool_history.append(name)
                                    begin = perf_counter()
                                    try:
                                        observation = executor.execute(name, args)
                                    finally:
                                        field = ("validation_total_ms" if name == "validate_proposal" else
                                                 "commit_ms" if name == "commit_validated_proposal" else "tool_total_ms")
                                        setattr(timing, field, getattr(timing, field)+(perf_counter()-begin)*1000)
                                    if name == "validate_proposal":
                                        feedback = state.validation_feedback
                                        if digest(self.store.get(session_id).state) != digest(w.base):
                                            stale()
                                    if executor.receipt:
                                        termination, stop = "SUCCESS", True
                            invalid_actions = 0
                        except ActionRejected as exc:
                            invalid_actions += 1
                            observation = executor.adapter.make(action.tool_name if action and action.tool_name else "action",
                                "ACTION_REJECTED", w, {**exc.details, "allowed_tools": available}, [exc.code])
                            if exc.code == "STALE_RESTART_LIMIT":
                                termination, stop = "STALE_RESTART_LIMIT", True
                            elif invalid_actions > config.structured_retry_limit:
                                termination, stop = "MODEL_ERROR", True
                        except ReconstructionError as exc:
                            observation = executor.adapter.make(action.tool_name, "REJECTED", w, {}, [exc.code])
                            if exc.code == "STALE_PROPOSAL":
                                stale()
                                observation.data = {"current_version": state.current_state_version, "refresh_required": True}
                            elif exc.validation:
                                feedback = state.validation_feedback = exc.validation
                                w.validation = exc.validation
                                w.validated_fingerprint = None
                                state.validation_attempts += 1
                                observation.data = {"validation": exc.validation.model_dump(mode="json")}
                                if state.validation_attempts > config.max_validation_retries:
                                    termination, stop = "VALIDATION_RETRY_LIMIT", True
                            else:
                                termination, stop = "TOOL_ERROR", True
                        except Exception:
                            # Unexpected adapter faults are fatal, never facts for policy use.
                            observation = executor.adapter.make(action.tool_name if action else "action", "ERROR", w, {}, ["TOOL_EXECUTION_ERROR"])
                            termination, stop = "TOOL_ERROR", True
                state.observations.append(observation)
                prompt_size = None
                if reply:
                    from .policy_models import PromptSizeMetrics
                    prompt_size = (PromptSizeMetrics.model_validate(reply.prompt_metrics) if reply.prompt_metrics else
                                   OpenAICompatibleProvider(config).build_request(context,schemas)[1])
                trace.steps.append(AgentTraceStep(step=index, timestamp=datetime.now(timezone.utc),
                    context_summary=compiler.trace_summary(context), model_name=reply.model_name if reply else self.provider.name,
                    model_latency_ms=model_ms, token_usage=reply.token_usage if reply else {}, action=action,
                    tool_name=action.tool_name if action else None, tool_arguments=action.arguments if action else {},
                    observation=observation, validation_feedback=feedback, state_version=state.current_state_version,
                    working_proposal_id=w.proposal.proposal_id if w.proposal else None,prompt_size=prompt_size))
                if state.stale_restarts > config.max_stale_restarts:
                    termination, stop = "STALE_RESTART_LIMIT", True
                if stop:
                    break

        # Operational recovery is explicit, outside the model's registered tools.
        should_fallback = config.fallback_enabled and termination in {"MODEL_ERROR", "MAX_STEPS_REACHED",
            "MAX_MODEL_CALLS_REACHED", "TOOL_ERROR"} and perf_counter()-started < config.max_total_seconds
        if mode == "deterministic" or should_fallback:
            fallback_used = mode != "deterministic"
            trace.fallback_reason = termination if fallback_used else None
            begin = perf_counter()
            try:
                baseline_version = self.store.get(session_id).state.version if fallback_used else expected_version
                result = DeterministicReconstructionEngine(self.store).reconstruct(session_id, baseline_version)
                w.proposal, w.validation = result.proposal, result.validation
                executor.receipt = result.receipt
                state.reconstruction_id = result.reconstruction_id
                state.current_state_version = result.state.version
                termination = ("FALLBACK_" if fallback_used else "DETERMINISTIC_") + ("SUCCESS" if result.committed else "FAILED")
                observation = executor.adapter.make("deterministic_fallback" if fallback_used else "deterministic_baseline",
                    termination, w, {"reconstruction_id": result.reconstruction_id,
                        "validation_status": result.validation.status.value, "committed": result.committed})
            except ReconstructionError as exc:
                termination = "FALLBACK_FAILED" if fallback_used else "DETERMINISTIC_FAILED"
                observation = executor.adapter.make("deterministic_fallback" if fallback_used else "deterministic_baseline",
                    termination, w, {}, [exc.code])
            except Exception:
                termination = "FALLBACK_FAILED" if fallback_used else "DETERMINISTIC_FAILED"
                observation = executor.adapter.make("deterministic_fallback" if fallback_used else "deterministic_baseline",
                    termination, w, {}, ["BASELINE_EXECUTION_ERROR"])
            if fallback_used:
                timing.fallback_ms = (perf_counter()-begin)*1000
            else:
                timing.deterministic_baseline_ms = (perf_counter()-begin)*1000
            trace.steps.append(AgentTraceStep(step=len(trace.steps)+1, timestamp=datetime.now(timezone.utc),
                context_summary={"policy": "deterministic", "trigger": trace.fallback_reason}, model_name="none",
                observation=observation, state_version=state.current_state_version,
                working_proposal_id=w.proposal.proposal_id if w.proposal else None))
        state.status, state.termination_reason = "TERMINATED", termination
        trace.termination, trace.scope_expansions = termination, state.scope_expansions
        timing.agent_total_ms = (perf_counter()-started)*1000
        current = self.store.get(session_id).state
        return AgentReconstructionResult(agent_run_id=state.agent_run_id, reconstruction_id=state.reconstruction_id,
            status="COMMITTED" if executor.receipt else "NO_CHANGE" if termination == "NO_RECONSTRUCTION_REQUIRED" else "STOPPED",
            termination_reason=termination, initial_state_version=expected_version, final_state_version=current.version,
            steps=len(trace.steps), model_calls=state.model_calls, tool_calls=state.tool_calls, solver_calls=state.solver_calls,
            validation_attempts=state.validation_attempts, proposal_id=w.proposal.proposal_id if w.proposal else None,
            validation_status=w.validation.status.value if w.validation else None, commit_receipt=executor.receipt,
            fallback_used=fallback_used, timing=timing, trace_id=trace.trace_id, trace=trace, proposal=w.proposal,
            final_outstanding_violations=w.tools.get_outstanding_violations(current),
            pure_agent_success=termination in {"SUCCESS","NO_RECONSTRUCTION_REQUIRED"},
            fallback_success=termination=="FALLBACK_SUCCESS",
            overall_success=bool(executor.receipt) or termination=="NO_RECONSTRUCTION_REQUIRED")
