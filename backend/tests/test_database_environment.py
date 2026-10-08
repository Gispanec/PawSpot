import pytest

from database_environment import TestDatabase


def test_ordinary_database_configuration_is_never_used() -> None:
    with pytest.raises(ValueError, match="PAWSPOT_TEST_DB_HOST"):
        TestDatabase.from_environment({"PAWSPOT_DB_NAME": "pawspot"})


@pytest.mark.parametrize("name", ["pawspot", "postgres", "pawspot_test;DROP", ""])
def test_rejects_unsafe_database_name(name: str) -> None:
    with pytest.raises(ValueError, match="Test database name"):
        TestDatabase.from_environment(make_environment(name))


def test_explicit_test_configuration_does_not_inherit_pilot_values() -> None:
    environment = make_environment("pawspot_test_local")
    environment.update(PAWSPOT_DB_NAME="pawspot", PAWSPOT_DB_PASSWORD="pilot-secret")
    configuration = TestDatabase.from_environment(environment)
    assert configuration.url.database == "pawspot_test_local"
    assert configuration.url.password == ""
    assert (
        configuration.application_environment()["PAWSPOT_DB_NAME"]
        == "pawspot_test_local"
    )


def make_environment(name: str) -> dict[str, str]:
    return {
        "PAWSPOT_TEST_DB_HOST": "127.0.0.1",
        "PAWSPOT_TEST_DB_PORT": "55432",
        "PAWSPOT_TEST_DB_NAME": name,
        "PAWSPOT_TEST_DB_USER": "pawspot_test",
        "PAWSPOT_TEST_DB_PASSWORD": "",
    }
