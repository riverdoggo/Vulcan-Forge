import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

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


@router.get("/tasks/{task_id}/logs")
def get_logs(task_id: str):
    try:
        return orc.get_logs(task_id)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "task_id": task_id},
        )


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    tasks = {t.id: t for t in orc.list_tasks()}
    if task_id not in tasks:
        return JSONResponse(status_code=404, content={"detail": "task not found"})
    return tasks[task_id]