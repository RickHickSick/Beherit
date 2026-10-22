"""FastAPI Application Entrypoint for Beherit Server."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from beherit import __version__
from beherit.api.routes import router as agents_router
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

app.include_router(agents_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": __version__,
        "environment": settings.BEHERIT_ENV,
        "agents_dir": str(settings.agents_dir),
    }
