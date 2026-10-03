import pytest
from fastapi.testclient import TestClient

from pawspot.main import app


@pytest.mark.integration
def test_readiness_with_postgis_database() -> None:
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["postgis_version"]
