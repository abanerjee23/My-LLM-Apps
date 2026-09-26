"""AUTH_PLAN.md test plan: authentication and cross-user isolation on the real services."""
from __future__ import annotations

import re
import time

import pytest
from sqlalchemy import text

from sourcelens import auth as auth_module
from sourcelens import main
from sourcelens.store import SYSTEM_USER_ID

CSV_V1 = b"region,sentiment,score\nNorth,negative,2\nSouth,positive,5\n"
CSV_V2 = b"region,sentiment,score\nEast,neutral,3\nWest,negative,1\nNorth,positive,4\n"
DEMO_DATASET = {
    "name": "Commerce warehouse copy",
    "kind": "bigquery",
    "project": "gemini-enterprise-learning",
    "dataset": "sourcelens_demo",
    "location": "us-central1",
}


def upload(client, headers, name="feedback.csv", body=CSV_V1):
    response = client.post("/api/sources/files", files={"file": (name, body, "text/csv")}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["source"]


def investigate(client, headers, brief, source_id=None):
    response = client.post(
        "/api/investigations", json={"brief": brief, "source_id": source_id}, headers=headers
    )
    assert response.status_code == 202, response.text
    return client.get(f"/api/investigations/{response.json()['investigation_id']}", headers=headers).json()


def review(client, headers, investigation_id):
    response = client.post(
        f"/api/investigations/{investigation_id}/review",
        json={"decision": "accepted", "accuracy_rating": 4, "usefulness_rating": 4},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def protected_routes():
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in {"/api/health", "/api/auth/session"}:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            yield method, re.sub(r"\{[^}]+\}", "some-id", path)


# 1. Unsigned requests ----------------------------------------------------------------


def test_every_protected_route_rejects_unsigned_requests(client):
    routes = list(protected_routes())
    assert len(routes) >= 15
    for method, path in routes:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path, response.status_code)
        bearer_less = client.request(method, path, headers={"Authorization": "Basic abc"})
        assert bearer_less.status_code == 401, (method, path)
    assert client.get("/api/health").status_code == 200


def test_health_is_public_and_exposes_no_user_data(client):
    body = client.get("/api/health").json()
    assert body["database"] == "ok"
    assert not {"user_id", "email", "investigations", "sources"} & set(body)


def test_id_token_is_exchanged_for_cookie_session_with_csrf_and_allowlist(
    client, settings, make_user, tokens, monkeypatch
):
    alice = make_user("Alice")
    monkeypatch.setattr(settings, "sourcelens_allowed_email", alice.email)
    monkeypatch.setattr(
        settings, "sourcelens_allowed_origins", "https://sourcelens.example.run.app"
    )
    claims = {
        "sub": alice.firebase_uid,
        "email": alice.email,
        "email_verified": True,
        "name": alice.display_name,
        "iss": "https://accounts.google.com",
        "iat": int(time.time()),
    }
    monkeypatch.setattr(auth_module, "verify_google_token", lambda token, config: claims)

    response = client.post(
        "/api/auth/session",
        json={"id_token": "x" * 100},
        headers={"Origin": "https://sourcelens.example.run.app"},
    )
    assert response.status_code == 200, response.text
    foreign_origin = client.post(
        "/api/auth/session", json={"id_token": "x" * 100},
        headers={"Origin": "https://untrusted.example"},
    )
    assert foreign_origin.status_code == 403
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/me").json()["email"] == alice.email

    rejected = client.post("/api/investigations", json={"brief": "Explain the revenue decline"})
    assert rejected.status_code == 403
    csrf = client.cookies.get(settings.sourcelens_csrf_cookie)
    accepted = client.post(
        "/api/investigations",
        json={"brief": "Explain the revenue decline"},
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 202, accepted.text

    signed_out = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert signed_out.status_code == 204
    assert client.get("/api/me").status_code == 401


def test_session_exchange_rejects_an_unapproved_google_account(
    client, settings, make_user, tokens, monkeypatch
):
    alice = make_user("Alice")
    monkeypatch.setattr(settings, "sourcelens_allowed_email", "owner@example.test")
    monkeypatch.setattr(
        auth_module,
        "verify_google_token",
        lambda token, config: {
            "sub": alice.firebase_uid,
            "email": alice.email,
            "email_verified": True,
            "name": alice.display_name,
            "iss": "https://accounts.google.com",
            "iat": int(time.time()),
        },
    )
    response = client.post("/api/auth/session", json={"id_token": "x" * 100})
    assert response.status_code == 403
    assert "does not have access" in response.json()["detail"]


# 2. Owners can create and retrieve their own data --------------------------------------


def test_user_creates_and_retrieves_source_investigation_and_notebook(client, make_user, tokens):
    alice = make_user("Alice")
    headers = tokens.headers(alice)
    assert client.get("/api/me", headers=headers).json()["user_id"] == alice.user_id

    source = upload(client, headers)
    assert source["metadata"]["object_uri"].split("/users/", 1)[1].startswith(
        f"{alice.user_id}/sources/{source['source_id']}/{source['version_id']}/original.csv"
    )
    assert source["source_id"] in {s["source_id"] for s in client.get("/api/sources", headers=headers).json()}
    detail = client.get(f"/api/sources/{source['source_id']}", headers=headers).json()
    assert detail["preview"][0]["region"] == "North"

    result = investigate(client, headers, "What patterns are present in this feedback?", source["source_id"])
    assert result["status"] == "ready"
    assert result["scope"]["source_id"] == source["source_id"]
    entry = review(client, headers, result["investigation_id"])
    assert entry["owner_user_id"] == alice.user_id
    assert [e["entry_id"] for e in client.get("/api/notebook", headers=headers).json()] == [entry["entry_id"]]


# 3. Other users get 404 ------------------------------------------------------------------


def test_other_user_gets_404_for_everything_owned_by_someone_else(client, make_user, tokens):
    alice, bob = make_user("Alice"), make_user("Bob")
    alice_headers, bob_headers = tokens.headers(alice), tokens.headers(bob)
    source = upload(client, alice_headers)
    result = investigate(client, alice_headers, "What patterns are in this file?", source["source_id"])
    review(client, alice_headers, result["investigation_id"])
    source_id, investigation_id = source["source_id"], result["investigation_id"]

    for method, path, kwargs in [
        ("GET", f"/api/sources/{source_id}", {}),
        ("GET", f"/api/sources/{source_id}/versions", {}),
        ("POST", f"/api/sources/{source_id}/versions", {"files": {"file": ("x.csv", CSV_V2, "text/csv")}}),
        ("GET", f"/api/investigations/{investigation_id}", {}),
        ("GET", f"/api/investigations/{investigation_id}/events", {}),
        ("POST", f"/api/investigations/{investigation_id}/refine", {"json": {"direction": "Look at the north"}}),
        ("POST", f"/api/investigations/{investigation_id}/review", {"json": {"decision": "accepted"}}),
    ]:
        response = client.request(method, path, headers=bob_headers, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)

    assert source_id not in {s["source_id"] for s in client.get("/api/sources", headers=bob_headers).json()}
    assert client.get("/api/investigations", headers=bob_headers).json() == []
    assert client.get("/api/notebook", headers=bob_headers).json() == []
    # Alice's data is untouched by Bob's attempts.
    assert client.get(f"/api/sources/{source_id}/versions", headers=alice_headers).json()[0]["manifest"][
        "owner_user_id"
    ] == alice.user_id


def test_shared_starter_source_is_visible_but_read_only(client, make_user, tokens):
    bob = make_user("Bob")
    headers = tokens.headers(bob)
    sources = {s["source_id"] for s in client.get("/api/sources", headers=headers).json()}
    assert main.STARTER_SOURCE_ID in sources
    response = client.post(
        f"/api/sources/{main.STARTER_SOURCE_ID}/versions",
        files={"file": ("x.csv", CSV_V2, "text/csv")},
        headers=headers,
    )
    assert response.status_code == 404


# 4. No investigations against another user's source --------------------------------------


def test_user_cannot_investigate_another_users_source(client, store, make_user, tokens):
    alice, bob = make_user("Alice"), make_user("Bob")
    bob_source = upload(client, tokens.headers(bob))
    response = client.post(
        "/api/investigations",
        json={"brief": "Summarise this source for me", "source_id": bob_source["source_id"]},
        headers=tokens.headers(alice),
    )
    assert response.status_code == 404
    assert store.list_investigations(alice.user_id) == []


# 5. Bad tokens --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    ["expired", "wrong_audience", "wrong_issuer", "malformed", "tampered", "revoked", "anonymous"],
)
def test_invalid_tokens_are_rejected_and_audited(client, store, make_user, tokens, variant):
    alice = make_user("Alice")
    if variant == "expired":
        token = tokens.issue(alice, expires_in=-60)
    elif variant == "wrong_audience":
        token = tokens.issue(alice, audience="another-project")
    elif variant == "wrong_issuer":
        token = tokens.issue(alice, issuer="https://securetoken.google.com/another-project")
    elif variant == "malformed":
        token = "not-a-jwt"
    elif variant == "tampered":
        header, payload, signature = tokens.issue(alice).split(".")
        token = f"{header}.{payload}.{signature[::-1]}"
    elif variant == "revoked":
        token = tokens.issue(alice)
        tokens.revoke(alice)
    else:
        token = tokens.issue(alice, provider="anonymous")

    response = client.get("/api/sources", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    with store.engine.connect() as db:
        rows = db.execute(
            text(
                "SELECT detail::text FROM audit_events WHERE event_type = 'auth_failed' "
                "AND created_at > now() - interval '1 minute' ORDER BY id DESC LIMIT 20"
            )
        ).scalars().all()
    assert rows
    assert not any(token in row for row in rows)  # Raw tokens are never logged.


# 6. BigQuery sources ----------------------------------------------------------------------


def test_bigquery_source_saved_only_after_verification_under_owner(client, make_user, tokens):
    alice, bob = make_user("Alice"), make_user("Bob")
    headers = tokens.headers(alice)

    missing = client.post(
        "/api/sources/connections", json={**DEMO_DATASET, "dataset": "does_not_exist"}, headers=headers
    )
    assert missing.status_code == 422
    wrong_location = client.post(
        "/api/sources/connections", json={**DEMO_DATASET, "location": "EU"}, headers=headers
    )
    assert wrong_location.status_code == 422
    names = {s["name"] for s in client.get("/api/sources", headers=headers).json()}
    assert DEMO_DATASET["name"] not in names

    connected = client.post("/api/sources/connections", json=DEMO_DATASET, headers=headers)
    assert connected.status_code == 201, connected.text
    source = connected.json()
    assert source["status"] == "connected"
    assert "feedback" in source["metadata"]["tables"]
    assert source["source_id"] in {s["source_id"] for s in client.get("/api/sources", headers=headers).json()}
    bob_sources = client.get("/api/sources", headers=tokens.headers(bob)).json()
    assert source["source_id"] not in {s["source_id"] for s in bob_sources}


# 7. Agent retrieval stays inside the owner's source set -----------------------------------


def test_agent_never_resolves_another_users_upload(client, store, investigator, make_user, tokens):
    alice, bob = make_user("Alice"), make_user("Bob")
    alice_source = upload(client, tokens.headers(alice), name="alpha-churn-notes.csv")

    assert alice_source["source_id"] not in {s.source_id for s in store.list_sources(bob.user_id)}
    assert investigator._resolve_uploaded_source("patterns in alpha-churn-notes.csv", bob.user_id) is None

    result = investigate(client, tokens.headers(bob), "What patterns are in alpha-churn-notes.csv?")
    assert result["status"] == "ready"
    assert result["scope"].get("source_id") != alice_source["source_id"]
    assert all(item["source_id"] != alice_source["source_id"] for item in result["evidence"])


def test_agent_does_not_fall_back_to_an_unnamed_upload(client, make_user, tokens):
    alice = make_user("Alice")
    headers = tokens.headers(alice)
    source = upload(client, headers, name="regional-sentiment.csv")
    result = investigate(client, headers, "Why did revenue decline for Nova X300 last quarter?")
    assert result["scope"].get("source_id") != source["source_id"]
    assert result["scope"]["product_id"] == "NOVA-X300"


# 8. Notebook snapshots keep their user and source version ---------------------------------


def test_notebook_snapshot_preserves_user_and_source_version(client, store, make_user, tokens):
    alice = make_user("Alice")
    headers = tokens.headers(alice)
    source = upload(client, headers, body=CSV_V1)
    first = investigate(client, headers, "What patterns are in this feedback?", source["source_id"])
    entry = review(client, headers, first["investigation_id"])

    second_upload = client.post(
        f"/api/sources/{source['source_id']}/versions",
        files={"file": ("feedback.csv", CSV_V2, "text/csv")},
        headers=headers,
    )
    assert second_upload.status_code == 201, second_upload.text
    v1, v2 = source["version_id"], second_upload.json()["source"]["version_id"]
    assert v1 != v2
    versions = client.get(f"/api/sources/{source['source_id']}/versions", headers=headers).json()
    assert [v["version_id"] for v in versions] == [v2, v1]
    assert all(f"/users/{alice.user_id}/sources/" in v["object_uri"] for v in versions)

    saved = client.get("/api/notebook", headers=headers).json()[0]
    assert saved["entry_id"] == entry["entry_id"]
    assert saved["owner_user_id"] == alice.user_id
    assert saved["brief_snapshot"]["scope"]["source_version_id"] == v1
    assert store.source_preview(source["source_id"], alice.user_id, version_id=v1)[0]["region"] == "North"

    second = investigate(client, headers, "What patterns are in this feedback?", source["source_id"])
    assert second["scope"]["source_version_id"] == v2
    assert client.get(f"/api/sources/{source['source_id']}", headers=headers).json()["preview"][0]["region"] == "East"


# Transition flag, rate limits and removed endpoints ----------------------------------------


def test_transition_flag_serves_demo_owner_without_token(client, settings, make_user, tokens, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    assert client.get("/api/me").json()["user_id"] == SYSTEM_USER_ID
    alice = make_user("Alice")
    assert client.get("/api/me", headers=tokens.headers(alice)).json()["user_id"] == alice.user_id
    bad = client.get("/api/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401


def test_expensive_routes_are_rate_limited_per_user(client, settings, make_user, tokens, monkeypatch):
    monkeypatch.setattr(settings, "sourcelens_uploads_per_hour", 2)
    alice, bob = make_user("Alice"), make_user("Bob")
    files = {"file": ("x.csv", CSV_V1, "text/csv")}
    for _ in range(2):
        assert client.post("/api/sources/files/preview", files=files, headers=tokens.headers(alice)).status_code == 200
    assert client.post("/api/sources/files/preview", files=files, headers=tokens.headers(alice)).status_code == 429
    assert client.post("/api/sources/files/preview", files=files, headers=tokens.headers(bob)).status_code == 200


def test_sample_reset_endpoint_is_removed(client, make_user, tokens):
    headers = tokens.headers(make_user())
    assert client.post("/api/setup/sample", headers=headers).status_code in {404, 405}
