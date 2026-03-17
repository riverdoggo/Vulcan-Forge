from fastapi import FastAPI

from app.api.routes import router
from app.config.logging_config import setup_logging
from app.config.settings import LOG_LEVEL, validate_config

validate_config()
setup_logging(level=LOG_LEVEL)

app = FastAPI(title="AI Coding Orchestrator")
app.include_router(router)


@app.get("/")
def root():
    return {"status": "running"}