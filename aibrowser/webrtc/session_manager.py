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
        # Event queues for each connection to stream events to frontend
        self._event_queues: dict[str, asyncio.Queue] = {}

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

    def get_event_queue(self, pc_id: str) -> Optional[asyncio.Queue]:
        """Get event queue for a specific peer connection."""
        return self._event_queues.get(pc_id)

    async def _start_pipeline_if_needed(self, connection: SmallWebRTCConnection, request_data: Optional[Dict[str, Any]]) -> None:
        import time
        start_time = time.time()
        
        if connection.pc_id in self._pipelines:
            logger.debug("Pipeline already running for %s", connection.pc_id)
            return

        logger.info("Starting pipeline initialization for %s", connection.pc_id)
        browser_start = time.time()
        integration = await self._browser_pool.ensure_ready()
        logger.info("Browser ready in %.2fs", time.time() - browser_start)

        # Create event queue for this connection
        event_queue = asyncio.Queue()
        self._event_queues[connection.pc_id] = event_queue
        logger.info("Created event queue for pc_id: %s", connection.pc_id)

        # Create callbacks that push events to the queue
        async def on_user_speech(text: str) -> None:
            try:
                logger.info("on_user_speech callback called with text: %s", text)
                await event_queue.put({"type": "user_speech", "text": text})
                logger.info("Queued user_speech event for %s", connection.pc_id)
            except Exception as e:
                logger.error("Error queueing user_speech event: %s", e, exc_info=True)

        async def on_agent_response(text: str) -> None:
            try:
                logger.info("on_agent_response callback called with text: %s", text)
                await event_queue.put({"type": "agent_response", "text": text})
                logger.info("Queued agent_response event for %s", connection.pc_id)
            except Exception as e:
                logger.error("Error queueing agent_response event: %s", e, exc_info=True)
        
        async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
            """Send step updates to frontend via SSE."""
            if phase == 'before':
                try:
                    logger.info("step_callback called for step %d", step)
                    await event_queue.put({
                        "type": "step",
                        "step": step,
                        "narration": narration or "",
                        "reasoning": reasoning or "",
                        "tool": tool or "",
                    })
                    logger.info("Queued step event for %s", connection.pc_id)
                except Exception as e:
                    logger.error("Error queueing step event: %s", e, exc_info=True)
        
        # Set step callback on integration
        integration.update_callbacks(step_callback=step_callback)

        agent_bridge = AgentBridge(
            integration=integration,
            on_user_speech=on_user_speech,
            on_agent_response=on_agent_response,
        )

        pipeline_create_start = time.time()
        pipeline = WebRTCPipeline(
            connection=connection,
            agent_bridge=agent_bridge,
            deepgram_language=Config.DEEPGRAM_LANGUAGE,
            elevenlabs_voice_id=Config.ELEVENLABS_VOICE_ID,
        )
        await pipeline.initialize()
        logger.info("Pipeline created and initialized in %.2fs", time.time() - pipeline_create_start)

        @connection.event_handler("closed")
        async def _on_closed(_: SmallWebRTCConnection) -> None:
            async with self._lock:
                await self._teardown_connection(connection.pc_id)

        task = asyncio.create_task(pipeline.run())
        self._pipelines[connection.pc_id] = pipeline
        self._pipeline_tasks[connection.pc_id] = task
        
        connect_start = time.time()
        await connection.connect()
        logger.info("WebRTC connection established in %.2fs", time.time() - connect_start)
        
        total_elapsed = time.time() - start_time
        logger.info("Total pipeline startup completed in %.2fs", total_elapsed)

    async def _teardown_connection(self, pc_id: str) -> None:
        pipeline = self._pipelines.pop(pc_id, None)
        task = self._pipeline_tasks.pop(pc_id, None)
        event_queue = self._event_queues.pop(pc_id, None)

        if task:
            task.cancel()
        if pipeline:
            await pipeline.stop()
        
        # Send close event to any listening clients
        if event_queue:
            try:
                await event_queue.put({"type": "closed"})
            except Exception:
                pass

