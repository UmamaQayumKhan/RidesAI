"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload
"""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import init_db
from app.deps import NotAuthenticated
from app.logging_config import setup_logging
from app.routers import api, assistant, auth, conversations, dashboard, orders, products, settings, simulator, webhook
from app.templating import render

settings_ = get_settings()
setup_logging(settings_.log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings_.is_production and settings_.secret_key.startswith("dev-only"):
        raise RuntimeError("Set a strong SECRET_KEY before running in production.")
    if not settings_.llm_configured:
        logger.warning("OPENAI_API_KEY is not set. The AI agent will not answer until you add it to .env")
    logger.info("%s started (model=%s)", settings_.app_name, settings_.openai_model)
    yield


app = FastAPI(title=f"{settings_.app_name} API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings_.secret_key,
    session_cookie="agentdesk_session",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=settings_.is_production,
)


@app.middleware("http")
async def log_and_secure(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    if not request.url.path.startswith("/static"):
        logger.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith(("/api", "/webhooks"))


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, _exc: NotAuthenticated):
    if _wants_json(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", {"status": exc.status_code, "detail": exc.detail}, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    if _wants_json(request):
        errors = [{"loc": e.get("loc"), "msg": e.get("msg", "").replace("Value error, ", ""), "type": e.get("type")}
                  for e in exc.errors()]
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)
    return render(request, "error.html", {"status": 422, "detail": "Some form fields are missing or invalid."}, 422)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    if _wants_json(request):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return render(request, "error.html", {"status": 500, "detail": "Something went wrong. Please try again."}, 500)


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

for module in (auth, dashboard, products, orders, conversations, settings, simulator, assistant, api, webhook):
    app.include_router(module.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "llm_configured": settings_.llm_configured}
