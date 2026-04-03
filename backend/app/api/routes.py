import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.database import load_all_tasks, load_task_transcript
from app.models.task import Task
from app.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()
orc = Orchestrator()
_TERMINAL_TASK_STATUSES = {"completed", "rejected", "error", "max_steps_reached", "killed"}


def detect_repo_type(repo_url: str) -> str:
    if not repo_url.strip():
        return "default"
    u = repo_url.strip()
    if u.startswith("https://github.com") or u.startswith("git@github.com"):
        return "github"
    return "local"


class CreateTaskRequest(BaseModel):
    goal: str
    repo_url: str = ""


@router.post("/tasks")
def create_task(req: CreateTaskRequest, background_tasks: BackgroundTasks) -> Task:
    goal = (req.goal or "").strip()
    repo_url = (req.repo_url or "").strip()
    logger.info("POST /tasks goal=%s", goal[:80])
    task = Task(goal=goal, repo_url=repo_url, repo_type=detect_repo_type(repo_url))
    created = orc.create_task(task)
    if created.status == "running":
        background_tasks.add_task(orc.run_agent_async, created)
    return created


@router.get("/tasks")
def list_tasks() -> list[Task]:
    return orc.list_tasks()


@router.get("/tasks/history")
def get_task_history():
    return load_all_tasks()


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
    tasks = {t.id: t for t in orc.tasks.values()}
    if task_id not in tasks:
        return JSONResponse(status_code=404, content={"detail": "task not found"})
    return tasks[task_id]


@router.get("/tasks/{task_id}/transcript")
def get_task_transcript(task_id: str):
    try:
        transcript = load_task_transcript(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    return transcript


def _stream_status(task_status: str) -> str:
    if task_status == "completed":
        return "completed"
    if task_status in {"rejected", "error", "max_steps_reached", "killed"}:
        return "failed"
    return task_status


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str, request: Request):
    try:
        load_task_transcript(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        last_index = 0
        sent_terminal = False
        while True:
            if await request.is_disconnected():
                break
            try:
                transcript = load_task_transcript(task_id)
            except KeyError:
                break

            task = orc.tasks.get(task_id)
            task_status = task.status if task else "unknown"
            stream_status = _stream_status(task_status)

            while last_index < len(transcript):
                entry = transcript[last_index]
                payload = {
                    "task_id": task_id,
                    "status": stream_status,
                    "task_status": task_status,
                    "step_data": entry,
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_index += 1

            if task_status in _TERMINAL_TASK_STATUSES and not sent_terminal:
                payload = {
                    "task_id": task_id,
                    "status": stream_status,
                    "task_status": task_status,
                    "step_data": None,
                }
                yield f"data: {json.dumps(payload)}\n\n"
                sent_terminal = True
                break

            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/tasks/{task_id}/diff")
def get_task_diff(task_id: str):
    tasks = {t.id: t for t in orc.tasks.values()}
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    if task.status not in ("awaiting_approval", "completed", "rejected"):
        raise HTTPException(
            status_code=400,
            detail="Diff is only available after a review gate or once the task has finished",
        )
    return {
        "diff": task.diff_output,
        "reviewer_feedback": task.reviewer_feedback,
        "reviewer_status": task.reviewer_status,
        "escalation_reason": task.escalation_reason,
        "review_iterations": task.review_iterations,
    }


@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: str):
    try:
        orc.approve_task(task_id)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RejectRequest(BaseModel):
    reason: str = ""


@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: str, req: RejectRequest):
    try:
        orc.reject_task(task_id, req.reason)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/kill")
async def kill_task(task_id: str):
    """
    Stop a single task's agent and remove its sandbox container/workspace.
    Safe to call multiple times and when the container is already gone.
    """
    try:
        return await orc.kill_task_async(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found")
