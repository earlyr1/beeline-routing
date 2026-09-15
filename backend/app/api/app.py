from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.deps import AppDeps, build_deps
from app.api.proposals import router as proposals_router
from app.api.routes import router
from app.settings import Settings


async def _validation_error(_, error: RequestValidationError) -> JSONResponse:
    parts = []
    for item in error.errors()[:3]:
        location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        message = str(item.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{location}: {message}" if location else message)
    return JSONResponse(status_code=422, content={"detail": "Некорректный запрос: " + "; ".join(parts)})


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
    return app
