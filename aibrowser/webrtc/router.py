"""FastAPI router exposing SmallWebRTC signaling endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, status
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

