from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .auth import (
    CurrentUser,
    SessionRequest,
    clear_session_cookies,
    establish_session,
    require_user,
    revoke_current_session,
    valid_csrf,
)
from .config import Settings, get_settings
from .db import create_database_engine
from .investigator import Investigator, new_investigation
from .models import (
    DataSource,
    InvestigationRequest,
    RefinementRequest,
    ReviewRequest,
    SourceConnectionRequest,
)
from .observability import configure_galileo
from .query import BigQueryWarehouse, SQLiteWarehouse
from .ratelimit import RateLimiter
from .reference_data import generate
from .retrieval import LocalEvidenceIndex, QdrantEvidenceIndex
from .sources import (
    SourceIngestionError,
    connected_source,
    ingest_upload,
    store_source_version,
    verify_bigquery,
)
from .store import SYSTEM_USER_ID, AppStore

logger = logging.getLogger(__name__)
settings = get_settings()

STARTER_SOURCE_ID = "warehouse-primary"


def ensure_starter_source(store: AppStore, settings: Settings) -> None:
    """The commerce warehouse is a read-only shared starter source owned by the system user.

    Refreshed on startup so its details match the configured warehouse.
    """
    store.save_source(
        DataSource(
            source_id=STARTER_SOURCE_ID,
            name="Commerce warehouse",
            kind=settings.sourcelens_warehouse,
            record_count=126_219,
            metadata={
                "project": settings.google_cloud_project,
                "dataset": settings.sourcelens_bigquery_dataset,
                "tables": [
                    "products",
                    "sales_monthly",
                    "sales_lines",
                    "inventory_monthly",
                    "returns_monthly",
                    "feedback",
                ],
                "shared": True,
            },
        ),
        SYSTEM_USER_ID,
    )


def build_warehouse_and_index(settings: Settings):
    # The local reference copy backs the SQLite warehouse and the retrieval fallback.
    if not settings.database_path.exists():
        generate(settings.database_path, reset=True)
    if settings.sourcelens_warehouse == "bigquery":
        warehouse = BigQueryWarehouse(
            settings.google_cloud_project,
            settings.sourcelens_bigquery_dataset,
            settings.google_cloud_location,
        )
    else:
        warehouse = SQLiteWarehouse(settings.database_path)
    local_index = LocalEvidenceIndex(settings.database_path)
    if settings.qdrant_url and settings.qdrant_api_key:
        evidence_index = QdrantEvidenceIndex(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection=settings.qdrant_collection,
            fallback=local_index,
        )
    else:
        evidence_index = local_index
    return warehouse, evidence_index


def build_services(settings: Settings) -> tuple[AppStore, Investigator]:
    store = AppStore(create_database_engine(settings))
    ensure_starter_source(store, settings)
    warehouse, evidence_index = build_warehouse_and_index(settings)
    return store, Investigator(
        settings=settings, store=store, warehouse=warehouse, evidence_index=evidence_index
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests install their own services on app.state before startup.
    if getattr(app.state, "store", None) is None:
        app.state.settings = settings
        app.state.store, app.state.investigator = build_services(settings)
    if getattr(app.state, "rate_limiter", None) is None:
        app.state.rate_limiter = RateLimiter()
    app.state.galileo_enabled = configure_galileo(app.state.settings)
    yield


app = FastAPI(title="SourceLens", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """Protect cookie-authenticated mutations while preserving token-based API access."""
    origin = request.headers.get("origin")
    settings = request.app.state.settings
    allowed_origins = settings.allowed_origins | {str(request.base_url).rstrip("/")}
    if not settings.sourcelens_secure_cookies and request.url.hostname in {"localhost", "127.0.0.1"}:
        allowed_origins.add("http://localhost:5173")
    if request.url.path == "/api/auth/session" and origin and origin not in allowed_origins:
        return JSONResponse({"detail": "Sign-in must originate from SourceLens."}, 403)
    if (
        request.url.path.startswith("/api/")
        and request.url.path != "/api/auth/session"
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.cookies.get(request.app.state.settings.sourcelens_session_cookie)
        and not valid_csrf(request)
    ):
        return JSONResponse({"detail": "Security token missing or invalid. Refresh and try again."}, 403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://accounts.google.com/gsi/client; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
        "connect-src 'self' https://accounts.google.com/gsi/; frame-src "
        "https://accounts.google.com/gsi/; base-uri 'self'; form-action 'self' "
        "https://accounts.google.com; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

User = Annotated[CurrentUser, Depends(require_user)]


def services(request: Request) -> tuple[Settings, AppStore, Investigator]:
    state = request.app.state
    return state.settings, state.store, state.investigator


def limit(request: Request, user: CurrentUser, action: str, per_hour: int) -> None:
    if not request.app.state.rate_limiter.allow(user.user_id, action, per_hour):
        request.app.state.store.audit("rate_limited", owner_user_id=user.user_id, action=action)
        raise HTTPException(429, "Too many requests. Try again later.")


@app.get("/api/health")
def health(request: Request):
    """Public. Reports service status only; never user data."""
    settings, store, _ = services(request)
    body = {
        "status": "ok",
        "warehouse": settings.sourcelens_warehouse,
        "live_agent": settings.sourcelens_live_agent and bool(settings.openai_api_key),
        "qdrant": bool(settings.qdrant_url and settings.qdrant_api_key),
        "galileo": bool(getattr(request.app.state, "galileo_enabled", False)),
        "auth_required": settings.auth_required,
    }
    try:
        body.update(
            runs_used=store.count_live_runs(),
            runs_limit=settings.sourcelens_max_live_runs,
            model_cost_usd=round(store.usage_cost(), 4),
            model_budget_usd=settings.sourcelens_model_budget_usd,
            database="ok",
        )
    except Exception:
        logger.exception("Database health check failed")
        body.update(status="degraded", database="unavailable")
    return body


@app.post("/api/auth/session")
def create_auth_session(request: Request, response: Response, body: SessionRequest):
    """Exchange a Google ID token for a revocable, HttpOnly application session."""
    from google.auth.exceptions import GoogleAuthError

    try:
        user = establish_session(request, response, body.id_token)
    except ValueError as exc:
        request.app.state.store.audit("auth_failed", reason=type(exc).__name__, path=request.url.path)
        raise HTTPException(401, "Google sign-in could not be verified. Try again.") from exc
    except (GoogleAuthError, RuntimeError) as exc:
        raise HTTPException(503, "Sign-in could not be verified right now. Try again.") from exc
    response.headers["Cache-Control"] = "no-store"
    return {"user_id": user.user_id, "email": user.email, "display_name": user.display_name}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, user: User):
    settings, store, _ = services(request)
    revoke_current_session(request)
    clear_session_cookies(response, settings)
    store.audit("auth_session_revoked", owner_user_id=user.user_id)
    response.status_code = 204
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/me")
def me(user: User):
    return {"user_id": user.user_id, "email": user.email, "display_name": user.display_name}


@app.get("/api/investigations")
def list_investigations(request: Request, user: User):
    _, store, _ = services(request)
    return store.list_investigations(user.user_id)


@app.post("/api/investigations", status_code=202)
async def create_investigation(
    request: Request,
    body: InvestigationRequest,
    background_tasks: BackgroundTasks,
    user: User,
):
    settings, store, investigator = services(request)
    limit(request, user, "investigation", settings.sourcelens_investigations_per_hour)
    if settings.sourcelens_live_agent and store.count_live_runs() >= settings.sourcelens_max_live_runs:
        raise HTTPException(429, "Portfolio live-investigation limit reached")
    if body.source_id:
        source = store.get_source(body.source_id, user.user_id)
        if not source:
            store.audit("source_access_denied", owner_user_id=user.user_id, source_id=body.source_id)
            raise HTTPException(404, "Source not found")
        if source.status != "connected":
            raise HTTPException(422, "Select an available connected source")
    investigation = new_investigation(body.brief)
    if body.source_id:
        investigation.scope["source_id"] = body.source_id
    # Ownership is recorded before any agent runs.
    store.create_investigation(investigation, user.user_id)
    background_tasks.add_task(investigator.run, investigation.investigation_id, user.user_id)
    return investigation


@app.post("/api/investigations/{investigation_id}/refine", status_code=202)
async def refine_investigation(
    request: Request,
    investigation_id: str,
    body: RefinementRequest,
    background_tasks: BackgroundTasks,
    user: User,
):
    settings, store, investigator = services(request)
    investigation = store.get_investigation(investigation_id, user.user_id)
    if not investigation:
        raise HTTPException(404, "Investigation not found")
    if investigation.status == "running":
        raise HTTPException(409, "Investigation is already running")
    limit(request, user, "investigation", settings.sourcelens_investigations_per_hour)
    if settings.sourcelens_live_agent and store.count_live_runs() >= settings.sourcelens_max_live_runs:
        raise HTTPException(429, "Portfolio live-investigation limit reached")
    investigation = investigator.prepare_refinement(investigation, body.direction)
    background_tasks.add_task(investigator.run, investigation.investigation_id, user.user_id)
    return investigation


@app.get("/api/investigations/{investigation_id}")
def get_investigation(request: Request, investigation_id: str, user: User):
    _, store, _ = services(request)
    investigation = store.get_investigation(investigation_id, user.user_id)
    if not investigation:
        raise HTTPException(404, "Investigation not found")
    return investigation


@app.get("/api/investigations/{investigation_id}/events")
async def stream_events(request: Request, investigation_id: str, user: User):
    _, store, _ = services(request)
    if not store.get_investigation(investigation_id, user.user_id):
        raise HTTPException(404, "Investigation not found")

    async def generate_events():
        seen = 0
        while True:
            investigation = store.get_investigation(investigation_id, user.user_id)
            if not investigation:
                yield 'event: error\ndata: {"detail":"not found"}\n\n'
                return
            for event in investigation.events[seen:]:
                yield f"event: investigation\ndata: {event.model_dump_json()}\n\n"
                seen += 1
            if investigation.status != "running":
                yield f"event: complete\ndata: {json.dumps({'status': investigation.status})}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(generate_events(), media_type="text/event-stream")


@app.post("/api/investigations/{investigation_id}/review")
def review_investigation(request: Request, investigation_id: str, review: ReviewRequest, user: User):
    _, store, _ = services(request)
    investigation = store.get_investigation(investigation_id, user.user_id)
    if not investigation:
        raise HTTPException(404, "Investigation not found")
    if investigation.status != "ready":
        raise HTTPException(409, "Investigation is not ready for review")
    return store.save_review(investigation, review, user.user_id)


@app.get("/api/notebook")
def notebook(request: Request, user: User):
    _, store, _ = services(request)
    return store.list_notebook(user.user_id)


@app.get("/api/sources")
def list_sources(request: Request, user: User):
    _, store, _ = services(request)
    return store.list_sources(user.user_id)


@app.get("/api/sources/requirements")
def source_requirements(request: Request, user: User):
    settings, _, _ = services(request)
    return {"service_account": f"sourcelens-runtime@{settings.google_cloud_project}.iam.gserviceaccount.com"}


def checked_bigquery(request: SourceConnectionRequest, settings: Settings):
    if request.kind != "bigquery":
        raise HTTPException(422, "BigQuery is the supported database connector")
    try:
        return verify_bigquery(request, settings.google_cloud_project)
    except SourceIngestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        from google.api_core.exceptions import Forbidden, NotFound

        if isinstance(exc, Forbidden):
            detail = "Access denied. Grant the displayed service account BigQuery Data Viewer on the dataset."
        elif isinstance(exc, NotFound):
            detail = "Dataset not found. Check the project ID and dataset ID."
        else:
            detail = "BigQuery could not be reached. Check the dataset location and try again."
        raise HTTPException(422, detail) from exc


@app.post("/api/sources/connections/verify")
def verify_source_connection(request: Request, body: SourceConnectionRequest, user: User):
    settings, _, _ = services(request)
    result = checked_bigquery(body, settings)
    return {
        "tables": result["tables"],
        "service_account": f"sourcelens-runtime@{settings.google_cloud_project}.iam.gserviceaccount.com",
    }


@app.post("/api/sources/files/preview")
async def preview_source_file(request: Request, file: Annotated[UploadFile, File()], user: User):
    settings, _, _ = services(request)
    limit(request, user, "upload", settings.sourcelens_uploads_per_hour)
    try:
        with tempfile.TemporaryDirectory() as directory:
            source, records = await ingest_upload(file, Path(directory))
            source.metadata.pop("raw_path", None)
            return {"source": source, "preview": records[:20]}
    except SourceIngestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "The file could not be read. Check the format and try again.") from exc


@app.post("/api/sources/connections", status_code=201)
def create_source_connection(request: Request, body: SourceConnectionRequest, user: User):
    settings, store, _ = services(request)
    limit(request, user, "upload", settings.sourcelens_uploads_per_hour)
    # Saved only after verification succeeds, under the authenticated owner.
    result = checked_bigquery(body, settings)
    source = connected_source(body)
    source.status = "connected"
    source.record_count = result["record_count"]
    source.metadata.update({key: value for key, value in result.items() if key != "records"})
    store.save_source(source, user.user_id, result["records"])
    return source


async def _ingest_version(
    request: Request, file: UploadFile, user: CurrentUser, source_id: str | None = None
):
    settings, store, _ = services(request)
    with tempfile.TemporaryDirectory() as directory:
        try:
            source, records = await ingest_upload(file, Path(directory), source_id=source_id)
        except SourceIngestionError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            version = store_source_version(
                source,
                records,
                user.user_id,
                bucket_name=settings.sourcelens_gcs_bucket,
                local_root=settings.sourcelens_data_dir,
            )
        except Exception as exc:
            logger.exception("Could not store source version")
            raise HTTPException(502, "The source could not be archived") from exc
    return source, records, version


@app.post("/api/sources/files", status_code=201)
async def upload_source_file(request: Request, file: Annotated[UploadFile, File()], user: User):
    settings, store, _ = services(request)
    limit(request, user, "upload", settings.sourcelens_uploads_per_hour)
    source, records, version = await _ingest_version(request, file, user)
    store.save_source(source, user.user_id, records, version)
    return {"source": source, "preview": records[:20]}


@app.post("/api/sources/{source_id}/versions", status_code=201)
async def upload_source_version(
    request: Request, source_id: str, file: Annotated[UploadFile, File()], user: User
):
    settings, store, _ = services(request)
    limit(request, user, "upload", settings.sourcelens_uploads_per_hour)
    existing = store.get_source(source_id, user.user_id, include_shared=False)
    if not existing or not existing.kind.startswith("file"):
        store.audit("source_access_denied", owner_user_id=user.user_id, source_id=source_id)
        raise HTTPException(404, "Source not found")
    source, records, version = await _ingest_version(request, file, user, source_id=source_id)
    source.created_at = existing.created_at
    store.save_source(source, user.user_id, records, version)
    return {"source": source, "preview": records[:20]}


@app.get("/api/sources/{source_id}")
def get_source(request: Request, source_id: str, user: User):
    _, store, _ = services(request)
    source = store.get_source(source_id, user.user_id)
    if not source:
        raise HTTPException(404, "Source not found")
    return {"source": source, "preview": store.source_preview(source_id, user.user_id)}


@app.get("/api/sources/{source_id}/versions")
def list_source_versions(request: Request, source_id: str, user: User):
    _, store, _ = services(request)
    if not store.get_source(source_id, user.user_id):
        raise HTTPException(404, "Source not found")
    return store.list_source_versions(source_id, user.user_id)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        requested = (frontend_dist / path).resolve()
        if path and requested.is_relative_to(frontend_dist) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")


def run() -> None:
    import uvicorn

    uvicorn.run("sourcelens.main:app", host="0.0.0.0", port=8000)
