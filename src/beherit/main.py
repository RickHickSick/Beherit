"""FastAPI Application Entrypoint for Beherit Server."""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from beherit import __version__
from beherit.api.routes import router as agents_router, history_router, scheduler
from beherit.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager to cleanly start and stop background scheduler."""
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="Beherit",
    description="Autonomous AI Agent Platform powered by Agent SDKs with per-agent subscription isolation.",
    version=__version__,
    lifespan=lifespan,
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
app.include_router(history_router)

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root_index():
    """Serve the Web UI / Safari iOS PWA."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "status": "healthy",
        "version": __version__,
        "message": "Beherit Core API Running. Static Web UI not found.",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": __version__,
        "environment": settings.BEHERIT_ENV,
        "agents_dir": str(settings.agents_dir),
    }
