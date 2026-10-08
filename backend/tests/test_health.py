from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from pawspot.db import get_engine, get_schema_heads
from pawspot.main import app


@pytest.fixture
def engine() -> Iterator[MagicMock]:
    engine = MagicMock(spec=Engine)
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        yield engine
    finally:
        app.dependency_overrides.pop(get_engine, None)


def test_health_does_not_require_database(engine: MagicMock) -> None:
    engine.connect.side_effect = AssertionError("Liveness must not access the DB")
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    engine.connect.assert_not_called()


def test_readiness_reports_postgis_version(engine: MagicMock) -> None:
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.one.return_value = ("3.6.4", "public")
    connection.dialect.identifier_preparer.quote_schema.return_value = '"public"'
    connection.execute.return_value.scalars.return_value.all.return_value = list(
        get_schema_heads()
    )
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "postgis_version": "3.6.4"}
    assert [call.args[0].text for call in connection.execute.call_args_list] == [
        "SELECT PostGIS_Version(), current_schema()",
        'SELECT version_num FROM "public".alembic_version',
    ]


def test_readiness_returns_503_when_database_unavailable(engine: MagicMock) -> None:
    engine.connect.side_effect = OperationalError("connect", {}, Exception("offline"))
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}


@pytest.mark.parametrize(
    "revisions", [["20261004_02"], [], ["unknown"], ["20261004_03", "unexpected"]]
)
def test_readiness_rejects_non_current_revisions(
    engine: MagicMock, revisions: list[str]
) -> None:
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.one.return_value = ("3.6.4", "public")
    connection.execute.return_value.scalars.return_value.all.return_value = revisions
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}


@pytest.mark.parametrize("failed_query", [1, 2])
def test_readiness_sanitizes_missing_postgis_or_version_table(
    engine: MagicMock, failed_query: int
) -> None:
    connection = engine.connect.return_value.__enter__.return_value
    result = MagicMock()
    result.one.return_value = ("3.6.4", "public")
    failure = ProgrammingError("sensitive SQL", {}, Exception("private DB details"))
    connection.execute.side_effect = (
        [failure] if failed_query == 1 else [result, failure]
    )
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not ready"}
