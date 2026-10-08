from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from pawspot.api.auth import internal_router, public_router
from pawspot.api.encounters import internal_router as internal_encounter_router
from pawspot.api.encounters import public_router as public_encounter_router
from pawspot.api.read import router as read_router
from pawspot.api.social import router as social_router
from pawspot.api.today import router as today_router
from pawspot.config import CorsSettings
from pawspot.db import check_database_readiness, get_engine

app = FastAPI(title="PawSpot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CorsSettings().allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(public_router)
app.include_router(internal_router)
app.include_router(public_encounter_router)
app.include_router(internal_encounter_router)
app.include_router(read_router)
app.include_router(social_router)
app.include_router(today_router)


@app.exception_handler(RequestValidationError)
def sanitized_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Invalid request"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(engine: Annotated[Engine, Depends(get_engine)]) -> dict[str, str]:
    try:
        postgis_version = check_database_readiness(engine)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    if postgis_version is None:
        raise HTTPException(status_code=503, detail="Database is not ready")
    return {"status": "ready", "postgis_version": postgis_version}
