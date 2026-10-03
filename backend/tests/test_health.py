from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from pawspot.db import get_engine
from pawspot.main import app


def test_health_does_not_require_database() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_postgis_version() -> None:
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = "3.6.4"
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        with TestClient(app) as client:
            response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "postgis_version": "3.6.4"}
    assert connection.execute.call_args.args[0].text == "SELECT PostGIS_Version()"


def test_readiness_returns_503_when_database_unavailable() -> None:
    engine = MagicMock(spec=Engine)
    engine.connect.side_effect = OperationalError("connect", {}, Exception("offline"))
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        with TestClient(app) as client:
            response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}
