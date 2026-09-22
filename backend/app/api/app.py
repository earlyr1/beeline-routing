from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.deps import AppDeps, build_deps
from app.api.geocoding import router as geocoding_router
from app.api.proposals import router as proposals_router
from app.api.routes import router
from app.domain.validation_text import validation_text
from app.settings import Settings
from app.state.repo import StateConflict, StateUnavailable


async def _validation_error(_, error: RequestValidationError) -> JSONResponse:
    detail = "Некорректный запрос: " + validation_text(error.errors(), skip=("body", "query"))
    return JSONResponse(status_code=422, content={"detail": detail})


async def _state_conflict(_, error: StateConflict) -> JSONResponse:
    """День изменили мимо этого процесса: сохранять поверх нельзя, диспетчеру нужно обновить страницу."""
    return JSONResponse(status_code=409, content={"detail": str(error)})


async def _state_unavailable(_, error: StateUnavailable) -> JSONResponse:
    """База отвалилась на ходу: чтения идут из памяти процесса и работают, а запись честно отказывает."""
    return JSONResponse(status_code=503, content={"detail": str(error)})


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.deps.close()


def create_app(deps: AppDeps | None = None) -> FastAPI:
    app = FastAPI(
        title="Планировщик выездных инженеров",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.deps = deps or build_deps(Settings.from_env())
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StateConflict, _state_conflict)
    app.add_exception_handler(StateUnavailable, _state_unavailable)
    app.include_router(router)
    app.include_router(proposals_router)
    app.include_router(geocoding_router)
    return app
