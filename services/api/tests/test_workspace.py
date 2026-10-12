def test_create_and_list_profiles(client):
    created = client.post("/api/v1/workspace/profiles", json={"display_name": "  Asha  "})
    assert created.status_code == 201
    assert created.json()["display_name"] == "Asha"

    listing = client.get("/api/v1/workspace/profiles").json()
    assert [p["display_name"] for p in listing["items"]] == ["Asha"]
    assert "not authentication" in listing["notice"]


def test_duplicate_profile_name_conflicts(client):
    assert client.post("/api/v1/workspace/profiles", json={"display_name": "Asha"}).status_code == 201
    response = client.post("/api/v1/workspace/profiles", json={"display_name": "Asha"})
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "PROFILE_EXISTS"


def test_blank_profile_name_rejected(client):
    response = client.post("/api/v1/workspace/profiles", json={"display_name": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
