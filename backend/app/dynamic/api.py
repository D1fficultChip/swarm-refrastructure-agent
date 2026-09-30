from fastapi import APIRouter, Request

from .models import (CreateDynamicSessionRequest, DynamicEventRequest,
                     DynamicReconstructRequest, DynamicSessionRequest,
                     DynamicSpeedRequest, DynamicStepRequest)

router=APIRouter(prefix='/api/v1/dynamic',tags=['Dynamic Mission'])


@router.post('/session')
def create(body:CreateDynamicSessionRequest,request:Request):return request.app.state.dynamic_service.create(body)


@router.post('/start')
def start(body:DynamicSessionRequest,request:Request):return request.app.state.dynamic_service.start(body.session_id)


@router.post('/pause')
def pause(body:DynamicSessionRequest,request:Request):return request.app.state.dynamic_service.pause(body.session_id)


@router.post('/resume')
def resume(body:DynamicSessionRequest,request:Request):return request.app.state.dynamic_service.resume(body.session_id)


@router.post('/step')
def step(body:DynamicStepRequest,request:Request):return request.app.state.dynamic_service.step(body.session_id,body.seconds)


@router.post('/speed')
def speed(body:DynamicSpeedRequest,request:Request):return request.app.state.dynamic_service.set_speed(body.session_id,body.speed)


@router.post('/event')
def event(body:DynamicEventRequest,request:Request):
    return request.app.state.dynamic_service.inject(body.session_id,body.mode,body.event_type,body.difficulty,body.target_id)


@router.post('/reconstruct')
def reconstruct(body:DynamicReconstructRequest,request:Request):
    return request.app.state.dynamic_service.reconstruct(body.session_id,body.mode)


@router.post('/sync')
def sync(body:DynamicSessionRequest,request:Request):return request.app.state.dynamic_service.sync(body.session_id)


@router.post('/reset')
def reset(body:DynamicSessionRequest,request:Request):return request.app.state.dynamic_service.reset(body.session_id)


@router.post('/save')
def save(body:DynamicSessionRequest,request:Request):return request.app.state.dynamic_service.save_scenario(body.session_id)


@router.get('/state/{session_id}')
def state(session_id:str,request:Request):return request.app.state.dynamic_service.state(session_id)


@router.get('/timeline/{session_id}')
def timeline(session_id:str,request:Request):return request.app.state.dynamic_service.timeline(session_id)


@router.get('/runs')
def runs(request:Request):return request.app.state.dynamic_service.list_runs()


@router.get('/run/{run_id}')
def run(run_id:str,request:Request):return request.app.state.dynamic_service.get_run(run_id)
