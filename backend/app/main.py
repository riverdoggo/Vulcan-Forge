from fastapi import FastAPI
from app.api.routes import router

app = FastAPI(title="AI Coding Orchestrator")

# register api routes
app.include_router(router)

@app.get("/")
def root():
    # simple health check
    return {"status": "running"}