import logging

from fastapi import APIRouter, BackgroundTasks
from app.models.task import Task
from app.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()
orc = Orchestrator()


@router.post("/tasks")
def create_task(task: Task, background_tasks: BackgroundTasks) -> Task:
    logger.info("POST /tasks goal=%s", (task.goal or "")[:80])
    created = orc.create_task(task)
    if created.status == "running":
        background_tasks.add_task(orc.run_agent, created)
    return created


@router.get("/tasks")
def list_tasks() -> list[Task]:
    return orc.list_tasks()