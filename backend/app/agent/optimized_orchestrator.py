"""P5.1 model decisions over stable options; mechanical guards run automatically."""
from datetime import datetime,timedelta,timezone
from time import perf_counter
from uuid import uuid4

from ..reconstruction.common import digest
from ..reconstruction.engine import DeterministicReconstructionEngine, ReconstructionError
from .action import ActionRejected, AgentActionValidator, EmptyArgs
from .config import AgentConfig
from .decision_snapshot import DecisionSnapshotBuilder, DeltaContextCompiler, compact, option_summary
from .models import AgentAction, AgentExecutionTrace, AgentReconstructionResult, AgentState, AgentTiming, AgentTraceStep
from .options import DominanceGuard, OptionCatalog
from .orchestrator import AgentReconstructionOrchestrator
from .policy_models import PolicyDecisionRecord, PromptSizeMetrics
from .policy_router import PolicyModelRouter
from .policy_tools import parse_policy_action, policy_registry, reason_consistency
from .providers.base import ModelError
from .providers.mock import MockModelProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .security import redact
from .tool_guard import ToolGuard
from .tool_registry import ToolExecutor, tool_schemas
from .workspace import AgentWorkspace


class OptimizedAgentOrchestrator:
    def __init__(self, store, provider=None, config=None, *, strong_provider=None, primitives=None):
        self.store, self.config, self.primitives = store, config or AgentConfig.load(), primitives
        config = self.config
        self.fast = provider or OpenAICompatibleProvider(config.model_copy(update={"model_name":config.fast_model_name or config.model_name}))
        self.strong = strong_provider or (provider if provider else OpenAICompatibleProvider(
            config.model_copy(update={"model_name":config.strong_model_name or config.model_name})))

    def run(self, session_id, expected_version, mode="agent", objective="Restore ACTIVE tasks with minimal lexicographic disruption.", initial_proposal=None):
        config = self.config
        mode = config.execution_mode if mode=="agent" else {"deterministic":"deterministic_realtime"}.get(mode,mode)
        if mode=="deterministic_realtime":
            result = AgentReconstructionOrchestrator(self.store,self.fast,config).run(session_id,expected_version,mode="deterministic")
            result.execution_mode, result.policy_profile = mode,config.policy_profile
            result.overall_success = result.status=="COMMITTED"
            return result
        if mode not in {"adaptive_agent","bounded_agent"}:
            raise ValueError("Unknown execution mode")
        started = perf_counter()
        total_deadline = started+config.max_total_seconds
        policy_deadline = min(total_deadline,started+config.bounded_agent_seconds) if mode=="bounded_agent" else total_deadline
        max_calls = min(config.max_model_calls,config.bounded_max_model_calls) if mode=="bounded_agent" else config.max_model_calls
        w = AgentWorkspace(self.store,session_id,self.primitives)
        if w.base.version != expected_version:
            raise ReconstructionError("STALE_PROPOSAL","Expected version differs from current state")
        if initial_proposal:
            w.install_initial(initial_proposal)
        now = datetime.now(timezone.utc)
        state = AgentState(session_id=session_id,base_state_version=w.base.version,current_state_version=w.base.version,
            objective=redact(objective),current_scope=w.scope,max_steps=config.max_agent_steps,
            start_time=now,deadline=now+timedelta(seconds=config.max_total_seconds))
        trace = AgentExecutionTrace(trace_id=str(uuid4()),agent_run_id=state.agent_run_id,session_id=session_id,
            policy_mode="mock" if isinstance(self.fast,MockModelProvider) else "agent")
        executor = ToolExecutor(w,state,config)
        catalog = OptionCatalog(config)
        builder,compiler = DecisionSnapshotBuilder(config),DeltaContextCompiler(config)
        router = PolicyModelRouter(config,self.fast,self.strong)
        registry = policy_registry()
        schemas = tool_schemas(registry)
        timing = AgentTiming()
        invalid = 0
        termination = "MAX_STEPS_REACHED"

        def mark_stale():
            if not state.needs_refresh:
                state.stale_restarts += 1
            state.needs_refresh = True
            state.current_state_version = self.store.get(session_id).state.version
            w.validation,w.validated_fingerprint = None,None

        def execute(name,args,record,automatic=False):
            if perf_counter() >= total_deadline:
                raise ActionRejected("TIME_BUDGET_EXCEEDED")
            if name=="validate_proposal" and state.validation_attempts>=config.max_validation_retries+1:
                raise ActionRejected("VALIDATION_RETRY_LIMIT")
            synthetic = AgentAction(action_type="CALL_TOOL",tool_name=name,decision_reason="Mechanical guard or typed tool action.")
            ToolGuard().check(synthetic,args,w,state)
            begin = perf_counter()
            state.tool_calls += 1
            state.tool_history.append(name)
            try:
                observation = executor.execute(name,args)
            finally:
                field = "validation_total_ms" if name=="validate_proposal" else "commit_ms" if name=="commit_validated_proposal" else "tool_total_ms"
                setattr(timing,field,getattr(timing,field)+(perf_counter()-begin)*1000)
            if automatic:
                record.automatic_steps.append(observation.model_dump(mode="json"))
            return observation

        def finalize(record):
            observation = execute("validate_proposal",EmptyArgs(),record,True)
            if digest(self.store.get(session_id).state)!=digest(w.base):
                mark_stale()
            if observation.status!="FAIL" and config.auto_commit_after_validation:
                observation = execute("commit_validated_proposal",EmptyArgs(),record,True)
            return observation

        for index in range(1,config.max_agent_steps+1):
            if perf_counter()>=policy_deadline:
                termination="TIME_BUDGET_EXCEEDED"
                break
            if state.model_calls>=max_calls:
                termination="MAX_MODEL_CALLS_REACHED"
                break
            state.step_index=index
            decision_deadline=min(policy_deadline,perf_counter()+config.decision_time_budget)
            calls,ms=catalog.prepare(w,state.validation_feedback)
            timing.option_generation_ms+=ms
            state.solver_calls+=calls
            begin=perf_counter()
            snapshot=builder.build(w,state,catalog)
            context=compiler.compile(snapshot,state,remaining_seconds=max(0,policy_deadline-perf_counter()),calls_remaining=max_calls-state.model_calls)
            timing.context_compile_ms+=(perf_counter()-begin)*1000
            provider,tier,router_reasons=router.choose(snapshot,invalid)
            record=PolicyDecisionRecord(decision_id=str(uuid4()),model=provider.name,model_tier=tier,
                router_reasons=router_reasons,decision_snapshot_hash=snapshot.snapshot_hash,
                non_dominated_options=[o.option_id for o in catalog.current if not o.dominated_by],
                validator_feedback=snapshot.validator_feedback,available_options=[option_summary(o) for o in catalog.current],
                preparation_solver_calls=calls,preparation_ms=ms)
            action,reply,feedback=None,None,None
            model_ms=0
            stop=False
            try:
                remaining=decision_deadline-perf_counter()
                if remaining<=0:
                    raise ActionRejected("DECISION_TIME_BUDGET_EXCEEDED")
                begin=perf_counter()
                try:
                    reply=provider.generate_action(context,schemas,timeout_seconds=min(remaining,config.model_timeout_seconds),max_attempts=max_calls-state.model_calls)
                    state.model_calls+=reply.attempts
                except ModelError as exc:
                    state.model_calls+=exc.attempts
                    raise
                finally:
                    model_ms=(perf_counter()-begin)*1000
                    timing.model_total_ms+=model_ms
                record.model=reply.model_name
                measured=getattr(reply,"prompt_metrics",None)
                record.prompt_size=(PromptSizeMetrics.model_validate(measured) if measured else
                    OpenAICompatibleProvider(config).build_request(context,schemas)[1])
                if perf_counter()>=decision_deadline:
                    raise ActionRejected("DECISION_TIME_BUDGET_EXCEEDED")
                action,args=parse_policy_action(reply.action,registry,w)
                if state.needs_refresh and action.tool_name!="get_current_state_summary":
                    raise ActionRejected("STATE_REFRESH_REQUIRED")
                if config.policy_profile=="snapshot" and (action.finalize or action.action_type=="FINALIZE_PROPOSAL"):
                    raise ActionRejected("AUTO_FINALIZATION_DISABLED")
                selected_id=action.option_id if action.action_type=="SELECT_OPTION" else (
                    args.option_id if action.tool_name=="apply_reconstruction_option" else None)
                bound=catalog.resolve(selected_id,w) if selected_id else None
                if bound:
                    option=bound.public
                    record.selected_option,record.selected_tool=option.option_id,"apply_reconstruction_option"
                    record.selected_targets,record.disruption_cost=option.target_tasks,option.disruption_cost_vector
                    record.reason_argument_consistency=reason_consistency(action.decision_reason,option,[n.id for n in w.base.nodes])
                    if record.reason_argument_consistency is False:
                        record.warnings.append("REASON_ARGUMENT_INCONSISTENCY")
                    if config.policy_profile=="optimized":
                        DominanceGuard().check(option)
                # Preflight every expansion before ANY private mutation. A bundle
                # is one decision revision; handles bind to the pre-bundle state.
                expansions=[]
                for expansion in action.scope_expansions:
                    legacy,params=AgentActionValidator().validate({"action_type":"CALL_TOOL","tool_name":"request_scope_expansion",
                        "arguments":expansion.model_dump(),"decision_reason":expansion.reason},registry,w)
                    ToolGuard().check(legacy,params,w,state)
                    expansions.append(params)
                if bound and not set(bound.public.target_tasks)<=set(w.scope.affected_tasks)|{e.entity.split(":")[1] for e in expansions if e.entity.startswith("task:")}:
                    raise ActionRejected("SCOPE_EXPANSION_REQUIRED")
                for params in expansions:
                    execute("request_scope_expansion",params,record,True)
                if bound:
                    begin=perf_counter()
                    catalog.apply(bound,w)
                    executor._sync()
                    state.tool_calls+=1
                    state.tool_history.append("apply_reconstruction_option")
                    timing.tool_total_ms+=(perf_counter()-begin)*1000
                    observation=executor.adapter.make("apply_reconstruction_option","APPLIED",w,
                        {"option_id":bound.public.option_id,"added_nodes":bound.public.added_nodes,
                         "target_tasks":bound.public.target_tasks,"cost":bound.public.disruption_cost_vector})
                elif action.action_type=="FINALIZE_PROPOSAL":
                    observation=finalize(record)
                elif action.action_type=="STOP":
                    if digest(self.store.get(session_id).state)!=digest(w.base):
                        mark_stale()
                        raise ActionRejected("STALE_PROPOSAL")
                    if args.reason=="SUCCESS":
                        raise ActionRejected("COMMIT_RECEIPT_REQUIRED")
                    if args.reason=="NO_RECONSTRUCTION_REQUIRED" and w.tools.get_outstanding_violations(w.base):
                        raise ActionRejected("OUTSTANDING_VIOLATIONS_REMAIN")
                    termination,stop=args.reason,True
                    observation=executor.adapter.make("STOP",termination,w)
                elif action.tool_name in {"get_reconstruction_options","try_in_place_repair","find_task_reassignment","try_formation_reconstruction"}:
                    task_id=getattr(args,"task_id",None)
                    strategy=getattr(args,"strategy",None) or {"try_in_place_repair":"IN_PLACE_REPAIR",
                        "find_task_reassignment":"TASK_REASSIGNMENT","try_formation_reconstruction":"FORMATION_RECONSTRUCTION"}.get(action.tool_name)
                    if strategy is not None and strategy not in OptionCatalog.METHODS:
                        raise ActionRejected("UNKNOWN_STRATEGY")
                    matching=[o for o in catalog.current if (not task_id or task_id in o.target_tasks) and (not strategy or strategy==o.strategy_type)]
                    state.tool_calls+=1
                    observation=executor.adapter.make(action.tool_name,"OK",w,{"option_set_id":catalog.option_set_id,"options":[option_summary(o) for o in matching]})
                else:
                    observation=execute(action.tool_name,args,record)
                if action.finalize and action.action_type!="FINALIZE_PROPOSAL" and not stop:
                    record.automatic_steps.insert(0,observation.model_dump(mode="json"))
                    observation=finalize(record)
                if executor.receipt:
                    termination,stop="SUCCESS",True
                elif record.automatic_steps and w.validation and w.validation.status!="FAIL" and not config.auto_commit_after_validation:
                    termination,stop="VALIDATED_NOT_COMMITTED",True
                if action.tool_name=="validate_proposal" and digest(self.store.get(session_id).state)!=digest(w.base):
                    mark_stale()
                feedback=state.validation_feedback if (action.finalize or action.action_type=="FINALIZE_PROPOSAL" or action.tool_name=="validate_proposal") else None
                invalid=0
            except ActionRejected as exc:
                action=action or getattr(exc,"action",None)
                observation=executor.adapter.make("action","ACTION_REJECTED",w,exc.details,[exc.code])
                invalid+=1
                if exc.code in {"STALE_OPTION","STALE_PROPOSAL"} and digest(self.store.get(session_id).state)!=digest(w.base):
                    mark_stale()
                if exc.code in {"TIME_BUDGET_EXCEEDED","DECISION_TIME_BUDGET_EXCEEDED","VALIDATION_RETRY_LIMIT","STALE_RESTART_LIMIT"}:
                    termination,stop=exc.code,True
                elif invalid>config.structured_retry_limit:
                    termination,stop="MODEL_ERROR",True
            except ModelError as exc:
                observation=executor.adapter.make("model","ERROR",w,{},[exc.code])
                termination,stop=("DECISION_TIME_BUDGET_EXCEEDED" if perf_counter()>=decision_deadline else "MODEL_ERROR"),True
            except ReconstructionError as exc:
                observation=executor.adapter.make(action.tool_name or action.action_type,"REJECTED",w,{},[exc.code])
                if exc.code=="STALE_PROPOSAL":
                    mark_stale()
                elif exc.validation:
                    feedback=state.validation_feedback=exc.validation
                    w.validation=exc.validation
                    w.validated_fingerprint=None
                    w.last_failed_plan_digest=digest(w.working)
                    state.validation_attempts+=1
                    observation.data={"feedback":exc.validation.model_dump(mode="json")}
                else:
                    termination,stop="TOOL_ERROR",True
            except Exception as exc:
                observation=executor.adapter.make("policy","ERROR",w,{"exception_type":type(exc).__name__},["TOOL_EXECUTION_ERROR"])
                termination,stop="TOOL_ERROR",True
            record.selected_tool=record.selected_tool or (action.tool_name or action.action_type if action else None)
            if action and not record.selected_targets and action.arguments.get("task_id"):
                record.selected_targets=[action.arguments["task_id"]]
            state.observations.append(observation)
            trace.steps.append(AgentTraceStep(step=index,timestamp=datetime.now(timezone.utc),
                context_summary={"snapshot_hash":snapshot.snapshot_hash,"serialized_chars":len(compact(context)),
                    "kind":context["context_kind"],"options_omitted":context["options_omitted"],"truncated":context["truncated"]},
                model_name=reply.model_name if reply else provider.name,model_latency_ms=model_ms,
                token_usage=reply.token_usage if reply else {},action=action,tool_name=record.selected_tool,
                tool_arguments=action.arguments if action else {},observation=observation,validation_feedback=feedback,
                state_version=state.current_state_version,working_proposal_id=w.proposal.proposal_id if w.proposal else None,
                decision_record=record))
            if state.stale_restarts>config.max_stale_restarts:
                termination,stop="STALE_RESTART_LIMIT",True
            if stop:
                break
        timing.pure_agent_ms=(perf_counter()-started)*1000
        fallback_used=False
        operational_failure=termination in {"MODEL_ERROR","TOOL_ERROR","MAX_MODEL_CALLS_REACHED","MAX_STEPS_REACHED",
                                            "TIME_BUDGET_EXCEEDED","DECISION_TIME_BUDGET_EXCEEDED"}
        if operational_failure and (mode=="bounded_agent" or config.fallback_enabled) and perf_counter()<total_deadline:
            fallback_used=True
            trace.fallback_reason=termination
            begin=perf_counter()
            try:
                result=DeterministicReconstructionEngine(self.store).reconstruct(session_id,self.store.get(session_id).state.version)
                w.proposal,w.validation=result.proposal,result.validation
                executor.receipt=result.receipt
                state.reconstruction_id=result.reconstruction_id
                termination="FALLBACK_SUCCESS" if result.committed else "FALLBACK_FAILED"
                data={"reconstruction_id":result.reconstruction_id,"committed":result.committed}
            except Exception:
                termination="FALLBACK_FAILED"
                data={"code":"FALLBACK_EXECUTION_ERROR"}
            timing.fallback_ms=(perf_counter()-begin)*1000
            trace.steps.append(AgentTraceStep(step=len(trace.steps)+1,timestamp=datetime.now(timezone.utc),
                context_summary={"trigger":trace.fallback_reason},model_name="none",
                observation=executor.adapter.make("deterministic_fallback",termination,w,data),
                state_version=self.store.get(session_id).state.version))
        timing.agent_total_ms=(perf_counter()-started)*1000
        trace.termination,trace.scope_expansions=termination,state.scope_expansions
        current=self.store.get(session_id).state
        return AgentReconstructionResult(agent_run_id=state.agent_run_id,reconstruction_id=state.reconstruction_id,
            status="COMMITTED" if executor.receipt else "NO_CHANGE" if termination=="NO_RECONSTRUCTION_REQUIRED" else "STOPPED",
            termination_reason=termination,initial_state_version=expected_version,final_state_version=current.version,
            steps=len(trace.steps),model_calls=state.model_calls,tool_calls=state.tool_calls,solver_calls=state.solver_calls,
            validation_attempts=state.validation_attempts,proposal_id=w.proposal.proposal_id if w.proposal else None,
            validation_status=w.validation.status.value if w.validation else None,commit_receipt=executor.receipt,
            fallback_used=fallback_used,timing=timing,trace_id=trace.trace_id,trace=trace,proposal=w.proposal,
            final_outstanding_violations=w.tools.get_outstanding_violations(current),execution_mode=mode,policy_profile=config.policy_profile,
            pure_agent_success=termination in {"SUCCESS","NO_RECONSTRUCTION_REQUIRED"},fallback_success=termination=="FALLBACK_SUCCESS",
            overall_success=bool(executor.receipt) or termination=="NO_RECONSTRUCTION_REQUIRED")
