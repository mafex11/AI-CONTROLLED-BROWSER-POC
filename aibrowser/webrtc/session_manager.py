"""Session manager that wires FastAPI signaling endpoints to Pipecat."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    ConnectionMode,
    IceCandidate,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
    SmallWebRTCPatchRequest,
)

from ..config import Config
from ..voice.agent_bridge import AgentBridge
from .browser_session import BrowserSessionPool
from .pipeline import WebRTCPipeline

logger = logging.getLogger(__name__)


class WebRTCSessionManager:
    """Creates WebRTC pipelines on-demand for each peer connection."""

    def __init__(self, *, ice_servers: Optional[list[IceServer]] = None) -> None:
        self._browser_pool = BrowserSessionPool()
        self._handler = SmallWebRTCRequestHandler(
            ice_servers=ice_servers,
            connection_mode=ConnectionMode.SINGLE,
        )
        self._pipelines: dict[str, WebRTCPipeline] = {}
        self._pipeline_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def handle_offer(self, payload: dict) -> dict:
        """Handle SDP offer (or renegotiation) from frontend."""
        request = SmallWebRTCRequest.from_dict(payload)

        async def _callback(connection: SmallWebRTCConnection) -> None:
            async with self._lock:
                await self._start_pipeline_if_needed(connection, payload.get("request_data"))

        return await self._handler.handle_web_request(request, _callback)

    async def handle_ice_patch(self, payload: dict) -> None:
        """Handle ICE candidate patches."""
        candidates = [
            IceCandidate(
                candidate=item["candidate"],
                sdp_mid=item["sdpMid"],
                sdp_mline_index=item["sdpMLineIndex"],
            )
            for item in payload.get("candidates", [])
        ]
        request = SmallWebRTCPatchRequest(
            pc_id=payload["pcId"],
            candidates=candidates,
        )
        await self._handler.handle_patch_request(request)

    async def shutdown(self) -> None:
        """Stop all active pipelines and Chromium."""
        async with self._lock:
            await asyncio.gather(*(pipeline.stop() for pipeline in self._pipelines.values()), return_exceptions=True)
            for task in self._pipeline_tasks.values():
                task.cancel()
            self._pipelines.clear()
            self._pipeline_tasks.clear()
        await self._browser_pool.cleanup()
        await self._handler.close()

    async def _start_pipeline_if_needed(self, connection: SmallWebRTCConnection, request_data: Optional[Dict[str, Any]]) -> None:
        if connection.pc_id in self._pipelines:
            logger.debug("Pipeline already running for %s", connection.pc_id)
            return

        integration = await self._browser_pool.ensure_ready()

        agent_bridge = AgentBridge(
            integration=integration,
        )

        pipeline = WebRTCPipeline(
            connection=connection,
            agent_bridge=agent_bridge,
            deepgram_language=Config.DEEPGRAM_LANGUAGE,
            elevenlabs_voice_id=Config.ELEVENLABS_VOICE_ID,
        )
        await pipeline.initialize()

        @connection.event_handler("closed")
        async def _on_closed(_: SmallWebRTCConnection) -> None:
            async with self._lock:
                await self._teardown_connection(connection.pc_id)

        task = asyncio.create_task(pipeline.run())
        self._pipelines[connection.pc_id] = pipeline
        self._pipeline_tasks[connection.pc_id] = task
        await connection.connect()

    async def _teardown_connection(self, pc_id: str) -> None:
        pipeline = self._pipelines.pop(pc_id, None)
        task = self._pipeline_tasks.pop(pc_id, None)

        if task:
            task.cancel()
        if pipeline:
            await pipeline.stop()

