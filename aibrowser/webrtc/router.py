"""FastAPI router exposing SmallWebRTC signaling endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from pipecat.transports.smallwebrtc.connection import IceServer

from .session_manager import WebRTCSessionManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webrtc-experimental", tags=["webrtc-experimental"])


class OfferRequest(BaseModel):
    """Incoming SDP offer from the frontend."""

    sdp: str
    type: Literal["offer", "answer"]
    pcId: Optional[str] = Field(default=None, alias="pc_id")
    restartPc: Optional[bool] = Field(default=None, alias="restart_pc")
    requestData: Optional[Dict[str, Any]] = Field(default=None, alias="request_data")


class IceCandidateModel(BaseModel):
    candidate: str
    sdpMid: str
    sdpMLineIndex: int


class IcePatchRequest(BaseModel):
    pcId: str = Field(alias="pcId")
    candidates: list[IceCandidateModel]


_default_ice_servers = [IceServer(urls="stun:stun.l.google.com:19302")]
manager = WebRTCSessionManager(ice_servers=_default_ice_servers)


@router.post("/offer")
async def start_webrtc_session(request: OfferRequest):
    """Accept a WebRTC offer and return the SDP answer."""
    try:
        payload = request.model_dump(by_alias=True)
        answer = await manager.handle_offer(payload)
        logger.info("WebRTC session created with pc_id: %s", answer.get("pc_id"))
        return answer
    except Exception as error:
        logger.error("Failed to process WebRTC offer: %s", error, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error


@router.post("/ice")
async def patch_webrtc_ice(request: IcePatchRequest):
    """Accept ICE candidates from the browser."""
    try:
        await manager.handle_ice_patch(
            {
                "pcId": request.pcId,
                "candidates": [
                    {
                        "candidate": c.candidate,
                        "sdpMid": c.sdpMid,
                        "sdpMLineIndex": c.sdpMLineIndex,
                    }
                    for c in request.candidates
                ],
            }
        )
        return {"status": "ok"}
    except Exception as error:
        logger.error("Failed to patch ICE candidates: %s", error, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error


@router.get("/events/{pc_id}")
async def stream_events(pc_id: str):
    """Stream voice events (user speech, agent responses) via Server-Sent Events."""
    
    async def event_generator():
        """Generate SSE events from the event queue."""
        logger.info("SSE connection requested for pc_id: %s", pc_id)
        event_queue = manager.get_event_queue(pc_id)
        
        if not event_queue:
            # Send error and close
            logger.warning("Event queue not found for pc_id: %s", pc_id)
            yield f"data: {json.dumps({'type': 'error', 'error': 'Connection not found'})}\n\n"
            return
        
        try:
            # Send ready event
            yield f"data: {json.dumps({'type': 'ready'})}\n\n"
            
            while True:
                try:
                    # Wait for events with timeout to allow checking connection state
                    event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                    
                    # Check for close event
                    if event.get("type") == "closed":
                        logger.info("Event stream closed for %s", pc_id)
                        break
                    
                    # Send event to frontend
                    yield f"data: {json.dumps(event)}\n\n"
                    
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
                    continue
                    
        except asyncio.CancelledError:
            logger.info("Event stream cancelled for %s", pc_id)
        except Exception as e:
            logger.error("Error in event stream for %s: %s", pc_id, e, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

