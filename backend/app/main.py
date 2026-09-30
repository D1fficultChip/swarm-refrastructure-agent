from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .events.injector import EventApplicationError
from .models.phase2 import EventErrorCode
from .scenarios.manager import ScenarioManager
from .reconstruction.engine import ReconstructionError
from .agent.config import AgentConfig
from .agent.orchestrator import configured_provider
from .demo.api import router as demo_router
from .demo.service import DemoApplicationService, DemoError
from .dynamic.api import router as dynamic_router
from .dynamic.service import DynamicEnvironmentService, DynamicError

DEFAULT_SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"


def create_app(scenario_dir: Path | None = None, *, agent_provider_factory=None, agent_config=None,
               demo_output_dir=None, dynamic_output_dir=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.scenario_manager = ScenarioManager(
            scenario_dir if scenario_dir is not None else DEFAULT_SCENARIO_DIR
        )
        application.state.agent_config = agent_config or AgentConfig.load()
        application.state.agent_provider_factory = agent_provider_factory or configured_provider
        application.state.agent_runs = {}
        application.state.demo_service = DemoApplicationService(output_dir=demo_output_dir,
            config=application.state.agent_config,provider_factory=application.state.agent_provider_factory)
        application.state.dynamic_service = DynamicEnvironmentService(application.state.demo_service,output_dir=dynamic_output_dir)
        yield
        application.state.demo_service.close()

    application = FastAPI(
        title="集群任务重构智能体 Demo",
        version="0.6.2",
        description="Phase 6.2：标准验证、动态任务环境、持续在线重构与结构化回放。",
        lifespan=lifespan,
    )
    application.include_router(router)
    application.include_router(demo_router)
    application.include_router(dynamic_router)

    @application.get("/healthz",include_in_schema=False)
    async def healthz():
        return {"status":"ok","version":application.version}

    @application.exception_handler(DynamicError)
    async def dynamic_error_handler(request,exc:DynamicError):
        return JSONResponse(status_code=exc.status,content={"detail":{"code":exc.code,"message":str(exc)}})

    @application.exception_handler(DemoError)
    async def demo_error_handler(request,exc:DemoError):
        return JSONResponse(status_code=exc.status,content={"detail":{"code":exc.code,"message":str(exc)}})

    @application.exception_handler(ReconstructionError)
    async def reconstruction_error_handler(request, exc: ReconstructionError):
        detail = {"code": exc.code, "message": str(exc)}
        if exc.validation is not None:
            detail["validation"] = exc.validation.model_dump(mode="json")
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    @application.exception_handler(EventApplicationError)
    async def event_error_handler(request, exc: EventApplicationError):
        status = 404 if exc.code in {EventErrorCode.TARGET_NOT_FOUND, EventErrorCode.ASSESSMENT_NOT_FOUND} else (
            422 if exc.code == EventErrorCode.INVALID_EVENT_STATE else 409
        )
        return JSONResponse(status_code=status, content={"detail": {"code": exc.code.value, "message": str(exc)}})

    frontend_dist=Path(os.environ.get("FRONTEND_DIST",Path(__file__).resolve().parents[2]/"frontend/dist"))
    if frontend_dist.is_dir():
        # Registered last so every API and health route keeps precedence.
        application.mount("/",StaticFiles(directory=frontend_dist,html=True),name="frontend")

    return application


app = create_app()
