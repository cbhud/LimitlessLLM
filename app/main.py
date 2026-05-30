"""FastAPI application entry point.

Startup:
  1. Load .env
  2. Parse config.yaml (with env-var interpolation)
  3. Create a shared httpx.AsyncClient (connection pooling, HTTP/2)
  4. Initialise provider adapters for platforms that have keys
  5. Mount the /v1 proxy routes

Shutdown:
  Close the httpx client.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import load_config
from app.providers import init_providers
from app.routes.proxy import router as proxy_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup / shutdown lifecycle."""
    # Load environment variables from .env (before config parsing)
    load_dotenv()

    # Parse and validate config
    config = load_config()

    logging.basicConfig(
        level=getattr(logging, config.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    log = logging.getLogger("startup")

    # Shared httpx client with connection pooling + HTTP/2
    client = httpx.AsyncClient(
        http2=True,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        timeout=httpx.Timeout(30.0, connect=10.0),
    )

    # Register provider adapters
    init_providers(config, client)

    active = list(config.providers.keys())
    chain_len = sum(1 for e in config.fallback_chain if e.enabled)

    log.info("LimitlessLLM Proxy started")
    log.info("Active providers: %s", ", ".join(active) if active else "(none)")
    log.info("Fallback chain:   %d models", chain_len)
    log.info("Listening on      %s:%d", config.server.host, config.server.port)

    yield

    # Shutdown
    await client.aclose()
    log.info("Shutdown complete")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="LimitlessLLM",
    description="High-performance LLM proxy aggregating free-tier providers",
    version="1.0.0",
    lifespan=lifespan,
    # Disable the auto-generated docs routes in production if you like;
    # keep them enabled for dev convenience.
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(proxy_router)


@app.get("/health")
async def health():
    """Simple liveness probe."""
    return {"status": "ok"}


# ── Direct run ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    load_dotenv()
    cfg = load_config()
    uvicorn.run(
        "app.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
    )
