from fastapi import APIRouter, BackgroundTasks
from app.models.task import Task
from app.orchestrator.orchestrator import Orchestrator

router = APIRouter()
orc = Orchestrator()

@router.post("/tasks")
def create_task(task: Task, background_tasks: BackgroundTasks):
    created = orc.create_task(task)
    background_tasks.add_task(orc.run_agent, created)
    return created

@router.get("/tasks")
def list_tasks():
    return orc.list_tasks()