"""Shared fixtures. Tests run against the real services configured in the environment.

Run with `make test`: it supplies the Cloud SQL password from Secret Manager and points the
warehouse at BigQuery and uploads at the Cloud Storage bucket. Every test user is created
fresh and all of their rows and stored objects are deleted afterwards.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sourcelens import main
from sourcelens.auth import CurrentUser
from sourcelens.config import Settings
from sourcelens.db import create_database_engine
from sourcelens.investigator import Investigator
from sourcelens.ratelimit import RateLimiter
from sourcelens.sources import delete_user_objects
from sourcelens.store import AppStore

LIVE_MODEL = os.environ.get("SOURCELENS_TEST_LIVE_MODEL") == "1"


def pytest_collection_modifyitems(config, items):
    if LIVE_MODEL:
        return
    skip = pytest.mark.skip(reason="real model calls cost money; run `make test-live`")
    for item in items:
        if "live_model" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    if not os.environ.get("SOURCELENS_DB_PASSWORD") and not os.environ.get("SOURCELENS_DATABASE_URL"):
        pytest.exit("Run the tests with `make test` so they can reach Cloud SQL.", returncode=2)
    return Settings(
        sourcelens_data_dir=tmp_path_factory.mktemp("data"),
        sourcelens_live_agent=False,
        auth_required=True,
    )


@pytest.fixture(scope="session")
def store(settings) -> AppStore:
    from sqlalchemy import text

    engine = create_database_engine(settings)
    try:
        with engine.connect() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.exit(f"Cannot reach Cloud SQL ({exc}). Is it stopped? Run `make db-start`.", returncode=2)
    store = AppStore(engine)
    main.ensure_starter_source(store, settings)
    yield store
    engine.dispose()


@pytest.fixture(scope="session")
def investigator(settings, store) -> Investigator:
    warehouse, evidence_index = main.build_warehouse_and_index(settings)
    return Investigator(
        settings=settings, store=store, warehouse=warehouse, evidence_index=evidence_index
    )


@pytest.fixture
def services(store, investigator):
    return store, investigator


@pytest.fixture
def make_user(settings, store):
    created: list[str] = []

    def factory(name: str = "Test user") -> CurrentUser:
        uid = f"test-{uuid4()}"
        email = f"{uid}@example.test"
        user_id = store.upsert_user(uid, email, name)
        created.append(user_id)
        return CurrentUser(firebase_uid=uid, email=email, display_name=name, user_id=user_id)

    yield factory
    for user_id in created:
        store.delete_user_data(user_id)
        delete_user_objects(
            user_id, bucket_name=settings.sourcelens_gcs_bucket, local_root=settings.sourcelens_data_dir
        )


# Firebase ID tokens -------------------------------------------------------------------


@pytest.fixture(scope="session")
def signing_key():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sourcelens-test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return SimpleNamespace(
        kid="sourcelens-test-key",
        private_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


@pytest.fixture
def tokens(settings, signing_key, monkeypatch):
    """Signed Firebase ID tokens verified by the real Admin SDK, without calling Google.

    Only the public-certificate download and the user lookup behind the revocation check
    are replaced; signature, issuer, audience and expiry checks run unchanged.
    """
    import firebase_admin._auth_client as auth_client
    import google.oauth2.id_token as id_token
    from google.auth import crypt, jwt

    monkeypatch.setattr(id_token, "_fetch_certs", lambda request, url: {signing_key.kid: signing_key.cert_pem})
    revoked_after: dict[str, int] = {}
    monkeypatch.setattr(
        auth_client.Client,
        "get_user",
        lambda self, uid: SimpleNamespace(
            disabled=False, tokens_valid_after_timestamp=revoked_after.get(uid, 0)
        ),
    )
    signer = crypt.RSASigner.from_string(signing_key.private_pem, key_id=signing_key.kid)
    project = settings.firebase_project

    def issue(
        user: CurrentUser,
        *,
        expires_in: int = 3600,
        audience: str | None = None,
        issuer: str | None = None,
        provider: str = "google.com",
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer or f"https://securetoken.google.com/{project}",
            "aud": audience or project,
            "auth_time": now - 10,
            "user_id": user.firebase_uid,
            "sub": user.firebase_uid,
            "iat": now - 10,
            "exp": now + expires_in,
            "email": user.email,
            "email_verified": True,
            "name": user.display_name,
            "firebase": {"identities": {}, "sign_in_provider": provider},
        }
        return jwt.encode(signer, claims).decode()

    def headers(user: CurrentUser, **kwargs) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue(user, **kwargs)}"}

    def revoke(user: CurrentUser) -> None:
        revoked_after[user.firebase_uid] = int(time.time() * 1000) + 60_000

    return SimpleNamespace(issue=issue, headers=headers, revoke=revoke)


@pytest.fixture
def client(settings, store, investigator):
    main.app.state.settings = settings
    main.app.state.store = store
    main.app.state.investigator = investigator
    main.app.state.rate_limiter = RateLimiter()
    with TestClient(main.app) as test_client:
        yield test_client
    for name in ("settings", "store", "investigator", "rate_limiter"):
        setattr(main.app.state, name, None)
