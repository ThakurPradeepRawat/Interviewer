from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_models
from app.routers import interview, resume


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()  # dev convenience; use Alembic migrations in production
    yield


app = FastAPI(
    title="Agentic AI Interview Assistant",
    description="Automates technical interview evaluation: resume parsing, "
    "multi-turn Q&A with an agentic planner/evaluator/critic loop, code "
    "execution, and structured scoring via OpenAI function calling.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(resume.router)
app.include_router(interview.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
