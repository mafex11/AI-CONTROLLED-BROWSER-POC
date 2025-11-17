"""Standalone FastAPI app that exposes the experimental WebRTC endpoints."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .router import router, manager as webrtc_manager
from ..screen_stream.router import router as screen_stream_router
from ..screen_stream import router as screen_stream_router_module

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")

app = FastAPI(title="AI Browser WebRTC Experimental API")
app.include_router(router)
app.include_router(screen_stream_router)

# Share the browser pool between voice and screen stream
# This ensures both use the same Chromium instance
screen_stream_router_module._browser_pool = webrtc_manager._browser_pool


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}

