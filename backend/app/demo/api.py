"""REST adapter only; workflow and persistence are in DemoApplicationService."""
from fastapi import APIRouter,Request
from .models import LoadDemoRequest,EventDemoRequest,RunDemoRequest,CommitDemoRequest

router=APIRouter(prefix='/api/v1/demo',tags=['Demo'])


@router.get('/scenarios')
def scenarios(request:Request): return request.app.state.demo_service.scenarios()


@router.post('/session')
def load(body:LoadDemoRequest,request:Request): return request.app.state.demo_service.load(body.scenario_id)


@router.post('/event')
def inject(body:EventDemoRequest,request:Request):
    return request.app.state.demo_service.inject(body.session_id,body.expected_version,body.all_remaining)


@router.get('/state/{session_id}')
def state(session_id:str,request:Request): return request.app.state.demo_service.state(session_id)


@router.post('/reconstruct',status_code=202)
def reconstruct(body:RunDemoRequest,request:Request):
    return request.app.state.demo_service.start(body.session_id,body.expected_version,body.mode,body.auto_commit)


@router.post('/commit')
def commit(body:CommitDemoRequest,request:Request): return request.app.state.demo_service.commit(body.run_id)


@router.get('/run/{run_id}')
def run(run_id:str,request:Request): return request.app.state.demo_service.get_run(run_id)


@router.get('/runs')
def runs(request:Request): return request.app.state.demo_service.list_runs()
