"""Standalone FastAPI app that exposes the experimental WebRTC endpoints."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .router import router

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")

app = FastAPI(title="AI Browser WebRTC Experimental API")
app.include_router(router)


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}

