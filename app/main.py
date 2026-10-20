import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config.settings import Settings
from app.db.mysql import MySQLDatabase
from app.observability.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, database: MySQLDatabase | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owned_database = database is None and config.database_configured
        application.state.database = database
        if owned_database:
            application.state.database = MySQLDatabase(config)
        try:
            yield
        finally:
            if owned_database:
                await run_in_threadpool(application.state.database.dispose)

    application = FastAPI(title="ERP SQL Agent", version="0.1.0", lifespan=lifespan)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready")
    async def ready() -> JSONResponse:
        db: Any = application.state.database
        if db is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "database_not_configured",
                },
            )
        try:
            await run_in_threadpool(db.check_connectivity)
        except Exception as exc:
            logger.warning("database_readiness_failed", extra={"error_type": type(exc).__name__})
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "database_unavailable_or_unsafe",
                },
            )
        return JSONResponse(content={"status": "ready"})

    return application
