from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from backend.api.routes_backtest import router as backtest_router
from backend.api.investment_api import router as investment_router
from backend.api.routes_learning import router as learning_router
from backend.api.routes_market import router as market_router
from backend.api.routes_news import router as news_router
from backend.api.routes_paper_trades import router as paper_trades_router
from backend.api.websocket_handler import router as websocket_router
from backend.backtest.backtester import resume_saved_backtest_if_needed
from backend.config import get_settings
from backend.data.news_fetcher import NewsFetcher
from backend.db.postgres import init_postgres
from backend.engine.fundamental_engine import FundamentalEngine
from backend.engine.scheduler_runtime import start_embedded_schedulers, stop_embedded_schedulers
from backend.logging_utils import configure_logging


settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_postgres()
    FundamentalEngine().sync_from_config()
    if settings.finbert_preload_on_startup:
        NewsFetcher.preload_sentiment_pipeline(wait=False)
    start_embedded_schedulers()
    resume_saved_backtest_if_needed()
    yield
    stop_embedded_schedulers()


app = FastAPI(
    title="Trading Bot API",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_router)
app.include_router(investment_router)
app.include_router(paper_trades_router)
app.include_router(backtest_router)
app.include_router(news_router)
app.include_router(learning_router)
app.include_router(websocket_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "Trading Bot API",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
