"""FastAPI Application Entrypoint for Beherit Server."""

import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from beherit import __version__
from beherit.api.routes import (
    router as agents_router,
    history_router,
    reports_router,
    auth_guard_router,
    scheduler,
)
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

# Optional Auth Guard Middleware for BEHERIT_ACCESS_TOKEN protection
@app.middleware("http")
async def auth_guard_middleware(request: Request, call_next):
    token = settings.BEHERIT_ACCESS_TOKEN
    if not token:
        return await call_next(request)

    path = request.url.path
    if (
        path in ("/", "/health", "/api/auth/status", "/api/auth/verify")
        or path.startswith("/static/")
        or not path.startswith("/api/")
        or request.method == "OPTIONS"
    ):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    custom_token = request.headers.get("X-Beherit-Token", "")
    cookie_token = request.cookies.get("beherit_token", "")

    provided_token = None
    if auth_header.startswith("Bearer "):
        provided_token = auth_header[7:].strip()
    elif custom_token:
        provided_token = custom_token.strip()
    elif cookie_token:
        provided_token = cookie_token.strip()

    if not provided_token or not hmac.compare_digest(
        provided_token.encode("utf-8"), token.strip().encode("utf-8")
    ):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized. Invalid or missing BEHERIT_ACCESS_TOKEN."},
        )

    return await call_next(request)


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
app.include_router(reports_router)
app.include_router(auth_guard_router)

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
