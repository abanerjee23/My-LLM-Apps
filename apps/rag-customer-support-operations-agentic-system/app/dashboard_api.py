"""Authenticated reviewer API and the Support Operations dashboard."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import support_actions

STATIC_DIR = Path(__file__).resolve().parent / "static"
router = APIRouter()


class Assignment(BaseModel):
    assignee: str = Field(min_length=2, max_length=200)
    note: str = Field(default="", max_length=1000)
    expected_version: int | None = None


class Decision(BaseModel):
    decision: str
    note: str = Field(min_length=2, max_length=2000)
    expected_version: int | None = None


def _email_from_iap(request: Request) -> str:
    value = request.headers.get("x-goog-authenticated-user-email", "")
    return value.split(":", 1)[-1].strip().lower() if value else ""


def reviewer(request: Request) -> str:
    email = _email_from_iap(request)
    is_local = not (os.getenv("K_SERVICE") or os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID"))
    if (
        not email
        and os.getenv("K_SERVICE")
        and os.getenv("DASHBOARD_AUTH_MODE") == "cloud_run_iam"
    ):
        # Cloud Run rejects unauthenticated traffic before it reaches this
        # container. This fixed identity is suitable for the single-reviewer
        # portfolio MVP; IAP remains the multi-reviewer production mode.
        email = os.getenv("DASHBOARD_SERVICE_REVIEWER", "").strip().lower()
    if not email and is_local:
        email = request.headers.get("x-reviewer-email", "local.reviewer@tarnfield.test").lower()
    if not email:
        raise HTTPException(status_code=401, detail="Reviewer identity was not provided by IAP.")
    allowed = {
        item.strip().lower()
        for item in os.getenv("DASHBOARD_REVIEWERS", "").split(",")
        if item.strip()
    }
    admins = {
        item.strip().lower()
        for item in os.getenv("DASHBOARD_ADMINS", "").split(",")
        if item.strip()
    }
    if (allowed or admins) and email not in allowed | admins:
        raise HTTPException(status_code=403, detail="You are not an authorised support reviewer.")
    return email


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, support_actions.ActionNotFound):
        return HTTPException(status_code=404, detail="Support request not found.")
    if isinstance(error, support_actions.VersionConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, support_actions.InvalidTransition):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="Support workflow could not be updated.")


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse("/ops/")


@router.get("/ops/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


@router.get("/ops/dashboard.css", include_in_schema=False)
def dashboard_css() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.css", media_type="text/css")


@router.get("/ops/dashboard.js", include_in_schema=False)
def dashboard_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.js", media_type="text/javascript")


@router.get("/ops/api/actions")
def list_actions(
    status: str = Query(default=""),
    kind: str = Query(default=""),
    limit: int = Query(default=500, ge=1, le=500),
    _: str = Depends(reviewer),
) -> dict:
    return {"actions": support_actions.get_store().list(status=status, kind=kind, limit=limit)}


@router.get("/ops/api/actions/{reference}")
def get_action(reference: str, _: str = Depends(reviewer)) -> dict:
    try:
        return support_actions.get_store().get(reference)
    except support_actions.SupportActionError as error:
        raise _translate_error(error) from error


@router.get("/ops/api/metrics")
def metrics(_: str = Depends(reviewer)) -> dict:
    return support_actions.calculate_metrics(support_actions.get_store().list(limit=500))


@router.post("/ops/api/actions/{reference}/assign")
def assign_action(reference: str, body: Assignment, actor: str = Depends(reviewer)) -> dict:
    try:
        return support_actions.get_store().assign(
            reference,
            reviewer=actor,
            assignee=actor if body.assignee == "me" else body.assignee,
            note=body.note,
            expected_version=body.expected_version,
        )
    except support_actions.SupportActionError as error:
        raise _translate_error(error) from error


@router.post("/ops/api/actions/{reference}/decision")
def decide_action(reference: str, body: Decision, actor: str = Depends(reviewer)) -> dict:
    targets = {
        "approve": "approved",
        "reject": "rejected",
        "request_information": "needs_information",
        "complete": "completed",
        "fail": "failed",
        "reopen": "reopened",
    }
    target = targets.get(body.decision)
    if target is None:
        raise HTTPException(status_code=422, detail="Unknown workflow decision.")
    try:
        return support_actions.get_store().transition(
            reference,
            target=target,
            reviewer=actor,
            note=body.note,
            expected_version=body.expected_version,
        )
    except support_actions.SupportActionError as error:
        raise _translate_error(error) from error
