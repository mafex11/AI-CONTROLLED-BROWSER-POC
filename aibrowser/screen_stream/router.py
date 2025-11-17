"""FastAPI router for screen stream signaling."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .webrtc_server import ScreenStreamManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/screen-stream", tags=["screen-stream"])

# Will be set by the app to share browser pool with voice session
_browser_pool = None


class OfferRequest(BaseModel):
    """WebRTC offer from the client."""
    sdp: str
    type: str
    sessionId: Optional[str] = Field(default=None, alias="session_id")


class AnswerResponse(BaseModel):
    """WebRTC answer to the client."""
    sdp: str
    type: str
    session_id: str


class IceCandidateModel(BaseModel):
    """ICE candidate."""
    candidate: str
    sdpMid: Optional[str] = None
    sdpMLineIndex: Optional[int] = None


class IceCandidateRequest(BaseModel):
    """ICE candidate from the client."""
    sessionId: str = Field(alias="session_id")
    candidate: IceCandidateModel


# Get Coturn configuration from environment
COTURN_HOST = os.getenv("COTURN_HOST")
COTURN_PORT = os.getenv("COTURN_PORT")
COTURN_USERNAME = os.getenv("COTURN_USERNAME")
COTURN_PASSWORD = os.getenv("COTURN_PASSWORD")

# Configure ICE servers
ice_servers = [
    {"urls": "stun:stun.l.google.com:19302"},
]

# Only add TURN server if configuration is provided
if COTURN_HOST and COTURN_PORT and COTURN_USERNAME and COTURN_PASSWORD:
    ice_servers.append({
        "urls": f"turn:{COTURN_HOST}:{COTURN_PORT}",
        "username": COTURN_USERNAME,
        "credential": COTURN_PASSWORD,
    })
else:
    logger.info("TURN server not configured, using STUN only (may not work behind strict NAT/firewalls)")

# Global screen stream manager
manager = ScreenStreamManager(ice_servers=ice_servers)


@router.post("/offer", response_model=AnswerResponse)
async def handle_offer(request: OfferRequest):
    """
    Handle WebRTC offer and return answer.
    
    This creates a new screen stream session and starts capturing
    the browser screen via CDP.
    """
    try:
        # Generate session ID if not provided
        session_id = request.sessionId or str(uuid.uuid4())
        
        logger.info("Received screen stream offer for session %s", session_id)
        
        # Get CDP WebSocket URL
        cdp_url = os.getenv("CDP_WEBSOCKET_URL")
        
        # If not set, try to get from shared browser pool
        if not cdp_url:
            if _browser_pool is None:
                raise RuntimeError("Browser pool not initialized. Please ensure browser is running.")
            
            # Check if _browser_pool is a CDPBrowserManager (from api_server)
            if hasattr(_browser_pool, "websocket_url"):
                # Direct CDPBrowserManager from api_server
                cdp_url = await _browser_pool.websocket_url()
            elif hasattr(_browser_pool, "ensure_ready"):
                # BrowserSessionPool from webrtc app
                integration = await _browser_pool.ensure_ready()
                if hasattr(_browser_pool, "_browser_manager") and _browser_pool._browser_manager:
                    cdp_url = await _browser_pool._browser_manager.websocket_url()
        
        if not cdp_url:
            raise RuntimeError("CDP WebSocket URL not available. Please ensure browser is running.")
        
        logger.info("Using CDP URL: %s", cdp_url)
        
        # Create session and get answer
        answer = await manager.create_session(
            session_id=session_id,
            cdp_url=cdp_url,
            offer_sdp=request.sdp,
            offer_type=request.type,
        )
        
        return AnswerResponse(
            sdp=answer["sdp"],
            type=answer["type"],
            session_id=session_id,
        )
        
    except Exception as error:
        logger.error("Failed to handle screen stream offer: %s", error, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error


@router.post("/ice")
async def handle_ice_candidate(request: IceCandidateRequest):
    """Handle ICE candidate from the client."""
    try:
        logger.debug("Received ICE candidate for session %s", request.sessionId)
        
        await manager.add_ice_candidate(
            session_id=request.sessionId,
            candidate={
                "candidate": request.candidate.candidate,
                "sdpMid": request.candidate.sdpMid,
                "sdpMLineIndex": request.candidate.sdpMLineIndex,
            },
        )
        
        return {"status": "ok"}
        
    except Exception as error:
        logger.error("Failed to handle ICE candidate: %s", error, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error


@router.get("/ice-servers")
async def get_ice_servers():
    """Return ICE server configuration for the client."""
    return {"iceServers": ice_servers}


@router.delete("/session/{session_id}")
async def close_session(session_id: str):
    """Close a screen stream session."""
    try:
        await manager.close_session(session_id)
        return {"status": "ok"}
    except Exception as error:
        logger.error("Failed to close session: %s", error, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

