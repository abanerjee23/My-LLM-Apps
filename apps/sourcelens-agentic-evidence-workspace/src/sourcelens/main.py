from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .investigator import Investigator, new_investigation
from .models import (
    DataSource,
    InvestigationRequest,
    RefinementRequest,
    ReviewRequest,
    SourceConnectionRequest,
)
from .query import BigQueryWarehouse, SQLiteWarehouse
from .reference_data import generate
from .retrieval import LocalEvidenceIndex, QdrantEvidenceIndex
from .sources import (
    SourceIngestionError,
    archive_source,
    connected_source,
    ingest_upload,
    restore_archived_sources,
    verify_bigquery,
    write_manifest,
)
from .store import AppStore

settings = get_settings()


def build_services():
    if not settings.database_path.exists():
        generate(settings.database_path, reset=True)
    store = AppStore(settings.database_path)
    if settings.sourcelens_gcs_bucket:
        try:
            restore_archived_sources(store, settings.sourcelens_gcs_bucket)
        except Exception:
            pass
    if not store.get_source("warehouse-primary"):
        store.save_source(
            DataSource(
                source_id="warehouse-primary",
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
                },
            )
        )
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
    return store, Investigator(
        settings=settings, store=store, warehouse=warehouse, evidence_index=evidence_index
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global store, investigator
    store, investigator = build_services()
    yield


app = FastAPI(title="SourceLens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
store, investigator = build_services()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "warehouse": settings.sourcelens_warehouse,
        "live_agent": settings.sourcelens_live_agent and bool(settings.openai_api_key),
        "qdrant": bool(settings.qdrant_url and settings.qdrant_api_key),
        "runs_used": store.count_live_runs(),
        "runs_limit": settings.sourcelens_max_live_runs,
        "model_cost_usd": round(store.usage_cost(), 4),
        "model_budget_usd": settings.sourcelens_model_budget_usd,
    }


@app.post("/api/setup/sample")
def setup_sample():
    manifest = generate(settings.database_path, reset=True)
    global store, investigator
    store, investigator = build_services()
    return manifest


@app.get("/api/investigations")
def list_investigations():
    return store.list_investigations()


@app.post("/api/investigations", status_code=202)
async def create_investigation(request: InvestigationRequest, background_tasks: BackgroundTasks):
    if settings.sourcelens_live_agent and store.count_live_runs() >= settings.sourcelens_max_live_runs:
        raise HTTPException(429, "Portfolio live-investigation limit reached")
    if request.source_id:
        source = store.get_source(request.source_id)
        if not source or source.status != "connected":
            raise HTTPException(422, "Select an available connected source")
    investigation = new_investigation(request.brief)
    if request.source_id:
        investigation.scope["source_id"] = request.source_id
    store.save_investigation(investigation)
    background_tasks.add_task(investigator.run, investigation.investigation_id)
    return investigation


@app.post("/api/investigations/{investigation_id}/refine", status_code=202)
async def refine_investigation(
    investigation_id: str, request: RefinementRequest, background_tasks: BackgroundTasks
):
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(404, "Investigation not found")
    if investigation.status == "running":
        raise HTTPException(409, "Investigation is already running")
    if settings.sourcelens_live_agent and store.count_live_runs() >= settings.sourcelens_max_live_runs:
        raise HTTPException(429, "Portfolio live-investigation limit reached")
    investigation = investigator.prepare_refinement(investigation, request.direction)
    background_tasks.add_task(investigator.run, investigation.investigation_id)
    return investigation


@app.get("/api/investigations/{investigation_id}")
def get_investigation(investigation_id: str):
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(404, "Investigation not found")
    return investigation


@app.get("/api/investigations/{investigation_id}/events")
async def stream_events(investigation_id: str):
    async def generate_events():
        seen = 0
        while True:
            investigation = store.get_investigation(investigation_id)
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
def review_investigation(investigation_id: str, review: ReviewRequest):
    investigation = store.get_investigation(investigation_id)
    if not investigation:
        raise HTTPException(404, "Investigation not found")
    if investigation.status != "ready":
        raise HTTPException(409, "Investigation is not ready for review")
    return store.save_review(investigation, review)


@app.get("/api/notebook")
def notebook():
    return store.list_notebook()


@app.get("/api/sources")
def list_sources():
    return store.list_sources()


@app.get("/api/sources/requirements")
def source_requirements():
    return {"service_account": f"sourcelens-runtime@{settings.google_cloud_project}.iam.gserviceaccount.com"}


def checked_bigquery(request: SourceConnectionRequest):
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
def verify_source_connection(request: SourceConnectionRequest):
    result = checked_bigquery(request)
    return {"tables": result["tables"], **source_requirements()}


@app.post("/api/sources/files/preview")
async def preview_source_file(file: Annotated[UploadFile, File()]):
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
def create_source_connection(request: SourceConnectionRequest):
    result = checked_bigquery(request)
    source = connected_source(request)
    source.status = "connected"
    source.record_count = result["record_count"]
    source.metadata.update({key: value for key, value in result.items() if key != "records"})
    store.save_source(source, result["records"])
    return source


@app.post("/api/sources/files", status_code=201)
async def upload_source_file(file: Annotated[UploadFile, File()]):
    try:
        source, records = await ingest_upload(file, settings.sourcelens_data_dir / "sources")
    except SourceIngestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    source_dir = Path(source.metadata["raw_path"]).parent
    write_manifest(source, records, source_dir)
    if settings.sourcelens_gcs_bucket:
        try:
            source = archive_source(source, records, source_dir, settings.sourcelens_gcs_bucket)
        except Exception as exc:
            raise HTTPException(502, "The source could not be archived") from exc
    store.save_source(source, records)
    return {"source": source, "preview": records[:20]}


@app.get("/api/sources/{source_id}")
def get_source(source_id: str):
    source = store.get_source(source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    return {"source": source, "preview": store.source_preview(source_id)}


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        requested = frontend_dist / path
        if path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")


def run() -> None:
    import uvicorn

    uvicorn.run("sourcelens.main:app", host="0.0.0.0", port=8000)
