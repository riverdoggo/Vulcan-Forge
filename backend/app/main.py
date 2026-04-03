from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.logging_config import setup_logging
from app.config.settings import LOG_LEVEL, validate_config
from app.database import init_db, load_all_tasks
from app.models.task import Task

validate_config()
setup_logging(level=LOG_LEVEL)

app = FastAPI(title="AI Coding Orchestrator")


@app.on_event("startup")
async def startup():
    init_db()
    from app.api.routes import orc

    for task_dict in load_all_tasks():
        t = Task.model_validate(task_dict)
        if t.id not in orc.tasks:
            orc.tasks[t.id] = t

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {"status": "running"}