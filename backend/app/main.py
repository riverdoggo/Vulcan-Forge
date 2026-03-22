from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.logging_config import setup_logging
from app.config.settings import LOG_LEVEL, validate_config

validate_config()
setup_logging(level=LOG_LEVEL)

app = FastAPI(title="AI Coding Orchestrator")

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