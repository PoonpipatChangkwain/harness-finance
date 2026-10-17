from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_exception_handlers
from app.api.routes.approvals import router as approvals_router
from app.api.routes.backtest import router as backtest_router
from app.api.routes.config import router as config_router
from app.api.routes.dev import router as dev_router
from app.api.routes.health import router as health_router
from app.api.routes.market import router as market_router
from app.api.routes.memory import router as memory_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.runs import router as runs_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.settings import router as settings_router
from app.api.routes.skills import router as skills_router
from app.api.routes.symbols import router as symbols_router
from app.api.routes.trade import router as trade_router
from app.api.routes.workflows import router as workflows_router
from app.harness.runtime.services import get_services
from app.harness.storage.migrate import upgrade_head_async
from app.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await upgrade_head_async()
    services = get_services()
    skipped = await services.load_overrides()
    if skipped:
        logger.warning("ignored invalid settings sections: %s", ", ".join(skipped))
    interrupted = await services.runs.recover_interrupted()
    if interrupted:
        logger.warning("marked %d runs left running by a previous process as interrupted", len(interrupted))
    if services.settings.portfolio_weekly_enabled:
        services.weekly.start()  # catches up a missed Friday slot, then sleeps until the next one
    yield
    await services.weekly.stop()
    await services.runs.shutdown()
    await services.checkpoints.close_all()


app = FastAPI(title="Finance Harness API", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_exception_handlers(app)

api_router_prefix = "/api"
app.include_router(health_router, prefix=api_router_prefix)
app.include_router(symbols_router, prefix=api_router_prefix)
app.include_router(market_router, prefix=api_router_prefix)
app.include_router(config_router, prefix=api_router_prefix)
app.include_router(runs_router, prefix=api_router_prefix)
app.include_router(dev_router, prefix=api_router_prefix)
app.include_router(skills_router, prefix=api_router_prefix)
app.include_router(sessions_router, prefix=api_router_prefix)
app.include_router(workflows_router, prefix=api_router_prefix)
app.include_router(approvals_router, prefix=api_router_prefix)
app.include_router(trade_router, prefix=api_router_prefix)
app.include_router(portfolio_router, prefix=api_router_prefix)
app.include_router(settings_router, prefix=api_router_prefix)
app.include_router(backtest_router, prefix=api_router_prefix)
app.include_router(memory_router, prefix=api_router_prefix)
