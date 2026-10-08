import re
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import URL


@dataclass(frozen=True)
class TestDatabase:
    __test__ = False
    host: str
    port: int
    name: str
    user: str
    password: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> TestDatabase:
        prefix = "PAWSPOT_TEST_DB_"
        for field in ("HOST", "PORT", "NAME", "USER", "PASSWORD"):
            if prefix + field not in environ:
                raise ValueError(
                    f"Set {prefix}{field}; ordinary PAWSPOT_DB_* is forbidden"
                )
        name = environ[prefix + "NAME"]
        if re.fullmatch(r"pawspot_test(?:_[a-z0-9_]+)?", name) is None:
            raise ValueError(
                "Test database name must be pawspot_test or pawspot_test_<suffix>"
            )
        try:
            port = int(environ[prefix + "PORT"])
        except ValueError:
            raise ValueError("Invalid PAWSPOT_TEST_DB_PORT") from None
        if not 1 <= port <= 65535:
            raise ValueError("Invalid PAWSPOT_TEST_DB_PORT")
        if not environ[prefix + "HOST"] or not environ[prefix + "USER"]:
            raise ValueError("Test database host and user must be explicit")
        return cls(
            environ[prefix + "HOST"],
            port,
            name,
            environ[prefix + "USER"],
            environ[prefix + "PASSWORD"],
        )

    @property
    def url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.name,
            query={"connect_timeout": "3"},
        )

    def application_environment(self) -> dict[str, str]:
        return {
            "PAWSPOT_DB_HOST": self.host,
            "PAWSPOT_DB_PORT": str(self.port),
            "PAWSPOT_DB_NAME": self.name,
            "PAWSPOT_DB_USER": self.user,
            "PAWSPOT_DB_PASSWORD": self.password,
        }
