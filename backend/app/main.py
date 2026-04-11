import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routes import router
from app.config.logging_config import setup_logging
from app.config.settings import LOG_LEVEL, validate_config
from app.database import init_db, load_all_tasks
from app.limiter import limiter
from app.models.task import Task

validate_config()
setup_logging(level=os.getenv("LOG_LEVEL", LOG_LEVEL))

app = FastAPI(title="AI Coding Orchestrator")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


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
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-API-Key",
        "X-LLM-Key",
        "X-LLM-Model",
        "X-LLM-Base-URL",
    ],
)

app.include_router(router)


@app.get("/")
def root():
    return {"status": "running"}
