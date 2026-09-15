from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.deps import AppDeps, build_deps
from app.api.geocoding import router as geocoding_router
from app.api.proposals import router as proposals_router
from app.api.routes import router
from app.domain.validation_text import validation_text
from app.settings import Settings


async def _validation_error(_, error: RequestValidationError) -> JSONResponse:
    detail = "Некорректный запрос: " + validation_text(error.errors(), skip=("body", "query"))
    return JSONResponse(status_code=422, content={"detail": detail})


def create_app(deps: AppDeps | None = None) -> FastAPI:
    app = FastAPI(
        title="Планировщик выездных инженеров",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.deps = deps or build_deps(Settings.from_env())
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.include_router(router)
    app.include_router(proposals_router)
    app.include_router(geocoding_router)
    return app
