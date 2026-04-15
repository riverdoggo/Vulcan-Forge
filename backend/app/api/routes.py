import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app.auth import require_api_key
from app.config.settings import GROQ_MODEL, SANDBOX_IMAGE
from app.database import load_all_tasks, load_task_transcript
from app.logging.log_writer import LOGS_DIR
from app.limiter import limiter
from app.models.task import Task
from app.orchestrator.orchestrator import Orchestrator
from app.sanitize import sanitize_goal, sanitize_repo_url

logger = logging.getLogger(__name__)
router = APIRouter()
orc = Orchestrator()
_TERMINAL_TASK_STATUSES = {"completed", "rejected", "error", "max_steps_reached", "killed"}

STARTUP_TIME = time.time()


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
    base_commit: str = ""


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": round(time.time() - STARTUP_TIME),
        "model": GROQ_MODEL,
        "sandbox_image": SANDBOX_IMAGE,
    }


@router.post("/tasks", dependencies=[Depends(require_api_key)])
@limiter.limit("10/minute")
def create_task(
    request: Request,
    req: CreateTaskRequest,
    background_tasks: BackgroundTasks,
) -> Task:
    try:
        goal = sanitize_goal(req.goal)
        repo_sanitized = sanitize_repo_url(req.repo_url if (req.repo_url or "").strip() else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    repo_url = (repo_sanitized or "").strip()
    base_commit = (req.base_commit or "").strip()
    llm_override: dict[str, str] = {}
    llm_key = (request.headers.get("X-LLM-Key") or "").strip()
    llm_model = (request.headers.get("X-LLM-Model") or "").strip()
    llm_base_url = (request.headers.get("X-LLM-Base-URL") or "").strip()
    if llm_key:
        llm_override["api_key"] = llm_key
    if llm_model:
        llm_override["model"] = llm_model
    if llm_base_url:
        llm_override["base_url"] = llm_base_url
    logger.info("POST /tasks goal=%s", goal[:80])
    task = Task(
        goal=goal,
        repo_url=repo_url,
        base_commit=base_commit,
        repo_type=detect_repo_type(repo_url),
    )
    if llm_override:
        object.__setattr__(task, "llm_override", llm_override)
    created = orc.create_task(task)
    if created.status == "running":
        background_tasks.add_task(orc.run_agent_async, created)
    return created


@router.get("/tasks", dependencies=[Depends(require_api_key)])
@limiter.limit("120/minute")
def list_tasks(request: Request) -> list[Task]:
    return orc.list_tasks()


@router.get("/tasks/history", dependencies=[Depends(require_api_key)])
def get_task_history():
    return load_all_tasks()


@router.get("/tasks/{task_id}/logs", dependencies=[Depends(require_api_key)])
def get_logs(task_id: str):
    try:
        return orc.get_logs(task_id)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "task_id": task_id},
        )


@router.get("/tasks/{task_id}", dependencies=[Depends(require_api_key)])
def get_task(task_id: str):
    tasks = {t.id: t for t in orc.tasks.values()}
    if task_id not in tasks:
        return JSONResponse(status_code=404, content={"detail": "task not found"})
    return tasks[task_id]


@router.get("/logs/last_run_azure", dependencies=[Depends(require_api_key)])
def get_last_run_azure_log():
    log_path = Path(LOGS_DIR) / "last_run_azure.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="last_run_azure.log not found")
    try:
        content = log_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to read log: {e}") from e
    return PlainTextResponse(content)


@router.get("/tasks/{task_id}/transcript", dependencies=[Depends(require_api_key)])
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


@router.get("/tasks/{task_id}/stream", dependencies=[Depends(require_api_key)])
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


@router.get("/tasks/{task_id}/diff", dependencies=[Depends(require_api_key)])
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


@router.post("/tasks/{task_id}/approve", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def approve_task(request: Request, task_id: str):
    try:
        orc.approve_task(task_id)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class RejectRequest(BaseModel):
    reason: str = ""


@router.post("/tasks/{task_id}/reject", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def reject_task(request: Request, task_id: str, req: RejectRequest):
    try:
        orc.reject_task(task_id, req.reason)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/tasks/{task_id}/kill", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def kill_task(request: Request, task_id: str):
    """
    Stop a single task's agent and remove its sandbox container/workspace.
    Safe to call multiple times and when the container is already gone.
    """
    try:
        return await orc.kill_task_async(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found")
