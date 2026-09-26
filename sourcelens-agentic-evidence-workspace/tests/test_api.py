def test_complete_api_flow(client, make_user, tokens):
    headers = tokens.headers(make_user())
    response = client.post(
        "/api/investigations",
        json={"brief": "Why did Nova X300 decline in the latest quarter?"},
        headers=headers,
    )
    assert response.status_code == 202
    investigation_id = response.json()["investigation_id"]
    result = client.get(f"/api/investigations/{investigation_id}", headers=headers).json()
    assert result["status"] == "ready"
    refined = client.post(
        f"/api/investigations/{investigation_id}/refine",
        json={"direction": "Test stock availability before concluding"},
        headers=headers,
    )
    assert refined.status_code == 202
    result = client.get(f"/api/investigations/{investigation_id}", headers=headers).json()
    assert result["status"] == "ready"
    assert any(event["event_type"] == "direction" for event in result["events"])
    review = client.post(
        f"/api/investigations/{investigation_id}/review",
        json={"decision": "accepted", "accuracy_rating": 4, "usefulness_rating": 5},
        headers=headers,
    )
    assert review.status_code == 200
    assert client.get("/api/notebook", headers=headers).json()[0]["decision"] == "accepted"
