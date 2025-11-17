"""Entry point for the experimental WebRTC FastAPI app."""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("WEBRTC_API_PORT", "8100"))
    uvicorn.run("aibrowser.webrtc.app:app", host="0.0.0.0", port=port, reload=False)

