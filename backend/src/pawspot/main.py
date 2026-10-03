from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from pawspot.api.auth import internal_router, public_router
from pawspot.config import CorsSettings
from pawspot.db import get_engine, get_postgis_version

app = FastAPI(title="PawSpot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CorsSettings().allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(public_router)
app.include_router(internal_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(engine: Annotated[Engine, Depends(get_engine)]) -> dict[str, str]:
    try:
        postgis_version = get_postgis_version(engine)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {"status": "ready", "postgis_version": postgis_version}
