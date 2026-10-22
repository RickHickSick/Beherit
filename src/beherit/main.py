"""FastAPI Application Entrypoint for Beherit Server."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from beherit import __version__
from beherit.config import settings
from beherit.core.repository import AgentRepository

app = FastAPI(
    title="Beherit",
    description="Autonomous AI Agent Platform powered by Agent SDKs with per-agent subscription isolation.",
    version=__version__,
)

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BEHERIT_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

repo = AgentRepository()


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": __version__,
        "environment": settings.BEHERIT_ENV,
    }


@app.get("/api/agents")
async def list_agents():
    """List all registered agents."""
    agents = repo.list_agents()
    return {"agents": [a.model_dump(mode="json") for a in agents]}
