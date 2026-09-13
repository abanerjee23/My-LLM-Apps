"""Dashboard-only FastAPI entry point for the separate Cloud Run service."""

from fastapi import FastAPI

from app.dashboard_api import router

app = FastAPI(
    title="Tarnfield Support Operations",
    description="Authenticated human-review queue for customer support actions.",
)
app.include_router(router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}
