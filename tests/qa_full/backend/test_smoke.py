"""管线 smoke: 验证 conftest + uvicorn fixture 通。"""
import pytest


@pytest.mark.smoke
@pytest.mark.api
def test_health_endpoint(api_base, http):
    r = http.get(f"{api_base}/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "app_name" in data
