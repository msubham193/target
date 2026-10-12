import pyproj
import pytest

from geopoc.geo import crs as registry

URL = "/api/v1/crs"


def test_proj_network_access_is_disabled():
    assert pyproj.network.is_network_enabled() is False


def test_search_by_code_returns_complete_definition(client):
    body = client.get(URL, params={"q": "EPSG:4326"}).json()
    first = body["items"][0]
    assert first["identifier"] == "EPSG:4326"
    assert first["name"] == "WGS 84"
    assert first["kind"] == "geographic"
    assert first["units"] == ["degree"]
    # The authority axis order (latitude first) is reported, not hidden.
    assert [axis["abbreviation"] for axis in first["axis_order"]] == ["Lat", "Lon"]
    assert first["area_of_use"]["west"] == -180
    assert "local" in body["registry"]


def test_search_by_bare_code_and_by_name(client):
    assert client.get(URL, params={"q": "32643"}).json()["items"][0]["identifier"] == "EPSG:32643"
    names = client.get(URL, params={"q": "utm zone 43n wgs 84"}).json()["items"]
    match = next(item for item in names if item["identifier"] == "EPSG:32643")
    assert match["kind"] == "projected"
    assert match["units"] == ["metre"]


def test_search_respects_limit_and_requires_query(client):
    assert len(client.get(URL, params={"q": "utm", "limit": 3}).json()["items"]) == 3
    assert client.get(URL).status_code == 422
    assert client.get(URL, params={"q": "utm", "limit": 500}).status_code == 422


def test_a_search_hit_does_not_make_an_incomplete_name_resolvable():
    for text in ("WGS84 + LCC", "WGS84", "LCC"):
        with pytest.raises(registry.CrsResolutionError):
            registry.resolve(text)
    assert registry.resolve("epsg:4326").to_epsg() == 4326
