from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from threading import Lock
from typing import Annotated

from fastapi import Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .config import Settings
from .store import SYSTEM_USER_ID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    firebase_uid: str
    email: str | None
    display_name: str | None
    user_id: str


# Used only while AUTH_REQUIRED=false and the request carries no token.
DEMO_USER = CurrentUser(
    firebase_uid="system:demo", email=None, display_name="SourceLens demo", user_id=SYSTEM_USER_ID
)

_app_lock = Lock()
_firebase_apps: dict[str, object] = {}


class SessionRequest(BaseModel):
    id_token: str = Field(min_length=100, max_length=10_000)


def _firebase_app(project_id: str):
    """Admin SDK app using the runtime service account (ADC); no key file."""
    import firebase_admin

    with _app_lock:
        if project_id not in _firebase_apps:
            _firebase_apps[project_id] = firebase_admin.initialize_app(
                options={"projectId": project_id}, name=f"sourcelens-{project_id}"
            )
        return _firebase_apps[project_id]


def verify_firebase_token(token: str, settings: Settings) -> dict:
    """Checks signature, issuer, audience, expiry and revocation status."""
    from firebase_admin import auth

    return auth.verify_id_token(
        token, app=_firebase_app(settings.firebase_project), check_revoked=True
    )


def verify_google_token(token: str, settings: Settings) -> dict:
    """Verify a Google Identity Services ID token against this app's OAuth client."""
    if not settings.google_oauth_client_id:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is not configured")
    from google.auth.transport import requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, requests.Request(), settings.google_oauth_client_id)


def _audit_failure(request: Request, reason: str) -> None:
    try:
        request.app.state.store.audit("auth_failed", reason=reason, path=request.url.path)
    except Exception:  # An audit outage must not change the response.
        logger.exception("Could not record authentication failure")


def _user_from_claims(request: Request, claims: dict) -> CurrentUser:
    settings: Settings = request.app.state.settings
    provider = claims.get("firebase", {}).get("sign_in_provider")
    if provider != "google.com":
        _audit_failure(request, f"provider:{provider or 'missing'}")
        raise HTTPException(401, "Sign in with Google to continue")

    email = claims.get("email")
    allowed = settings.sourcelens_allowed_email
    if allowed and (
        not claims.get("email_verified")
        or not email
        or not compare_digest(email.casefold(), allowed.casefold())
    ):
        _audit_failure(request, "account_not_allowed")
        raise HTTPException(403, "This Google account does not have access to SourceLens.")

    store = request.app.state.store
    user_id = store.upsert_user(claims["uid"], email, claims.get("name"))
    return CurrentUser(
        firebase_uid=claims["uid"],
        email=email,
        display_name=claims.get("name"),
        user_id=user_id,
    )


def _user_from_google_claims(request: Request, claims: dict) -> CurrentUser:
    email = claims.get("email")
    settings: Settings = request.app.state.settings
    allowed = settings.sourcelens_allowed_email
    if (
        claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
        or not claims.get("sub")
        or not claims.get("email_verified")
        or not email
    ):
        _audit_failure(request, "invalid_google_identity")
        raise HTTPException(401, "Google sign-in could not be verified. Try again.")
    if allowed and not compare_digest(email.casefold(), allowed.casefold()):
        _audit_failure(request, "account_not_allowed")
        raise HTTPException(403, "This Google account does not have access to SourceLens.")
    user_id = request.app.state.store.upsert_google_user(claims["sub"], email, claims.get("name"))
    return CurrentUser(
        firebase_uid=f"google:{claims['sub']}",
        email=email,
        display_name=claims.get("name"),
        user_id=user_id,
    )


def establish_session(request: Request, response: Response, id_token: str) -> CurrentUser:
    """Exchange a Google ID token for an opaque, revocable SourceLens session."""
    settings: Settings = request.app.state.settings
    claims = verify_google_token(id_token, settings)
    issued_at = claims.get("iat")
    if not isinstance(issued_at, (int, float)) or time.time() - issued_at > 300:
        _audit_failure(request, "stale_google_credential")
        raise HTTPException(401, "Please sign in with Google again to create a new session.")
    user = _user_from_google_claims(request, claims)
    session_cookie = secrets.token_urlsafe(48)
    max_age = settings.sourcelens_session_days * 24 * 60 * 60
    request.app.state.store.create_auth_session(
        sha256(session_cookie.encode()).hexdigest(),
        user.user_id,
        datetime.now(UTC) + timedelta(seconds=max_age),
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        settings.sourcelens_session_cookie,
        session_cookie,
        max_age=max_age,
        secure=settings.sourcelens_secure_cookies,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.sourcelens_csrf_cookie,
        csrf_token,
        max_age=max_age,
        secure=settings.sourcelens_secure_cookies,
        httponly=False,
        samesite="lax",
        path="/",
    )
    request.app.state.store.audit("auth_session_created", owner_user_id=user.user_id)
    return user


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.sourcelens_session_cookie,
        secure=settings.sourcelens_secure_cookies,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        settings.sourcelens_csrf_cookie,
        secure=settings.sourcelens_secure_cookies,
        httponly=False,
        samesite="lax",
        path="/",
    )


def revoke_current_session(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if cookie := request.cookies.get(settings.sourcelens_session_cookie):
        request.app.state.store.revoke_auth_session(sha256(cookie.encode()).hexdigest())


def valid_csrf(request: Request) -> bool:
    """Bearer requests are CSRF-safe; cookie-authenticated writes use double-submit CSRF."""
    if request.headers.get("authorization", "").startswith("Bearer "):
        return True
    settings: Settings = request.app.state.settings
    cookie = request.cookies.get(settings.sourcelens_csrf_cookie)
    header = request.headers.get("x-csrf-token")
    return bool(cookie and header and compare_digest(cookie, header))


def require_user(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> CurrentUser:
    """FastAPI dependency: the verified caller. Identity comes only from the token."""
    settings: Settings = request.app.state.settings
    from firebase_admin import auth, exceptions

    try:
        if authorization:
            if not authorization.startswith("Bearer "):
                raise HTTPException(401, "Sign in to continue")
            claims = verify_firebase_token(authorization.removeprefix("Bearer "), settings)
        elif cookie := request.cookies.get(settings.sourcelens_session_cookie):
            row = request.app.state.store.auth_session_user(sha256(cookie.encode()).hexdigest())
            if not row:
                raise HTTPException(401, "Your session has expired. Sign in again.")
            return CurrentUser(
                firebase_uid=row["firebase_uid"],
                email=row["email"],
                display_name=row["display_name"],
                user_id=str(row["user_id"]),
            )
        elif not settings.auth_required:
            return DEMO_USER
        else:
            raise HTTPException(401, "Sign in to continue")
    except HTTPException:
        raise
    except auth.CertificateFetchError as exc:
        raise HTTPException(503, "Sign-in could not be verified right now. Try again.") from exc
    except (
        auth.InvalidIdTokenError,
        auth.UserDisabledError,
        auth.UserNotFoundError,
        ValueError,
    ) as exc:
        _audit_failure(request, type(exc).__name__)
        raise HTTPException(401, "Your session has expired. Sign in again.") from exc
    except exceptions.FirebaseError as exc:
        raise HTTPException(503, "Sign-in could not be verified right now. Try again.") from exc

    return _user_from_claims(request, claims)
